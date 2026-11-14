from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DateTimeField, DecimalField, F, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Round
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import Client

from .models import OpeningBalancePayment, Payment, Sale
from .visibility import personal_sales_only


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
    if personal_sales_only(user):
        sales = sales.filter(created_by=user)
    return sales


def receivables_queryset(user):
    sales = receivable_sales(user)
    # Count outstanding invoices in SQL, not by instantiating every invoice and
    # payment behind a client-list page. Keep the same visibility and active-
    # payment rules as the account calculation (including partial payments).
    paid_per_sale = Payment.objects.filter(
        sale_id=OuterRef('pk'), cancelled_at__isnull=True,
    ).order_by().values('sale_id').annotate(value=Sum('amount')).values('value')[:1]
    unpaid_counts = sales.filter(client_id=OuterRef('pk')).annotate(
        active_paid=Coalesce(
            # Monetary amounts have two decimal places. Rounding also avoids
            # SQLite floating-point SUM artefacts marking a paid bill unpaid.
            Round(Subquery(paid_per_sale, output_field=MONEY_FIELD), precision=2),
            Value(Decimal('0.00')), output_field=MONEY_FIELD,
        ),
    ).filter(total__gt=F('active_paid')).order_by().values('client_id').annotate(
        value=Count('pk'),
    ).values('value')[:1]
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
    historical_sales = Sale.objects.filter(payment_tracking_initialized=False)
    if personal_sales_only(user):
        historical_sales = historical_sales.filter(created_by=user)
    unreconciled = historical_sales.filter(
        client_id=OuterRef('pk'),
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
        receivable_unpaid_count=Coalesce(Subquery(unpaid_counts), Value(0)),
        receivable_opening_payment_count=Count('opening_balance_payments'),
        receivable_last_payment=Subquery(last_payments, output_field=DateTimeField()),
        receivable_unreconciled_count=Coalesce(Subquery(unreconciled), Value(0)),
    )


def attach_receivable_counts(clients):
    """Compatibility hook: counts are now already annotated by SQL."""
    return clients


def receivable_account_page(user, client, *, page=1, payments_page=1, opening_page=1, page_size=25):
    """Bound each history independently; totals always cover the whole account.

    Separate from the installed clients' legacy complete-response contract.
    Invoice amounts are SQL annotations, never an unbounded payment prefetch.
    """
    page_size = max(1, min(int(page_size), 100))
    summary = receivables_queryset(user).get(pk=client.pk)
    sales = receivable_sales(user).filter(client=client)
    paid = Payment.objects.filter(
        sale_id=OuterRef('pk'), cancelled_at__isnull=True,
    ).order_by().values('sale_id').annotate(value=Sum('amount')).values('value')[:1]
    invoice_rows = sales.annotate(
        active_paid=Coalesce(
            Round(Subquery(paid, output_field=MONEY_FIELD), precision=2),
            Value(Decimal('0.00')), output_field=MONEY_FIELD,
        ),
    ).order_by('-created_at', '-pk').values(
        'id', 'invoice_number', 'created_at', 'total', 'active_paid',
    )
    invoice_page = Paginator(invoice_rows, page_size).get_page(page)
    rows = []
    for row in invoice_page.object_list:
        amount_paid = row.pop('active_paid')
        due = max(row['total'] - amount_paid, Decimal('0.00'))
        row.update(pk=row['id'], amount_paid=amount_paid, balance_due=due,
                   payment_status='paid' if due == 0 else ('partial' if amount_paid > 0 else 'unpaid'))
        rows.append(row)
    invoice_page.object_list = rows
    payment_page = Paginator(
        Payment.objects.filter(sale__in=sales).select_related('sale', 'created_by', 'cancelled_by')
        .order_by('-created_at', '-pk'), page_size,
    ).get_page(payments_page)
    opening_payment_page = Paginator(
        OpeningBalancePayment.objects.filter(client=client)
        .select_related('created_by', 'cancelled_by').order_by('-created_at', '-pk'), page_size,
    ).get_page(opening_page)
    return {
        'client': client,
        'sales': invoice_page, 'payments': payment_page,
        'opening_balance_payments': opening_payment_page,
        'invoice_page': invoice_page, 'payment_page': payment_page,
        'opening_payment_page': opening_payment_page,
        'opening_balance': client.balance,
        'total_invoiced': summary.receivable_invoiced,
        'total_paid': summary.receivable_paid,
        'balance_due': max(summary.receivable_invoiced - summary.receivable_paid, Decimal('0.00')),
        'unreconciled_count': summary.receivable_unreconciled_count,
        'history_paginated': True,
    }


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
        'unreconciled_count': receivables_queryset(user).get(pk=client.pk).receivable_unreconciled_count,
    }
