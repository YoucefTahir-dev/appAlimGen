from decimal import Decimal

from django.db.models import Count, DateTimeField, DecimalField, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.accounts.permissions import has_permission
from apps.inventory.models import Client, LoadingOrder

from .models import Payment, Sale


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def receivable_sales(user):
    sales = Sale.objects.filter(payment_tracking_initialized=True)
    if LoadingOrder.objects.filter(operator=user).exists() and not has_permission(
        user, 'inventory.view_all_loadingorders',
    ):
        sales = sales.filter(created_by=user)
    return sales


def receivables_queryset(user):
    sales = receivable_sales(user)
    totals = sales.filter(client_id=OuterRef('pk')).values('client_id').annotate(
        value=Sum('total'),
    ).values('value')[:1]
    invoice_counts = sales.filter(client_id=OuterRef('pk')).values('client_id').annotate(
        value=Count('pk'),
    ).values('value')[:1]
    payments = Payment.objects.filter(
        sale__in=sales, sale__client_id=OuterRef('pk'), cancelled_at__isnull=True,
    ).values('sale__client_id').annotate(value=Sum('amount')).values('value')[:1]
    last_payments = Payment.objects.filter(
        sale__in=sales, sale__client_id=OuterRef('pk'), cancelled_at__isnull=True,
    ).order_by('-created_at').values('created_at')[:1]
    unreconciled = Sale.objects.filter(
        client_id=OuterRef('pk'), payment_tracking_initialized=False,
    ).values('client_id').annotate(value=Count('pk')).values('value')[:1]
    return Client.objects.annotate(
        receivable_invoiced=Coalesce(Subquery(totals, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD),
        receivable_paid=Coalesce(Subquery(payments, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD),
        receivable_invoice_count=Coalesce(Subquery(invoice_counts), Value(0)),
        receivable_last_payment=Subquery(last_payments, output_field=DateTimeField()),
        receivable_unreconciled_count=Coalesce(Subquery(unreconciled), Value(0)),
    ).prefetch_related(Prefetch(
        'sales',
        queryset=sales.prefetch_related(Prefetch(
            'payments',
            queryset=Payment.objects.filter(cancelled_at__isnull=True),
            to_attr='active_payments',
        )),
        to_attr='receivable_sales_cache',
    ))


def attach_receivable_counts(clients):
    for client in clients:
        client.receivable_unpaid_count = sum(
            1
            for sale in client.receivable_sales_cache
            if sale.total > sum(
                (payment.amount for payment in sale.active_payments),
                Decimal('0.00'),
            )
        )
    return clients


def receivable_account(user, client):
    sales = list(
        receivable_sales(user).filter(client=client).prefetch_related(
            Prefetch('payments', queryset=Payment.objects.select_related(
                'created_by', 'cancelled_by',
            ).order_by('-created_at', '-pk')),
        ).order_by('-created_at', '-pk')
    )
    payments = sorted(
        (payment for sale in sales for payment in sale.payments.all()),
        key=lambda payment: (payment.created_at, payment.pk),
        reverse=True,
    )
    invoiced = sum((sale.total for sale in sales), Decimal('0.00'))
    paid = sum(
        (payment.amount for payment in payments if not payment.is_cancelled),
        Decimal('0.00'),
    )
    return {
        'client': client,
        'sales': sales,
        'payments': payments,
        'total_invoiced': invoiced,
        'total_paid': paid,
        'balance_due': max(invoiced - paid, Decimal('0.00')),
        'unpaid_sales': [sale for sale in sales if sale.balance_due == sale.total],
        'partial_sales': [sale for sale in sales if 0 < sale.balance_due < sale.total],
        'paid_sales': [sale for sale in sales if sale.balance_due == 0],
        'unreconciled_count': Sale.objects.filter(
            client=client, payment_tracking_initialized=False,
        ).count(),
    }
