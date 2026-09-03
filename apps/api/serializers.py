from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.accounts.permissions import has_permission
from apps.commerce.models import Purchase, PurchaseLine, Sale, SaleLine
from apps.commerce.services import create_purchase, create_sale
from apps.expenses.models import Expense, ExpenseCategory
from apps.inventory.models import (
    Brand,
    Category,
    Client,
    Product,
    ProductPackaging,
    StockMovement,
    Supplier,
    Unit,
)
from apps.inventory.services import record_stock_movement
from apps.printing.models import PrinterProfile, PrintProfile, UserPrinterPreference

from .exceptions import BusinessAPIException


class NamedReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ('id', 'name')


class CategorySerializer(NamedReferenceSerializer):
    class Meta(NamedReferenceSerializer.Meta):
        model = Category


class BrandSerializer(NamedReferenceSerializer):
    class Meta(NamedReferenceSerializer.Meta):
        model = Brand


class UnitSerializer(NamedReferenceSerializer):
    class Meta(NamedReferenceSerializer.Meta):
        model = Unit


class ProductPackagingSerializer(serializers.ModelSerializer):
    minimum_sale_price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = ProductPackaging
        fields = (
            'id', 'product', 'name', 'conversion_factor', 'default_sale_price',
            'minimum_sale_price', 'barcode', 'is_active',
        )

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request and not has_permission(request.user, 'inventory.change_product'):
            fields.pop('minimum_sale_price', None)
        return fields

    def validate(self, attrs):
        product = attrs.get('product') or getattr(self.instance, 'product', None)
        factor = attrs.get('conversion_factor', getattr(self.instance, 'conversion_factor', None))
        price = attrs.get('default_sale_price', getattr(self.instance, 'default_sale_price', None))
        if product and factor and price is not None and price < product.purchase_price * factor:
            raise serializers.ValidationError({
                'default_sale_price': _("Le prix du conditionnement ne peut pas être inférieur à son coût d'achat."),
            })
        return attrs


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    stock_status = serializers.SerializerMethodField()
    conditionnements = ProductPackagingSerializer(source='packagings', many=True, read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'reference', 'barcode', 'name', 'category', 'category_name',
            'brand', 'brand_name', 'unit', 'unit_name', 'purchase_price',
            'sale_price', 'quantity', 'minimum_stock', 'description', 'photo',
            'qr_code', 'barcode_image', 'stock_status', 'conditionnements', 'created_at',
        )
        read_only_fields = ('reference', 'barcode', 'qr_code', 'barcode_image', 'created_at')

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request and not has_permission(request.user, 'inventory.change_product'):
            fields.pop('purchase_price', None)
        return fields

    def get_stock_status(self, product) -> str:
        if product.quantity == 0:
            return 'out_of_stock'
        if product.quantity <= product.minimum_stock:
            return 'critical'
        if product.quantity <= product.minimum_stock + 5:
            return 'low'
        return 'normal'

    @transaction.atomic
    def create(self, validated_data):
        initial_quantity = validated_data.pop('quantity', 0)
        product = Product.objects.create(quantity=0, **validated_data)
        if initial_quantity:
            record_stock_movement(
                product=product,
                movement_type=StockMovement.ENTRY,
                quantity=initial_quantity,
                reason=_('Stock initial du produit'),
                user=self.context['request'].user,
                source_type=StockMovement.SOURCE_PRODUCT,
                source_reference=product.reference,
            )
            product.refresh_from_db()
        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        target_quantity = validated_data.pop('quantity', instance.quantity)
        locked = Product.objects.select_for_update().get(pk=instance.pk)
        for field, value in validated_data.items():
            setattr(locked, field, value)
        locked.save()
        if target_quantity != locked.quantity:
            record_stock_movement(
                product=locked,
                movement_type=StockMovement.ADJUSTMENT,
                quantity=target_quantity,
                reason=_('Modification du stock depuis API'),
                user=self.context['request'].user,
                source_type=StockMovement.SOURCE_PRODUCT,
                source_reference=locked.reference,
            )
            locked.refresh_from_db()
        return locked


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = '__all__'
        read_only_fields = ('created_at',)


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'
        read_only_fields = ('created_at',)


class ProductSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ('id', 'reference', 'barcode', 'name', 'sale_price')


