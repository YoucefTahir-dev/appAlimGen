from django.contrib import admin
from .models import (
    InvoiceSequence,
    OpeningBalancePayment,
    Payment,
    Purchase,
    PurchaseLine,
    Sale,
    SaleLine,
    TicketSequence,
)

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
    list_display = (
        'purchase', 'product', 'packaging_name', 'packaging_quantity',
        'quantity', 'purchase_price',
    )

    def save_model(self, request, obj, form, change):
        obj._stock_user = request.user
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        obj._stock_user = request.user
        super().delete_model(request, obj)

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    actions = None
    list_display = (
        'invoice_number', 'ticket_number', 'client', 'payment_type',
        'snapshot_integrity_status', 'created_at', 'total',
    )
    search_fields = ('invoice_number', 'ticket_number', 'client__name')
    list_filter = ('payment_type', 'snapshot_integrity_status')
    readonly_fields = (
        'invoice_number', 'ticket_number', 'issued_at', 'snapshot_version',
        'snapshot_integrity_status', 'currency_snapshot',
        'subtotal_ht_snapshot', 'tax_amount_snapshot',
    )

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
    list_display = ('sale', 'product', 'packaging_name', 'packaging_quantity', 'quantity', 'unit_price', 'unit_cost')
    readonly_fields = (
        'unit_cost', 'packaging_name', 'packaging_factor',
        'product_name_snapshot', 'product_reference_snapshot',
        'unit_name_snapshot', 'line_total_ht_snapshot',
    )

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


@admin.register(OpeningBalancePayment)
class OpeningBalancePaymentAdmin(admin.ModelAdmin):
    """Read-only audit trail; creation and cancellation go through the service API."""

    actions = None
    list_display = (
        'reference', 'client', 'payment_type', 'amount', 'created_at', 'cancelled_at',
    )
    list_filter = ('payment_type', 'cancelled_at')
    search_fields = ('reference', 'client__name', 'external_reference')
    readonly_fields = (
        'reference', 'client', 'amount', 'payment_type', 'external_reference',
        'notes', 'created_by', 'created_at', 'cancelled_at', 'cancelled_by',
        'cancellation_reason',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False
