from decimal import Decimal

from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from apps.inventory.location import validate_location
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import has_permission
from apps.core.models import CompanySettings
from apps.commerce.models import Payment, Purchase, PurchaseLine, Sale, SaleLine
from apps.commerce.services import create_purchase, create_sale, update_sale
from apps.commerce.utils import legacy_snapshot_warning
from apps.expenses.models import Expense, ExpenseCategory
from apps.inventory.models import (
    Brand,
    Category,
    Client,
    LoadingOrder,
    LoadingOrderLine,
    OperatorStock,
    OperatorStockMovement,
    Product,
    ProductPackaging,
    StockMovement,
    Supplier,
    Unit,
)
from apps.inventory.services import generate_loading_number, record_stock_movement
from apps.inventory.pricing import (
    loading_base_unit_allowed, loading_packaging_allowed, validate_product_prices,
)
from apps.printing.models import PrinterProfile, PrintProfile, UserPrinterPreference

from .exceptions import BusinessAPIException


class NamedReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ('id', 'name')


class CategorySerializer(NamedReferenceSerializer):
    class Meta(NamedReferenceSerializer.Meta):
        model = Category

    def validate_name(self, value):
        cleaned = ' '.join(str(value or '').split())
        if not cleaned:
            raise serializers.ValidationError(_('Le nom de la catégorie est obligatoire.'))
        duplicate = Category.objects.filter(name__iexact=cleaned)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError(_('Une catégorie portant ce nom existe déjà.'))
        return cleaned

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return super().create(validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError({'name': _('Cette catégorie existe déjà.')}) from exc

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                return super().update(instance, validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError({'name': _('Cette catégorie existe déjà.')}) from exc


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
            'id', 'product', 'name', 'kind', 'conversion_factor', 'default_sale_price',
            'reference_purchase_price', 'super_wholesale_price', 'wholesale_price',
            'retail_price', 'purchase_enabled', 'sale_enabled',
            'minimum_sale_price', 'barcode', 'is_active',
        )

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request and not has_permission(request.user, 'inventory.view_product_pricing'):
            fields.pop('default_sale_price', None)
            fields.pop('minimum_sale_price', None)
            fields.pop('reference_purchase_price', None)
            fields.pop('super_wholesale_price', None)
            fields.pop('wholesale_price', None)
            fields.pop('retail_price', None)
        return fields

    def validate(self, attrs):
        product = attrs.get('product') or getattr(self.instance, 'product', None)
        factor = attrs.get('conversion_factor', getattr(self.instance, 'conversion_factor', None))
        kind = attrs.get('kind', getattr(self.instance, 'kind', ProductPackaging.OTHER))
        reference_cost = attrs.get(
            'reference_purchase_price',
            getattr(self.instance, 'reference_purchase_price', None),
        )
        minimum_price = reference_cost
        if minimum_price is None and product and factor:
            minimum_price = product.purchase_price * factor
        errors = {}
        for field_name in (
            'default_sale_price', 'super_wholesale_price',
            'wholesale_price', 'retail_price',
        ):
            value = attrs.get(field_name, getattr(self.instance, field_name, None))
            if value is not None and minimum_price is not None and value < minimum_price:
                errors[field_name] = _(
                    "Le prix du conditionnement ne peut pas être inférieur à son coût d'achat."
                )
        tier_prices = [
            attrs.get(name, getattr(self.instance, name, None))
            for name in ('super_wholesale_price', 'wholesale_price', 'retail_price')
        ]
        if all(value is not None for value in tier_prices) and not (
            tier_prices[0] <= tier_prices[1] <= tier_prices[2]
        ):
            errors['retail_price'] = _(
                'Les tarifs carton doivent respecter l’ordre : Super Gros ≤ Gros ≤ Détail.'
            )
        if kind == ProductPackaging.CARTON and factor == 1:
            errors['conversion_factor'] = _('Un carton doit contenir plus d’une pièce.')
        if errors:
            raise serializers.ValidationError(errors)
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
            'brand', 'brand_name', 'unit', 'unit_name', 'unit_mode', 'purchase_price',
            'sale_price', 'super_wholesale_price', 'wholesale_price', 'retail_price',
            'quantity', 'minimum_stock', 'description', 'photo',
            'qr_code', 'barcode_image', 'stock_status', 'conditionnements', 'created_at',
        )
        read_only_fields = ('reference', 'barcode', 'qr_code', 'barcode_image', 'created_at')
        extra_kwargs = {'sale_price': {'required': False}}

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request and not has_permission(request.user, 'inventory.view_product_pricing'):
            fields.pop('purchase_price', None)
            fields.pop('sale_price', None)
            fields.pop('super_wholesale_price', None)
            fields.pop('wholesale_price', None)
            fields.pop('retail_price', None)
        return fields

    def validate(self, attrs):
        current = self.instance
        legacy_price = attrs.get(
            'sale_price',
            getattr(current, 'sale_price', None),
        )
        retail_price = attrs.get(
            'retail_price',
            legacy_price if current is None else getattr(current, 'retail_price', legacy_price),
        )
        if retail_price is None:
            raise serializers.ValidationError({'retail_price': _('Le prix Détail est obligatoire.')})
        attrs.setdefault('retail_price', retail_price)
        attrs.setdefault(
            'wholesale_price',
            (
                legacy_price if legacy_price is not None else retail_price
            ) if current is None else getattr(current, 'wholesale_price', retail_price),
        )
        attrs.setdefault(
            'super_wholesale_price',
            (
                legacy_price if legacy_price is not None else retail_price
            ) if current is None else getattr(current, 'super_wholesale_price', retail_price),
        )
        attrs['sale_price'] = attrs['retail_price']

        candidate = Product(
            purchase_price=attrs.get(
                'purchase_price', getattr(current, 'purchase_price', None)
            ),
            super_wholesale_price=attrs['super_wholesale_price'],
            wholesale_price=attrs['wholesale_price'],
            retail_price=attrs['retail_price'],
            sale_price=attrs['retail_price'],
        )
        try:
            validate_product_prices(candidate)
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if not isinstance(exc, DjangoValidationError):
                raise
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
            ) from exc
        return attrs

    def get_stock_status(self, product) -> str:
        quantity = self._effective_quantity(product)
        if quantity == 0:
            return 'out_of_stock'
        if quantity <= product.minimum_stock:
            return 'critical'
        if quantity <= product.minimum_stock + 5:
            return 'low'
        return 'normal'

    def _effective_quantity(self, product):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return product.quantity
        has_active_loading = getattr(request, '_has_active_operator_loading', None)
        if has_active_loading is None:
            has_active_loading = LoadingOrder.objects.filter(
                operator=user, status__in=(LoadingOrder.VALIDATED, LoadingOrder.IN_PROGRESS),
            ).exists()
            request._has_active_operator_loading = has_active_loading
        if not has_active_loading:
            return product.quantity
        cache = getattr(request, '_operator_stock_quantities', None)
        if cache is None:
            cache = dict(OperatorStock.objects.filter(operator=user).values_list('product_id', 'quantity'))
            request._operator_stock_quantities = cache
        return cache.get(product.pk, 0)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['quantity'] = self._effective_quantity(instance)
        return data

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
        locked = Product.objects.select_for_update().get(pk=instance.pk)
        target_quantity = validated_data.pop('quantity', locked.quantity)
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
    def validate(self, attrs):
        instance = self.instance
        latitude = attrs.get('latitude', getattr(instance, 'latitude', None))
        longitude = attrs.get('longitude', getattr(instance, 'longitude', None))
        if instance and (latitude, longitude) != (instance.latitude, instance.longitude):
            for field in ('location_accuracy', 'formatted_address', 'place_id'):
                attrs.setdefault(field, None)
        accuracy = attrs.get('location_accuracy', getattr(instance, 'location_accuracy', None))
        try:
            validate_location(latitude, longitude, accuracy)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs

    class Meta:
        model = Client
        fields = '__all__'
        read_only_fields = ('created_at',)


class CompanySettingsSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = CompanySettings
        fields = (
            'id', 'company_name', 'address', 'phone', 'email', 'rc_number',
            'tax_number', 'nis', 'article_number', 'tax_rate', 'logo_url',
            'updated_at',
        )
        read_only_fields = ('id', 'logo_url', 'updated_at')

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'
        read_only_fields = ('created_at',)


class ProductSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ('id', 'reference', 'barcode', 'name')


class SaleLineReadSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    packaging_id = serializers.IntegerField(read_only=True)
    entered_quantity = serializers.IntegerField(source='packaging_quantity', read_only=True)
    base_quantity = serializers.IntegerField(source='quantity', read_only=True)

    def get_product(self, obj) -> dict:
        if obj.sale.has_certified_snapshot:
            return {
                'id': obj.product_id,
                'reference': obj.product_reference_snapshot,
                'barcode': obj.product.barcode,
                'name': obj.product_name_snapshot,
            }
        return ProductSummarySerializer(obj.product, context=self.context).data

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if not request or not has_permission(request.user, 'inventory.view_product_pricing'):
            fields.pop('unit_cost', None)
        return fields

    class Meta:
        model = SaleLine
        fields = (
            'id', 'product', 'packaging', 'packaging_id', 'packaging_name',
            'packaging_factor', 'packaging_quantity', 'entered_quantity',
            'quantity', 'base_quantity', 'unit_price', 'unit_cost', 'line_total',
            'product_name_snapshot', 'product_reference_snapshot', 'unit_name_snapshot',
            'line_total_ht_snapshot',
        )


class SaleLineWriteSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False)
    product_id = serializers.PrimaryKeyRelatedField(
        source='product', queryset=Product.objects.all(), required=False, write_only=True,
    )
    packaging_id = serializers.PrimaryKeyRelatedField(
        source='packaging', queryset=ProductPackaging.objects.filter(
            is_active=True, sale_enabled=True,
        ), required=False, allow_null=True,
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
    amount_received = serializers.DecimalField(
        write_only=True, required=False, allow_null=True,
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
    )
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    client_details = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()
    tax_amount = serializers.SerializerMethodField()
    payment_type_display = serializers.CharField(source='get_payment_type_display', read_only=True)
    capabilities = serializers.SerializerMethodField()
    company_details = serializers.SerializerMethodField()
    historical_document = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            'id', 'invoice_number', 'ticket_number', 'client', 'total', 'discount',
            'tax_rate', 'payment_type', 'payment_tracking_initialized', 'created_at',
            'show_legal_information',
            'amount_paid', 'balance_due', 'payment_status', 'lines', 'items', 'pay_full', 'amount_received',
            'created_by_name',
            'loading_order',
            'client_details', 'payments', 'subtotal', 'tax_amount',
            'payment_type_display', 'capabilities', 'company_details',
            'issued_at', 'snapshot_version', 'snapshot_integrity_status',
            'currency_snapshot', 'subtotal_ht_snapshot', 'tax_amount_snapshot',
            'historical_document',
        )
        read_only_fields = (
            'invoice_number', 'ticket_number', 'total',
            'payment_tracking_initialized', 'created_at', 'loading_order',
            'issued_at', 'snapshot_version', 'snapshot_integrity_status',
            'currency_snapshot', 'subtotal_ht_snapshot', 'tax_amount_snapshot',
        )

    def get_client_details(self, obj) -> dict:
        client = obj.client
        if obj.has_certified_snapshot:
            customer_type = obj.client_customer_type_snapshot
            return {
                'id': obj.client_id,
                'name': obj.client_name_snapshot,
                'phone': obj.client_phone_snapshot,
                'address': obj.client_address_snapshot,
                'tax_number': obj.client_tax_number_snapshot,
                'nis': obj.client_nis_snapshot,
                'article_number': obj.client_article_number_snapshot,
                'trade_register_number': obj.client_trade_register_number_snapshot,
                'customer_type': customer_type,
                'customer_type_display': str(
                    dict(Client.CustomerType.choices).get(customer_type, '')
                ),
            }
        return {
            'id': client.pk,
            'name': client.name,
            'phone': client.phone,
            'address': client.address,
            'tax_number': client.tax_number,
            'nis': client.nis,
            'article_number': client.article_number,
            'trade_register_number': client.trade_register_number,
            'customer_type': client.customer_type,
            'customer_type_display': client.get_customer_type_display(),
        }

    def get_company_details(self, obj) -> dict:
        if obj.has_certified_snapshot:
            company = CompanySettings(
                company_name=obj.company_name_snapshot,
                address=obj.company_address_snapshot,
                phone=obj.company_phone_snapshot,
                email=obj.company_email_snapshot,
                rc_number=obj.company_rc_number_snapshot,
                tax_number=obj.company_tax_number_snapshot,
                nis=obj.company_nis_snapshot,
                article_number=obj.company_article_number_snapshot,
                tax_rate=obj.tax_rate,
            )
            if obj.company_logo_snapshot:
                company.logo.name = obj.company_logo_snapshot
            return CompanySettingsSerializer(company, context=self.context).data
        request = self.context.get('request')
        company = getattr(request, '_company_settings', None) if request else None
        if company is None:
            company = CompanySettings.objects.order_by('pk').first() or CompanySettings()
            if request:
                request._company_settings = company
        return CompanySettingsSerializer(company, context=self.context).data

    def get_payments(self, obj) -> list:
        return [
            {
                'id': payment.pk,
                'reference': payment.reference,
                'external_reference': payment.external_reference,
                'notes': payment.notes,
                'amount': str(payment.amount),
                'payment_type': payment.payment_type,
                'payment_type_display': payment.get_payment_type_display(),
                'created_at': payment.created_at,
                'is_cancelled': payment.is_cancelled,
                'cancelled_at': payment.cancelled_at,
                'cancellation_reason': payment.cancellation_reason,
            }
            for payment in obj.payments.all()
        ]

    def get_subtotal(self, obj) -> str:
        if obj.has_certified_snapshot and obj.subtotal_ht_snapshot is not None:
            return f'{obj.subtotal_ht_snapshot:.2f}'
        value = sum((line.line_total() for line in obj.lines.all()), Decimal('0.00'))
        return f'{value:.2f}'

    def get_tax_amount(self, obj) -> str:
        if obj.has_certified_snapshot and obj.tax_amount_snapshot is not None:
            return f'{obj.tax_amount_snapshot:.2f}'
        subtotal = sum((line.line_total() for line in obj.lines.all()), Decimal('0.00'))
        value = subtotal * Decimal(str(obj.tax_rate or 0)) / Decimal('100')
        return f'{value:.2f}'

    def get_historical_document(self, obj) -> dict:
        return {
            'version': obj.snapshot_version,
            'integrity_status': obj.snapshot_integrity_status,
            'is_certified': obj.has_certified_snapshot,
            'warning': '' if obj.has_certified_snapshot else legacy_snapshot_warning(),
            'issued_at': obj.issued_at.isoformat() if obj.issued_at else None,
            'currency': obj.currency_snapshot,
            'company': self.get_company_details(obj),
            'client': self.get_client_details(obj),
            'totals': {
                'subtotal_ht': self.get_subtotal(obj),
                'tax_rate': f'{obj.tax_rate:.2f}',
                'tax_amount': self.get_tax_amount(obj),
                'discount': f'{obj.discount:.2f}',
                'total_ttc': f'{obj.total:.2f}',
            },
        }

    def get_capabilities(self, obj) -> dict:
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return {}
        return {
            'can_view': has_permission(user, 'commerce.view_sale'),
            'can_update': has_permission(user, 'commerce.change_sale'),
            'can_delete': has_permission(user, 'commerce.delete_sale'),
            'can_view_invoice': has_permission(user, 'accounts.view_invoices'),
            'can_view_pdf': has_permission(user, 'accounts.download_invoice_pdf'),
            'can_print': has_permission(user, 'accounts.print_invoice'),
            'can_add_payment': has_permission(user, 'commerce.change_sale'),
            'can_cancel': False,
        }
    def create(self, validated_data):
        items = validated_data.pop('items')
        pay_full = validated_data.pop('pay_full', False)
        amount_received = validated_data.pop('amount_received', None)
        if pay_full and amount_received is not None:
            raise serializers.ValidationError({'amount_received': _('Choisissez paiement total ou partiel, pas les deux.')})
        try:
            return create_sale(
                lines=items, user=self.context['request'].user, pay_full=pay_full,
                initial_payment_amount=amount_received, **validated_data,
            )
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

    def update(self, instance, validated_data):
        items = validated_data.pop('items', None)
        pay_full = validated_data.pop('pay_full', False)
        amount_received = validated_data.pop('amount_received', None)
        if pay_full and amount_received is not None:
            raise serializers.ValidationError({'amount_received': _('Choisissez paiement total ou partiel, pas les deux.')})
        try:
            return update_sale(
                sale=instance,
                lines=items,
                user=self.context['request'].user,
                pay_full=pay_full,
                initial_payment_amount=amount_received,
                **validated_data,
            )
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
    packaging_id = serializers.IntegerField(read_only=True)
    entered_quantity = serializers.IntegerField(read_only=True)
    base_quantity = serializers.IntegerField(source='quantity', read_only=True)

    class Meta:
        model = PurchaseLine
        fields = (
            'id', 'product', 'quantity', 'base_quantity', 'packaging_id',
            'packaging_name', 'packaging_factor', 'packaging_quantity',
            'entered_quantity', 'purchase_price', 'line_total',
        )


class PurchaseLineWriteSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    packaging_id = serializers.PrimaryKeyRelatedField(
        source='packaging',
        queryset=ProductPackaging.objects.filter(is_active=True, purchase_enabled=True),
        required=False, allow_null=True,
    )
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


class PaymentSerializer(serializers.ModelSerializer):
    is_cancelled = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    cancelled_by_name = serializers.CharField(source='cancelled_by.username', read_only=True)

    class Meta:
        model = Payment
        fields = (
            'id', 'reference', 'external_reference', 'notes', 'sale', 'purchase',
            'amount', 'payment_type', 'created_by', 'created_by_name', 'created_at',
            'is_cancelled', 'cancelled_at', 'cancelled_by', 'cancelled_by_name',
            'cancellation_reason',
        )
        read_only_fields = (
            'reference', 'created_by', 'created_by_name', 'is_cancelled',
            'cancelled_at', 'cancelled_by', 'cancelled_by_name', 'cancellation_reason',
        )

    def validate(self, attrs):
        if bool(attrs.get('sale')) == bool(attrs.get('purchase')):
            raise serializers.ValidationError(_('Indiquez exactement une vente ou un achat.'))
        request = self.context['request']
        permission = 'commerce.add_customerpayment' if attrs.get('sale') else 'commerce.change_purchase'
        if not has_permission(request.user, permission):
            raise PermissionDenied(_('Vous ne disposez pas de la permission requise.'))
        sale = attrs.get('sale')
        if sale and LoadingOrder.objects.filter(operator=request.user).exists() and not has_permission(
            request.user, 'inventory.view_all_loadingorders'
        ) and sale.created_by_id != request.user.pk:
            raise PermissionDenied(_('Cette vente appartient à un autre opérateur.'))
        return attrs

    def create(self, validated_data):
        try:
            return Payment.objects.create(created_by=self.context['request'].user, **validated_data)
        except DjangoValidationError as exc:
            details = exc.message_dict if hasattr(exc, 'message_dict') else {'non_field_errors': exc.messages}
            raise BusinessAPIException('PAYMENT_VALIDATION_ERROR', ' '.join(str(v) for values in details.values() for v in values), details) from exc


