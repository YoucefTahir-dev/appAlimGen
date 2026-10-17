from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DateTimeField, DecimalField, F, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.permissions import has_permission
from apps.inventory.models import Client, LoadingOrder

from .models import OpeningBalancePayment, Payment, Sale


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


@transaction.atomic
def allocate_client_payment(
    *, client, user, amount, payment_type, created_at=None,
    external_reference='', notes='',
):
    """Allocate one receipt FIFO across the client's visible outstanding invoices."""
    client = Client.objects.select_for_update().get(pk=client.pk)
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    if amount <= 0:
        raise ValidationError({'amount': _('Le montant doit être strictement positif.')})
    sales = list(
        receivable_sales(user).select_for_update().filter(client=client)
        .order_by('created_at', 'pk')
    )
    sale_ids = [sale.pk for sale in sales]
    payments = list(
        Payment.objects.select_for_update().filter(
            sale_id__in=sale_ids, cancelled_at__isnull=True,
        ).order_by('sale_id', 'pk')
    )
    paid_by_sale = {}
    for payment in payments:
        paid_by_sale[payment.sale_id] = (
            paid_by_sale.get(payment.sale_id, Decimal('0.00')) + payment.amount
        )
    outstanding = [
        (sale, max(sale.total - paid_by_sale.get(sale.pk, Decimal('0.00')), Decimal('0.00')))
        for sale in sales
    ]
    invoice_due = sum((due for _sale, due in outstanding), Decimal('0.00'))
    total_due = invoice_due + client.balance
    if amount > total_due:
        raise ValidationError({
            'amount': _('Le montant dépasse le total des créances restantes (%(total).2f DZD).')
            % {'total': total_due},
        })
    remaining = amount
    allocations = []
    for sale, due in outstanding:
        if remaining <= 0:
            break
        allocated = min(remaining, due)
        if allocated <= 0:
            continue
        payment = Payment.objects.create(
            sale=sale,
            amount=allocated,
            payment_type=payment_type,
            created_by=user,
            created_at=created_at or timezone.now(),
            external_reference=external_reference,
            notes=notes,
        )
        allocations.append(payment)
        remaining -= allocated
    opening_payment = None
    if remaining > 0:
        opening_payment = OpeningBalancePayment.objects.create(
            client=client,
            amount=remaining,
            payment_type=payment_type,
            created_by=user,
            created_at=created_at or timezone.now(),
            external_reference=external_reference,
            notes=notes,
        )
        remaining = Decimal('0.00')
    if remaining != 0:
        raise ValidationError({'amount': _('Le montant n’a pas pu être affecté complètement.')})
    return allocations, opening_payment


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
    opening_payments = OpeningBalancePayment.objects.filter(
        client_id=OuterRef('pk'), cancelled_at__isnull=True,
    ).values('client_id').annotate(value=Sum('amount')).values('value')[:1]
    return Client.objects.annotate(
        receivable_opening_paid=Coalesce(Subquery(opening_payments, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD),
        receivable_invoiced=(
            Coalesce(Subquery(totals, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD)
            + F('balance')
            + Coalesce(Subquery(opening_payments, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD)
        ),
        receivable_paid=(
            Coalesce(Subquery(payments, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD)
            + Coalesce(Subquery(opening_payments, output_field=MONEY_FIELD), Value(Decimal('0.00')), output_field=MONEY_FIELD)
        ),
        receivable_invoice_count=Coalesce(Subquery(invoice_counts), Value(0)),
        receivable_opening_payment_count=Count('opening_balance_payments'),
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
    opening_payments = list(
        OpeningBalancePayment.objects.filter(client=client)
        .select_related('created_by', 'cancelled_by')
        .order_by('-created_at', '-pk')
    )
    active_opening_paid = sum(
        (payment.amount for payment in opening_payments if not payment.is_cancelled),
        Decimal('0.00'),
    )
    invoiced = (
        sum((sale.total for sale in sales), Decimal('0.00'))
        + client.balance
        + active_opening_paid
    )
    paid = sum(
        (payment.amount for payment in payments if not payment.is_cancelled),
        Decimal('0.00'),
    ) + active_opening_paid
    return {
        'client': client,
        'sales': sales,
        'payments': payments,
        'opening_balance': client.balance,
        'opening_balance_payments': opening_payments,
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
