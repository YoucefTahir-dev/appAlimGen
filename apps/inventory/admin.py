from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import (
    Brand, Category, Client, ClientBalanceAdjustment, LoadingOrder, LoadingOrderLine, OperatorStock,
    OperatorStockMovement, Product, ProductPackaging, StockMovement, Supplier, Unit,
)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)

@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name',)

@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name',)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'reference',
        'barcode',
        'name',
        'category',
        'brand',
        'unit_mode',
        'quantity',
        'minimum_stock',
        'super_wholesale_price',
        'wholesale_price',
        'retail_price',
    )
    search_fields = ('reference', 'barcode', 'name')
    list_filter = ('category', 'brand', 'unit_mode')
    readonly_fields = ('reference', 'barcode', 'quantity', 'qr_code', 'barcode_image')


@admin.register(ProductPackaging)
class ProductPackagingAdmin(admin.ModelAdmin):
    list_display = (
        'product', 'name', 'kind', 'conversion_factor', 'reference_purchase_price',
        'super_wholesale_price', 'wholesale_price', 'retail_price', 'is_active',
    )
    list_filter = ('kind', 'purchase_enabled', 'sale_enabled', 'is_active')
    search_fields = ('product__name', 'product__reference', 'name', 'barcode')

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        'product',
        'movement_type',
        'quantity',
        'applied_delta',
        'balance_after',
        'source_type',
        'created_at',
    )
    list_filter = ('movement_type', 'source_type')
    readonly_fields = (
        'product',
        'movement_type',
        'quantity',
        'reason',
        'applied_delta',
        'balance_before',
        'balance_after',
        'source_type',
        'source_reference',
        'created_by',
        'reversal_of',
        'created_at',
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

class ClientAdminForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = '__all__'

    def clean(self):
        data = super().clean()
        if self.instance.pk and 'balance' in self.data:
            submitted = forms.DecimalField(max_digits=12, decimal_places=2).clean(self.data.get('balance'))
            if submitted != self.instance.balance:
                raise ValidationError(_(
                    'La dette antérieure est en lecture seule. Utilisez la correction dédiée dans la fiche client.'
                ))
        return data


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    form = ClientAdminForm
    list_display = ('name', 'phone', 'wilaya', 'customer_type', 'balance')
    list_filter = ('customer_type', 'wilaya')
    search_fields = (
        'name', 'phone', 'tax_number', 'nis',
        'article_number', 'trade_register_number',
    )

    def get_readonly_fields(self, request, obj=None):
        return ('balance',) if obj else ()

    def save_model(self, request, obj, form, change):
        from .balance_adjustments import save_client_profile, save_new_client_with_balance_audit
        if change:
            save_client_profile(client=obj)
        else:
            save_new_client_with_balance_audit(client=obj, user=request.user)


@admin.register(ClientBalanceAdjustment)
class ClientBalanceAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('client', 'kind', 'balance_before', 'balance_after', 'delta', 'actor_name_snapshot', 'created_at')
    list_filter = ('kind', 'created_at')
    search_fields = ('client__name', 'reason', 'actor_name_snapshot')
    readonly_fields = (
        'client', 'kind', 'balance_before', 'balance_after', 'delta', 'reason',
        'created_by', 'actor_id_snapshot', 'actor_name_snapshot', 'created_at',
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'wilaya')
    search_fields = ('name', 'phone')


class LoadingOrderLineInline(admin.TabularInline):
    model = LoadingOrderLine
    extra = 0
    readonly_fields = (
        'product', 'packaging', 'packaging_name', 'packaging_factor',
        'packaging_quantity', 'quantity', 'returned_quantity',
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LoadingOrder)
class LoadingOrderAdmin(admin.ModelAdmin):
    list_display = ('number', 'operator', 'status', 'created_at', 'validated_at', 'closed_at')
    list_filter = ('status',)
    search_fields = ('number', 'operator__username')
    readonly_fields = ('number', 'status', 'created_by', 'created_at', 'validated_at', 'closed_at')
    inlines = (LoadingOrderLineInline,)


@admin.register(OperatorStock)
class OperatorStockAdmin(admin.ModelAdmin):
    list_display = ('operator', 'product', 'quantity', 'updated_at')
    readonly_fields = ('operator', 'product', 'quantity', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OperatorStockMovement)
class OperatorStockMovementAdmin(admin.ModelAdmin):
    list_display = ('operator', 'product', 'loading_order', 'movement_type', 'applied_delta', 'balance_after', 'created_at')
    readonly_fields = [field.name for field in OperatorStockMovement._meta.fields]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