class ReceivableSerializer(serializers.ModelSerializer):
    total_invoiced = serializers.DecimalField(
        source='receivable_invoiced', max_digits=14, decimal_places=2, read_only=True,
    )
    total_paid = serializers.DecimalField(
        source='receivable_paid', max_digits=14, decimal_places=2, read_only=True,
    )
    balance_due = serializers.SerializerMethodField()
    invoice_count = serializers.IntegerField(source='receivable_invoice_count', read_only=True)
    unpaid_invoice_count = serializers.SerializerMethodField()
    last_payment_at = serializers.DateTimeField(source='receivable_last_payment', read_only=True)
    unreconciled_invoice_count = serializers.IntegerField(
        source='receivable_unreconciled_count', read_only=True,
    )

    class Meta:
        model = Client
        fields = (
            'id', 'name', 'phone', 'email', 'tax_number', 'total_invoiced',
            'total_paid', 'balance_due', 'invoice_count', 'unpaid_invoice_count',
            'last_payment_at', 'unreconciled_invoice_count',
        )

    def get_balance_due(self, obj) -> Decimal:
        return max(obj.receivable_invoiced - obj.receivable_paid, Decimal('0.00'))

    def get_unpaid_invoice_count(self, obj) -> int:
        cached = getattr(obj, 'receivable_unpaid_count', None)
        if cached is not None:
            return cached
        return sum(1 for sale in obj.receivable_sales_cache if sale.balance_due > 0)


