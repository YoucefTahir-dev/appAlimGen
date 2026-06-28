from django.contrib import admin
from .models import InvoiceSequence, Purchase, PurchaseLine, Sale, SaleLine, Payment

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('reference', 'supplier', 'created_at', 'total')
    search_fields = ('reference',)

@admin.register(PurchaseLine)
class PurchaseLineAdmin(admin.ModelAdmin):
    list_display = ('purchase', 'product', 'quantity', 'purchase_price')

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'client', 'created_at', 'total')
    search_fields = ('invoice_number',)
    readonly_fields = ('invoice_number',)

@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(admin.ModelAdmin):
    list_display = ('year', 'last_number', 'updated_at')
    readonly_fields = ('updated_at',)

@admin.register(SaleLine)
class SaleLineAdmin(admin.ModelAdmin):
    list_display = ('sale', 'product', 'packaging', 'quantity', 'unit_price')

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('reference', 'payment_type', 'amount', 'created_at')
    list_filter = ('payment_type',)
