from django.contrib import admin
from django.core.exceptions import PermissionDenied
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


class FinancialAuditAdmin(admin.ModelAdmin):
    """Audit only: financial changes must use the transactional business flows.

    Keep Django's per-model view/change permission check for read access. Even a
    superuser must not bypass document totals, payment or stock services here.
    """

    actions = None
    inlines = ()
    list_editable = ()
    autocomplete_fields = ()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}

    def get_inline_instances(self, request, obj=None):
        return []

    def get_readonly_fields(self, request, obj=None):
        # Include new concrete/M2M fields automatically, not reverse relations.
        return tuple(dict.fromkeys((
            *super().get_readonly_fields(request, obj),
            *(field.name for field in self.model._meta.concrete_fields),
            *(field.name for field in self.model._meta.many_to_many),
        )))

    def changelist_view(self, request, extra_context=None):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            raise PermissionDenied
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        raise PermissionDenied

    def save_related(self, request, form, formsets, change):
        raise PermissionDenied

    def delete_model(self, request, obj):
        raise PermissionDenied

    def delete_queryset(self, request, queryset):
        raise PermissionDenied


@admin.register(Purchase)
class PurchaseAdmin(FinancialAuditAdmin):
    list_display = ('reference', 'supplier', 'created_at', 'total')
    search_fields = ('reference',)


@admin.register(PurchaseLine)
class PurchaseLineAdmin(FinancialAuditAdmin):
    list_display = (
        'purchase', 'product', 'packaging_name', 'packaging_quantity',
        'quantity', 'purchase_price',
    )


@admin.register(Sale)
class SaleAdmin(FinancialAuditAdmin):
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


@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(FinancialAuditAdmin):
    list_display = ('year', 'last_number', 'updated_at')
    readonly_fields = ('year', 'last_number', 'updated_at')


@admin.register(TicketSequence)
class TicketSequenceAdmin(FinancialAuditAdmin):
    list_display = ('year', 'last_number', 'updated_at')
    readonly_fields = ('year', 'last_number', 'updated_at')


@admin.register(SaleLine)
class SaleLineAdmin(FinancialAuditAdmin):
    list_display = ('sale', 'product', 'packaging_name', 'packaging_quantity', 'quantity', 'unit_price', 'unit_cost')
    readonly_fields = (
        'unit_cost', 'packaging_name', 'packaging_factor',
        'product_name_snapshot', 'product_reference_snapshot',
        'unit_name_snapshot', 'line_total_ht_snapshot',
    )


@admin.register(Payment)
class PaymentAdmin(FinancialAuditAdmin):
    list_display = ('reference', 'payment_type', 'amount', 'sale', 'purchase', 'created_at')
    list_filter = ('payment_type',)
    readonly_fields = ('reference', 'created_by')


@admin.register(OpeningBalancePayment)
class OpeningBalancePaymentAdmin(FinancialAuditAdmin):
    """Read-only audit trail; creation and cancellation go through the service API."""

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
