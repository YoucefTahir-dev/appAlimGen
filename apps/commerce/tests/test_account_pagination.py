from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.models import Sale, Payment
from apps.commerce.receivables import receivable_account_page
from apps.inventory.models import Client


class AccountPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username='page-synthetic')
        cls.customer = Client.objects.create(name='Synthetic large history', balance=Decimal('50.00'))
        sales = Sale.objects.bulk_create([
            Sale(invoice_number=f'SYN-PAGE-{i}', client=cls.customer, total=Decimal('100.00'),
                 created_by=cls.user, payment_tracking_initialized=True) for i in range(10000)
        ], batch_size=500)
        models.QuerySet(model=Payment, using=connection.alias).bulk_create([
            Payment(reference=f'SYN-PAY-PAGE-{i}', sale=sale, amount=Decimal('40.00'),
                    payment_type=Payment.CASH, created_by=cls.user) for i, sale in enumerate(sales)
        ], batch_size=500)

    def test_new_api_contract_bounded_and_complete_via_navigation(self):
        api = APIClient()
        api.force_authenticate(self.user)
        with patch.object(Sale, 'from_db', wraps=Sale.from_db) as sales, \
                patch.object(Payment, 'from_db', wraps=Payment.from_db) as payments, \
                CaptureQueriesContext(connection) as queries:
            response = api.get(reverse('api-receivable-account-page', args=[self.customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['contract'], 'receivable-account-page-v1')
        self.assertEqual(len(response.data['invoices']), 25)
        self.assertEqual(len(response.data['payments']), 25)
        self.assertEqual(Decimal(response.data['total_invoiced']), Decimal('1000050.00'))
        self.assertEqual(Decimal(response.data['total_paid']), Decimal('400000.00'))
        self.assertEqual(Decimal(response.data['balance_due']), Decimal('600050.00'))
        self.assertEqual(response.data['pagination']['invoices']['count'], 10000)
        self.assertLessEqual(sales.call_count, 25)
        self.assertLessEqual(payments.call_count, 25)
        self.assertLessEqual(len(queries), 12)
        second = api.get(reverse('api-receivable-account-page', args=[self.customer.pk]), {'page': 2, 'payments_page': 2})
        self.assertFalse({x['id'] for x in response.data['invoices']} & {x['id'] for x in second.data['invoices']})
        self.assertEqual(response.data['total_invoiced'], second.data['total_invoiced'])

    def test_web_detail_and_statement_are_bounded_and_statement_explicit(self):
        self.client.force_login(self.user)
        for route in ('receivable_detail', 'receivable_statement'):
            with self.subTest(route=route), patch.object(Sale, 'from_db', wraps=Sale.from_db) as sales, \
                    patch.object(Payment, 'from_db', wraps=Payment.from_db) as payments:
                response = self.client.get(reverse(route, args=[self.customer.pk]), {'page': 2, 'payments_page': 3})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context['sales']), 25)
                self.assertEqual(response.context['invoice_page'].number, 2)
                self.assertEqual(response.context['payment_page'].number, 3)
                self.assertLessEqual(sales.call_count, 25)
                self.assertLessEqual(payments.call_count, 25)
                if route == 'receivable_statement':
                    self.assertContains(response, 'Extrait paginé')

    def test_page_size_is_capped_and_invalid_api_input_refused(self):
        account = receivable_account_page(self.user, self.customer, page_size=10000)
        self.assertEqual(len(account['sales']), 100)
        api = APIClient()
        api.force_authenticate(self.user)
        for query in ({'page_size': 101}, {'page': 0}, {'payments_page': 'bad'}):
            self.assertEqual(api.get(reverse('api-receivable-account-page', args=[self.customer.pk]), query).status_code, 400)

    def test_legacy_small_response_still_contains_all_rows_and_same_totals(self):
        customer = Client.objects.create(name='Synthetic small legacy')
        Sale.objects.create(invoice_number='SYN-PAGE-LEGACY', client=customer, total=Decimal('15.00'), created_by=self.user)
        api = APIClient()
        api.force_authenticate(self.user)
        old = api.get(reverse('api-receivable-detail', args=[customer.pk]))
        new = api.get(reverse('api-receivable-account-page', args=[customer.pk]))
        self.assertEqual(len(old.data['invoices']), 1)
        for key in ('total_invoiced', 'total_paid', 'balance_due', 'opening_balance'):
            self.assertEqual(Decimal(old.data[key]), Decimal(new.data[key]))

    def test_cancelled_fractional_zero_and_opening_payment_semantics_match_legacy(self):
        from apps.commerce.models import OpeningBalancePayment
        customer = Client.objects.create(name='Synthetic pagination semantics', balance=Decimal('50.00'))
        sale = Sale.objects.create(invoice_number='SYN-PAGE-FRACTION', client=customer,
            total=Decimal('30.30'), created_by=self.user)
        for amount in ('10.10', '20.20'):
            Payment.objects.create(sale=sale, amount=Decimal(amount), payment_type=Payment.CASH)
        other = Sale.objects.create(invoice_number='SYN-PAGE-CANCELLED', client=customer,
                                   total=Decimal('20.00'), created_by=self.user)
        payment = Payment.objects.create(sale=other, amount=Decimal('5.00'), payment_type=Payment.CASH)
        payment.cancel(user=self.user, reason='Synthetic cancellation')
        opening = OpeningBalancePayment.objects.create(client=customer, amount=Decimal('10.00'),
            payment_type=Payment.CASH, created_by=self.user)
        opening.cancel(user=self.user, reason='Synthetic cancellation')
        Sale.objects.create(invoice_number='SYN-PAGE-ZERO', client=customer,
                            total=Decimal('0.00'), created_by=self.user)
        api = APIClient()
        api.force_authenticate(self.user)
        old = api.get(reverse('api-receivable-detail', args=[customer.pk]))
        new = api.get(reverse('api-receivable-account-page', args=[customer.pk]))
        self.assertEqual(old.status_code, 200)
        self.assertEqual(new.status_code, 200)
        for key in ('total_invoiced', 'total_paid', 'balance_due', 'opening_balance'):
            self.assertEqual(Decimal(old.data[key]), Decimal(new.data[key]))
        self.assertEqual([row['payment_status'] for row in new.data['invoices']], ['paid', 'unpaid', 'paid'])
        self.assertEqual(len(new.data['opening_balance_payments']), 1)
        self.assertTrue(new.data['opening_balance_payments'][0]['is_cancelled'])
