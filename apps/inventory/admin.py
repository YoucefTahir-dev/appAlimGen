from django.contrib import admin
from .models import Category, Brand, Unit, Product, StockMovement, Client, Supplier

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
    list_display = ('reference', 'barcode', 'name', 'category', 'brand', 'quantity', 'minimum_stock', 'sale_price')
    search_fields = ('reference', 'barcode', 'name')
    list_filter = ('category', 'brand')

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'movement_type', 'quantity', 'created_at')
    list_filter = ('movement_type',)

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'wilaya', 'balance')
    search_fields = ('name', 'phone')

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'wilaya')
    search_fields = ('name', 'phone')