class LoadingOrderLineSerializer(serializers.ModelSerializer):
    quantity = serializers.IntegerField(min_value=1)
    product_name = serializers.CharField(source='product.name', read_only=True)
    sold_quantity = serializers.IntegerField(read_only=True)
    packaging_id = serializers.PrimaryKeyRelatedField(
        source='packaging', queryset=ProductPackaging.objects.all(),
        required=False, allow_null=True,
    )
    entered_quantity = serializers.IntegerField(read_only=True)
    base_quantity = serializers.IntegerField(source='quantity', read_only=True)

    class Meta:
        model = LoadingOrderLine
        fields = (
            'id', 'product', 'product_name', 'packaging_id', 'packaging_name',
            'packaging_factor', 'packaging_quantity', 'entered_quantity',
            'quantity', 'base_quantity', 'returned_quantity', 'sold_quantity',
        )
        read_only_fields = (
            'packaging_name', 'packaging_factor', 'packaging_quantity',
            'entered_quantity', 'base_quantity', 'returned_quantity', 'sold_quantity',
        )

    def validate(self, attrs):
        product = attrs.get('product')
        packaging = attrs.get('packaging')
        if packaging and not loading_packaging_allowed(product, packaging):
            raise serializers.ValidationError({'packaging_id': _('Conditionnement de chargement invalide ou inactif.')})
        if not packaging and not loading_base_unit_allowed(product):
            raise serializers.ValidationError({'packaging_id': _('Ce produit doit être chargé en carton.')})
        return attrs


class LoadingOrderSerializer(serializers.ModelSerializer):
    lines = LoadingOrderLineSerializer(many=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = LoadingOrder
        fields = (
            'id', 'number', 'operator', 'operator_name', 'status', 'notes', 'lines',
            'created_by_name', 'created_at', 'validated_at', 'closed_at',
        )
        read_only_fields = ('number', 'status', 'created_by_name', 'created_at', 'validated_at', 'closed_at')

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError(_('Ajoutez au moins un produit.'))
        return lines

    @staticmethod
    def _create_lines(order, lines):
        for data in lines:
            line_data = dict(data)
            entered_quantity = line_data.pop('quantity')
            LoadingOrderLine.objects.create(
                loading_order=order,
                packaging_quantity=entered_quantity,
                quantity=entered_quantity,
                **line_data,
            )

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop('lines')
        order = LoadingOrder.objects.create(
            number=generate_loading_number(), created_by=self.context['request'].user, **validated_data,
        )
        self._create_lines(order, lines)
        return order

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = LoadingOrder.objects.select_for_update().get(pk=instance.pk)
        if instance.status != LoadingOrder.DRAFT:
            raise BusinessAPIException('LOADING_NOT_DRAFT', _('Seul un brouillon peut être modifié.'))
        lines = validated_data.pop('lines', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if lines is not None:
            instance.lines.all().delete()
            self._create_lines(instance, lines)
        return instance


class OperatorStockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OperatorStock
        fields = ('id', 'product', 'product_name', 'quantity', 'updated_at')
        read_only_fields = fields


class OperatorStockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OperatorStockMovement
        fields = (
            'id', 'product', 'product_name', 'loading_order', 'movement_type', 'quantity',
            'applied_delta', 'balance_before', 'balance_after', 'source_reference', 'created_at',
        )
        read_only_fields = fields


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
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
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
            'connection_mode', 'connection_mode_display', 'local_identifier', 'bluetooth_name', 'bluetooth_address', 'ip_address',
            'network_port', 'paper_width', 'protocol', 'protocol_display',
            'characters_per_line', 'encoding', 'auto_print', 'print_invoices', 'print_receipts', 'is_default', 'is_active',
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
