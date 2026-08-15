from django.contrib import admin
from .models import InvoiceSequence, Purchase, PurchaseLine, Sale, SaleLine, Payment, TicketSequence

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('reference', 'supplier', 'created_at', 'total')
    search_fields = ('reference',)

    def delete_model(self, request, obj):
        obj._stock_user = request.user
        super().delete_model(request, obj)

@admin.register(PurchaseLine)
class PurchaseLineAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('purchase', 'product', 'quantity', 'purchase_price')

    def save_model(self, request, obj, form, change):
        obj._stock_user = request.user
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        obj._stock_user = request.user
        super().delete_model(request, obj)

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('invoice_number', 'ticket_number', 'client', 'payment_type', 'created_at', 'total')
    search_fields = ('invoice_number', 'ticket_number', 'client__name')
    list_filter = ('payment_type',)
    readonly_fields = ('invoice_number', 'ticket_number')

    def delete_model(self, request, obj):
        obj._stock_user = request.user
        super().delete_model(request, obj)

@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(admin.ModelAdmin):
    list_display = ('year', 'last_number', 'updated_at')
    readonly_fields = ('year', 'last_number', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(TicketSequence)
class TicketSequenceAdmin(admin.ModelAdmin):
    list_display = ('year', 'last_number', 'updated_at')
    readonly_fields = ('year', 'last_number', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(SaleLine)
class SaleLineAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('sale', 'product', 'quantity', 'unit_price', 'unit_cost')
    readonly_fields = ('unit_cost',)

    def save_model(self, request, obj, form, change):
        obj._stock_user = request.user
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        obj._stock_user = request.user
        super().delete_model(request, obj)

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('reference', 'payment_type', 'amount', 'sale', 'purchase', 'created_at')
    list_filter = ('payment_type',)
    readonly_fields = ('reference', 'created_by')

    def save_model(self, request, obj, form, change):
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
