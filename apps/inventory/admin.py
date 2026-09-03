from django.contrib import admin
from .models import Category, Brand, Unit, Product, ProductPackaging, StockMovement, Client, Supplier

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
        'quantity',
        'minimum_stock',
        'super_wholesale_price',
        'wholesale_price',
        'retail_price',
    )
    search_fields = ('reference', 'barcode', 'name')
    list_filter = ('category', 'brand')
    readonly_fields = ('reference', 'barcode', 'quantity', 'qr_code', 'barcode_image')


@admin.register(ProductPackaging)
class ProductPackagingAdmin(admin.ModelAdmin):
    list_display = ('product', 'name', 'conversion_factor', 'default_sale_price', 'barcode', 'is_active')
    list_filter = ('is_active',)
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

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'wilaya', 'customer_type', 'balance')
    list_filter = ('customer_type', 'wilaya')
    search_fields = ('name', 'phone')

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'wilaya')
    search_fields = ('name', 'phone')