class SaleLineReadSerializer(serializers.ModelSerializer):
    product = ProductSummarySerializer(read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = SaleLine
        fields = (
            'id', 'product', 'packaging', 'packaging_name', 'packaging_factor',
            'packaging_quantity', 'quantity', 'unit_price', 'unit_cost', 'line_total',
        )


class SaleLineWriteSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False)
    product_id = serializers.PrimaryKeyRelatedField(
        source='product', queryset=Product.objects.all(), required=False, write_only=True,
    )
    packaging_id = serializers.PrimaryKeyRelatedField(
        source='packaging', queryset=ProductPackaging.objects.all(), required=False, allow_null=True,
    )
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)

    def to_internal_value(self, data):
        supplied_product = data.get('product')
        supplied_product_id = data.get('product_id')
        if supplied_product is not None and supplied_product_id is not None:
            if str(supplied_product) != str(supplied_product_id):
                raise serializers.ValidationError({'product_id': _('Les identifiants produit sont incohérents.')})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if 'product' not in attrs:
            raise serializers.ValidationError({'product_id': _('Le produit est obligatoire.')})
        return attrs


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineReadSerializer(many=True, read_only=True)
    items = SaleLineWriteSerializer(many=True, write_only=True)
    pay_full = serializers.BooleanField(write_only=True, required=False, default=False)
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = Sale
        fields = (
            'id', 'invoice_number', 'ticket_number', 'client', 'total', 'discount',
            'tax_rate', 'payment_type', 'payment_tracking_initialized', 'created_at',
            'amount_paid', 'balance_due', 'payment_status', 'lines', 'items', 'pay_full',
            'created_by_name',
        )
        read_only_fields = (
            'invoice_number', 'ticket_number', 'total', 'payment_tracking_initialized', 'created_at',
        )

    def create(self, validated_data):
        items = validated_data.pop('items')
        pay_full = validated_data.pop('pay_full', False)
        try:
            return create_sale(lines=items, user=self.context['request'].user, pay_full=pay_full, **validated_data)
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if not isinstance(exc, DjangoValidationError):
                raise
            details = exc.message_dict if hasattr(exc, 'message_dict') else {'non_field_errors': exc.messages}
            flattened = ' '.join(str(message) for messages in details.values() for message in messages)
            error_codes = {
                error.code
                for errors in getattr(exc, 'error_dict', {}).values()
                for error in errors
            }
            if 'insufficient_stock' in error_codes:
                code = 'INSUFFICIENT_STOCK'
            elif 'sale_price_below_cost' in error_codes:
                code = 'SALE_PRICE_BELOW_COST'
            else:
                code = 'SALE_VALIDATION_ERROR'
            raise BusinessAPIException(code, flattened, details) from exc


class PurchaseLineReadSerializer(serializers.ModelSerializer):
    product = ProductSummarySerializer(read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseLine
        fields = ('id', 'product', 'quantity', 'purchase_price', 'line_total')


class PurchaseLineWriteSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)


class PurchaseSerializer(serializers.ModelSerializer):
    lines = PurchaseLineReadSerializer(many=True, read_only=True)
    items = PurchaseLineWriteSerializer(many=True, write_only=True)
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)

    class Meta:
        model = Purchase
        fields = (
            'id', 'reference', 'supplier', 'total', 'tax_rate', 'payment_tracking_initialized',
            'created_at', 'amount_paid', 'balance_due', 'payment_status', 'lines', 'items',
        )
        read_only_fields = ('total', 'payment_tracking_initialized', 'created_at')

    def create(self, validated_data):
        items = validated_data.pop('items')
        try:
            return create_purchase(lines=items, user=self.context['request'].user, **validated_data)
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if not isinstance(exc, DjangoValidationError):
                raise
            details = exc.message_dict if hasattr(exc, 'message_dict') else {'non_field_errors': exc.messages}
            message = ' '.join(str(item) for messages in details.values() for item in messages)
            raise BusinessAPIException('PURCHASE_VALIDATION_ERROR', message, details) from exc


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = StockMovement
        fields = (
            'id', 'product', 'product_name', 'movement_type', 'quantity', 'reason',
            'applied_delta', 'balance_before', 'balance_after', 'source_type',
            'source_reference', 'created_by_name', 'created_at',
        )
        read_only_fields = fields


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ('id', 'name', 'is_active')


class ExpenseSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = Expense
        fields = (
            'id', 'number', 'date', 'category', 'description', 'amount', 'payment_method',
            'supplier', 'receipt', 'created_by', 'created_by_name', 'observation',
            'created_at', 'updated_at',
        )
        read_only_fields = ('number', 'created_by', 'created_by_name', 'created_at', 'updated_at')

    def create(self, validated_data):
        return Expense.objects.create(created_by=self.context['request'].user, **validated_data)


class PrinterProfileSerializer(serializers.ModelSerializer):
    connection_mode_display = serializers.CharField(source='get_connection_mode_display', read_only=True)
    protocol_display = serializers.CharField(source='get_protocol_display', read_only=True)

    class Meta:
        model = PrinterProfile
        fields = (
            'id', 'name', 'description', 'printer_type', 'manufacturer', 'model_name',
            'connection_mode', 'connection_mode_display', 'local_identifier', 'ip_address',
            'network_port', 'paper_width', 'protocol', 'protocol_display',
            'characters_per_line', 'encoding', 'auto_print', 'is_default', 'is_active',
            'created_at', 'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at')

    def validate(self, attrs):
        connection = attrs.get('connection_mode', getattr(self.instance, 'connection_mode', None))
        ip_address = attrs.get('ip_address', getattr(self.instance, 'ip_address', None))
        network_port = attrs.get('network_port', getattr(self.instance, 'network_port', None))
        width = attrs.get('paper_width', getattr(self.instance, 'paper_width', 80))
        characters = attrs.get('characters_per_line', getattr(self.instance, 'characters_per_line', 48))
        if connection == PrinterProfile.NETWORK and (not ip_address or not network_port):
            raise serializers.ValidationError({'ip_address': _('Une adresse IP et un port sont requis pour une imprimante réseau.')})
        if width == 58 and characters > 42:
            raise serializers.ValidationError({'characters_per_line': _('Une imprimante 58 mm ne peut pas dépasser 42 caractères par ligne.')})
        return attrs


class PrintProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintProfile
        fields = '__all__'


class UserPrinterPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPrinterPreference
        fields = ('user', 'printer', 'updated_at')
        read_only_fields = ('user', 'updated_at')
