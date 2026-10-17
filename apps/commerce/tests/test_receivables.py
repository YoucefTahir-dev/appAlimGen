from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.commerce.models import Payment, Sale
from apps.inventory.models import Client


class ReceivableAccountingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(
            username='receivable-manager', password='pass', role=User.MANAGER,
        )
        cls.customer = Client.objects.create(name='Client créancier', phone='0550000000')
        cls.other_customer = Client.objects.create(name='Autre client')
        cls.first = Sale.objects.create(
            invoice_number='FAC-CRE-001', client=cls.customer,
            total=Decimal('100.00'), payment_tracking_initialized=True,
        )
        cls.second = Sale.objects.create(
            invoice_number='FAC-CRE-002', client=cls.customer,
            total=Decimal('50.00'), payment_tracking_initialized=True,
        )
        cls.legacy = Sale.objects.create(
            invoice_number='FAC-CRE-LEGACY', client=cls.customer,
            total=Decimal('999.00'), payment_tracking_initialized=False,
        )

    def setUp(self):
        self.client.force_login(self.manager)

    def test_totals_ignore_unreconciled_and_cancelled_payments(self):
        Payment.objects.create(
            sale=self.first, amount=Decimal('40.00'), payment_type=Payment.CASH,
            created_by=self.manager,
        )
        cancelled = Payment.objects.create(
            sale=self.second, amount=Decimal('20.00'), payment_type=Payment.TRANSFER,
            created_by=self.manager,
        )
        cancelled.cancel(user=self.manager, reason='Virement rejeté')

        response = self.client.get(reverse('receivable_detail', args=[self.customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_invoiced'], Decimal('150.00'))
        self.assertEqual(response.context['total_paid'], Decimal('40.00'))
        self.assertEqual(response.context['balance_due'], Decimal('110.00'))
        self.assertEqual(response.context['unreconciled_count'], 1)
        self.assertContains(response, 'Virement rejeté')

    def test_customer_payment_is_explicitly_allocated_and_idempotent(self):
        payload = {
            'sale': self.first.pk,
            'amount': '25.00',
            'payment_type': Payment.CASH,
            'request_token': 'same-web-request',
            'created_at': '2026-10-16T10:30',
            'external_reference': 'CAISSE-42',
            'notes': 'Acompte',
        }
        url = reverse('receivable_payment_create', args=[self.customer.pk])
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(Payment.objects.filter(idempotency_key='same-web-request').count(), 1)
        payment = Payment.objects.get(idempotency_key='same-web-request')
        self.assertEqual(payment.sale, self.first)
        self.assertEqual(payment.external_reference, 'CAISSE-42')

    def test_cross_customer_allocation_and_overpayment_are_rejected(self):
        foreign = Sale.objects.create(
            invoice_number='FAC-CRE-OTHER', client=self.other_customer,
            total=Decimal('10.00'), payment_tracking_initialized=True,
        )
        url = reverse('receivable_payment_create', args=[self.customer.pk])
        response = self.client.post(url, {
            'sale': foreign.pk, 'amount': '5.00', 'payment_type': Payment.CASH,
            'request_token': 'cross-client', 'created_at': '2026-10-16T10:30',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(idempotency_key='cross-client').exists())
        with self.assertRaises(ValidationError):
            Payment.objects.create(
                sale=self.first, amount=Decimal('100.01'), payment_type=Payment.CASH,
            )

    def test_payment_cancellation_is_traceable_and_hard_delete_is_forbidden(self):
        payment = Payment.objects.create(
            sale=self.first, amount=Decimal('30.00'), payment_type=Payment.CHEQUE,
            created_by=self.manager,
        )
        payment.cancel(user=self.manager, reason='Chèque impayé')
        payment.refresh_from_db()
        self.assertTrue(payment.is_cancelled)
        self.assertEqual(payment.cancelled_by, self.manager)
        self.assertEqual(self.first.balance_due, Decimal('100.00'))
        with self.assertRaises(ValidationError):
            payment.delete()


class ReceivableApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser(
            username='receivable-api-admin', password='StrongPass123!',
        )
        cls.seller = User.objects.create_user(
            username='receivable-api-seller', password='StrongPass123!', role=User.SELLER,
        )
        cls.customer = Client.objects.create(name='Client API créances')
        cls.sale = Sale.objects.create(
            invoice_number='FAC-API-CRE-001', client=cls.customer,
            total=Decimal('80.00'), payment_tracking_initialized=True,
        )

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def test_list_detail_create_and_cancel_payment(self):
        listing = self.client.get(reverse('api-receivable-list'), {'status': 'all'})
        self.assertEqual(listing.status_code, 200, listing.data)
        detail = self.client.get(reverse('api-receivable-detail', args=[self.customer.pk]))
        self.assertEqual(detail.status_code, 200, detail.data)

        created = self.client.post(
            reverse('api-payment-list'),
            {
                'sale': self.sale.pk, 'amount': '35.00', 'payment_type': Payment.CASH,
                'external_reference': 'MOB-001', 'notes': 'Android',
            },
            format='json', HTTP_IDEMPOTENCY_KEY='mobile-payment-1',
        )
        self.assertEqual(created.status_code, 201, created.data)
        payment = Payment.objects.get(sale=self.sale)
        cancelled = self.client.post(
            reverse('api-payment-cancel', args=[payment.pk]),
            {'reason': 'Test annulation'}, format='json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        payment.refresh_from_db()
        self.assertTrue(payment.is_cancelled)
        self.assertEqual(self.sale.balance_due, Decimal('80.00'))

    def test_cancel_payment_requires_dedicated_permission(self):
        payment = Payment.objects.create(
            sale=self.sale, amount=Decimal('10.00'), payment_type=Payment.CASH,
            created_by=self.admin,
        )
        self.client.force_authenticate(self.seller)
        response = self.client.post(
            reverse('api-payment-cancel', args=[payment.pk]),
            {'reason': 'Tentative non autorisée'}, format='json',
        )
        self.assertEqual(response.status_code, 403, response.data)
        payment.refresh_from_db()
        self.assertFalse(payment.is_cancelled)


class PostgreSQLPaymentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Verrouillage concurrent validé dans la CI PostgreSQL.')
        User = get_user_model()
        self.user = User.objects.create_user(
            username='payment-concurrent', password='StrongPass123!', role=User.MANAGER,
        )
        self.customer = Client.objects.create(name='Client paiement concurrent')
        self.sale = Sale.objects.create(
            invoice_number='FAC-PAY-CONCURRENT', client=self.customer,
            total=Decimal('100.00'), payment_tracking_initialized=True,
        )

    def _pay_sixty(self):
        close_old_connections()
        try:
            Payment.objects.create(
                sale_id=self.sale.pk,
                amount=Decimal('60.00'),
                payment_type=Payment.CASH,
                created_by_id=self.user.pk,
            )
            return 'ok'
        except ValidationError:
            return 'rejected'
        finally:
            close_old_connections()

    def test_concurrent_payments_cannot_overpay_invoice(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._pay_sixty(), range(2)))
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('rejected'), 1)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount_paid, Decimal('60.00'))
        self.assertEqual(self.sale.balance_due, Decimal('40.00'))
