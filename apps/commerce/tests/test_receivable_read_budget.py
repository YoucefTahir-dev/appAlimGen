"""Synthetic read budgets: never seed or call a production database."""
import json
import tracemalloc
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.models import Payment, Sale
from apps.commerce.receivables import receivables_queryset
from apps.inventory.models import Client, LoadingOrder


class ReceivableReadBudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username='synthetic-read-budget')
        cls.clients_by_volume = {}
        for volume in (10, 1000, 10000):
            client = Client.objects.create(name=f'Synthetic-volume-{volume}-only')
            cls.clients_by_volume[volume] = client
            sales = Sale.objects.bulk_create([
                Sale(invoice_number=f'SYN-RB-{volume}-{i}', client=client,
                     total=Decimal('100.00'), payment_tracking_initialized=True,
                     created_by=cls.user)
                for i in range(volume)
            ], batch_size=500)
            # Synthetic read fixtures only, not an alternate payment write API.
            models.QuerySet(model=Payment, using=connection.alias).bulk_create([
                Payment(reference=f'SYN-RBP-{volume}-{i}', sale_id=sale.pk,
                        amount=Decimal('40.00'), payment_type=Payment.CASH,
                        created_by=cls.user)
                for i, sale in enumerate(sales)
            ], batch_size=500)

    def test_api_client_page_never_materializes_sales_or_payments(self):
        api = APIClient()
        api.force_authenticate(self.user)
        for volume, client in self.clients_by_volume.items():
            with self.subTest(volume=volume), patch.object(
                Sale, 'from_db', side_effect=AssertionError('Invoice materialized on summary page'),
            ) as sale_load, patch.object(
                Payment, 'from_db', side_effect=AssertionError('Payment materialized on summary page'),
            ) as payment_load:
                tracemalloc.start()
                try:
                    with CaptureQueriesContext(connection) as queries:
                        response = api.get(reverse('api-receivable-list'), {
                            'status': 'all', 'page_size': 1, 'search': client.name,
                        })
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['count'], 1)
                row = response.data['results'][0]
                self.assertEqual(Decimal(row['total_invoiced']), volume * Decimal('100.00'))
                self.assertEqual(Decimal(row['total_paid']), volume * Decimal('40.00'))
                self.assertEqual(Decimal(row['balance_due']), volume * Decimal('60.00'))
                self.assertEqual(row['unpaid_invoice_count'], volume)
                self.assertEqual(row['invoice_count'], volume)
                self.assertEqual(sale_load.call_count + payment_load.call_count, 0)
                self.assertLessEqual(len(queries), 5)
                print('RECEIVABLE_READ_BUDGET ' + json.dumps({
                    'database': connection.vendor, 'volume': volume,
                    'sale_payment_instances': 0, 'queries': len(queries),
                    'python_peak_bytes': peak, 'response_bytes': len(response.content),
                }, sort_keys=True))

    def test_web_summary_has_the_same_zero_object_budget(self):
        self.client.force_login(self.user)
        with patch.object(Sale, 'from_db', side_effect=AssertionError('Invoice loaded')), patch.object(
            Payment, 'from_db', side_effect=AssertionError('Payment loaded'),
        ):
            response = self.client.get(reverse('receivable_list'), {
                'status': 'all', 'q': self.clients_by_volume[10000].name,
            })
        self.assertEqual(response.status_code, 200)
        row = list(response.context['page_obj'].object_list)[0]
        self.assertEqual(row.receivable_unpaid_count, 10000)


class ReceivableAggregateSemanticsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='synthetic-aggregate')
        self.customer = Client.objects.create(name='Synthetic aggregate', balance=Decimal('-5.00'))

    def sale(self, suffix, total='100.00', **kwargs):
        return Sale.objects.create(invoice_number=f'SYN-AG-{suffix}', client=self.customer,
                                   total=Decimal(total), payment_tracking_initialized=True,
                                   created_by=self.user, **kwargs)

    def test_partial_cancelled_zero_and_unreconciled_semantics_and_legacy_detail(self):
        partial = self.sale('partial')
        paid = self.sale('paid')
        cancelled = self.sale('cancelled')
        self.sale('zero', total='0.00')
        Sale.objects.create(invoice_number='SYN-AG-legacy', client=self.customer,
                            total=Decimal('999.00'), payment_tracking_initialized=False)
        Payment.objects.create(sale=partial, amount=Decimal('40.00'), payment_type=Payment.CASH)
        Payment.objects.create(sale=paid, amount=Decimal('100.00'), payment_type=Payment.CASH)
        p = Payment.objects.create(sale=cancelled, amount=Decimal('25.00'), payment_type=Payment.CASH)
        p.cancel(user=self.user, reason='Synthetic cancellation')
        row = receivables_queryset(self.user).get(pk=self.customer.pk)
        self.assertEqual(row.receivable_unpaid_count, 2)
        self.assertEqual(row.receivable_invoice_count, 4)
        self.assertEqual(row.receivable_unreconciled_count, 1)
        self.assertEqual(row.receivable_invoiced, Decimal('295.00'))
        self.assertEqual(row.receivable_paid, Decimal('140.00'))
        api = APIClient()
        api.force_authenticate(self.user)
        # Installed mobile clients still receive complete legacy collections.
        with patch.object(Sale, 'from_db', wraps=Sale.from_db) as sales, patch.object(
            Payment, 'from_db', wraps=Payment.from_db,
        ) as payments:
            response = api.get(reverse('api-receivable-detail', args=[self.customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['invoices']), 4)
        self.assertEqual(len(response.data['payments']), 3)
        self.assertEqual(sales.call_count, 4)
        self.assertEqual(payments.call_count, 3)
        self.assertEqual(Decimal(response.data['balance_due']), Decimal('155.00'))

    def test_operator_scope_is_not_broadened_by_sql_counts(self):
        operator = get_user_model().objects.create_user(
            username='synthetic-operator', role=get_user_model().SELLER,
        )
        LoadingOrder.objects.create(number='SYN-RB-LOADING', operator=operator, created_by=self.user)
        self.sale('outside-scope')
        Sale.objects.create(invoice_number='SYN-RB-owned', client=self.customer,
                            created_by=operator, total=Decimal('20.00'), payment_tracking_initialized=True)
        row = receivables_queryset(operator).get(pk=self.customer.pk)
        self.assertEqual(row.receivable_invoice_count, 1)
        self.assertEqual(row.receivable_unpaid_count, 1)
        self.assertEqual(row.receivable_invoiced, Decimal('15.00'))

    def test_fractional_full_payment_is_not_counted_as_outstanding(self):
        sale = self.sale('fractional', total='30.30')
        for amount in ('10.10', '20.20'):
            Payment.objects.create(sale=sale, amount=Decimal(amount), payment_type=Payment.CASH)
        row = receivables_queryset(self.user).get(pk=self.customer.pk)
        self.assertEqual(sale.balance_due, Decimal('0.00'))
        self.assertEqual(row.receivable_unpaid_count, 0)
