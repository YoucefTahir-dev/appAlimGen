"""Compatible key enforcement and live receivable replay scopes."""

from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APITestCase

from apps.api.exceptions import BusinessAPIException
from apps.api.idempotency import idempotent, mutation_key_required
from apps.api.models import IdempotencyRecord
from apps.api.tests.test_idempotency_replay_access import ReplayApiFixtures
from apps.commerce.models import OpeningBalancePayment, Payment
from apps.inventory.models import LoadingOrder


class KeyRolloutPolicyTests(SimpleTestCase):
    @override_settings(API_IDEMPOTENCY_REQUIRED=False)
    def test_default_accepts_old_client_but_honours_new_client_opt_in(self):
        self.assertFalse(mutation_key_required(SimpleNamespace(headers={})))
        self.assertTrue(mutation_key_required(SimpleNamespace(headers={'Idempotency-Policy': 'required-v1'})))

    @override_settings(API_IDEMPOTENCY_REQUIRED=True)
    def test_operator_can_enforce_for_every_client_without_negotiation(self):
        self.assertTrue(mutation_key_required(SimpleNamespace(headers={})))
        self.assertTrue(mutation_key_required(SimpleNamespace(headers={'Idempotency-Policy': 'required-v1'})))

    @override_settings(API_IDEMPOTENCY_REQUIRED=False)
    def test_unknown_or_attempted_opt_out_policy_is_explicitly_rejected(self):
        for policy in ('optional', 'false', 'required-v2'):
            with self.subTest(policy=policy), self.assertRaises(BusinessAPIException) as raised:
                mutation_key_required(SimpleNamespace(headers={'Idempotency-Policy': policy}))
            self.assertEqual(raised.exception.business_code, 'INVALID_IDEMPOTENCY_POLICY')

    def test_required_key_rejects_before_mutation_callback(self):
        operation = Mock(return_value=Response({'id': 1}))
        with self.assertRaises(BusinessAPIException):
            idempotent(SimpleNamespace(headers={}), 'synthetic.enforce', operation, required=True)
        operation.assert_not_called()


class ReceivableReplayScopeTests(ReplayApiFixtures, APITestCase):
    def allocate(self, amount='40.00', key='synthetic-receivable-intent', **headers):
        return self.client.post(
            reverse('api-receivable-allocate-payment', args=[self.customer.pk]),
            {'amount': amount, 'payment_type': 'cash'}, format='json',
            HTTP_IDEMPOTENCY_KEY=key, **headers,
        )

    def test_replay_after_full_settlement_does_not_revalidate_current_debt(self):
        self.grant('commerce.add_customerpayment')
        self.sale(creator=self.writer)
        first = self.allocate()
        self.assertEqual(first.status_code, 201, first.data)
        before = self.financial_state()
        replay = self.allocate()
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), before)

    def test_replay_denied_when_previous_invoice_leaves_current_operator_scope(self):
        self.grant('commerce.add_customerpayment', 'inventory.view_all_loadingorders')
        self.sale(creator=self.other)
        first = self.allocate()
        self.assertEqual(first.status_code, 201, first.data)
        LoadingOrder.objects.create(operator=self.writer, created_by=self.admin, number='SCOPE-CHANGE')
        self.deny('inventory.view_all_loadingorders')
        before = self.financial_state()
        replay = self.allocate()
        self.assertEqual(replay.status_code, 404, replay.data)
        self.assertEqual(self.financial_state(), before)

    def test_replay_rechecks_permission_after_revocation(self):
        self.grant('commerce.add_customerpayment')
        self.sale(creator=self.writer)
        self.assertEqual(self.allocate().status_code, 201)
        self.deny('commerce.add_customerpayment')
        before = self.financial_state()
        self.assertEqual(self.allocate().status_code, 403)
        self.assertEqual(self.financial_state(), before)

    def test_cancelled_opening_receipt_replays_original_result_without_reactivating_it(self):
        self.grant('commerce.add_customerpayment')
        self.customer.balance = '40.00'
        self.customer.save(update_fields=('balance',))
        first = self.allocate()
        self.assertEqual(first.status_code, 201, first.data)
        receipt = OpeningBalancePayment.objects.get()
        receipt.cancel(user=self.admin, reason='Synthetic cancellation')
        replay = self.allocate()
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)
        receipt.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertIsNotNone(receipt.cancelled_at)
        self.assertEqual(str(self.customer.balance), '40.00')
        self.assertEqual(OpeningBalancePayment.objects.count(), 1)
        self.assertFalse(Payment.objects.exists())

    def test_corrupt_replay_metadata_cannot_expose_another_clients_receipt(self):
        self.grant('commerce.add_customerpayment')
        self.sale(creator=self.writer)
        self.assertEqual(self.allocate().status_code, 201)
        record = IdempotencyRecord.objects.get()
        record.response_body = {'payments': [{'id': 999999}], 'opening_balance_payment': None}
        record.save(update_fields=('response_body',))
        before = self.financial_state()
        self.assertEqual(self.allocate().status_code, 404)
        self.assertEqual(self.financial_state(), before)

    @override_settings(API_IDEMPOTENCY_REQUIRED=True)
    def test_global_enforcement_rejects_missing_key_on_all_covered_mutation_routes(self):
        self.authenticate(self.admin)
        sale = self.sale()
        purchase = self.purchase()
        order = LoadingOrder.objects.create(operator=self.writer, created_by=self.admin, number='REQUIRED-KEY')
        routes = [
            ('post', reverse('api-sale-list')),
            ('patch', reverse('api-sale-detail', args=[sale.pk])),
            ('post', reverse('api-purchase-list')),
            ('patch', reverse('api-purchase-detail', args=[purchase.pk])),
            ('post', reverse('api-payment-list')),
            ('post', reverse('api-receivable-allocate-payment', args=[self.customer.pk])),
            ('post', reverse('api-loading-order-list')),
            ('post', reverse('api-loading-order-validate', args=[order.pk])),
            ('post', reverse('api-loading-order-close', args=[order.pk])),
        ]
        before = self.financial_state()
        for method, url in routes:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, {}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(response.data['error']['code'], 'IDEMPOTENCY_KEY_REQUIRED')
        self.assertEqual(self.financial_state(), before)
        self.assertFalse(IdempotencyRecord.objects.exists())

    @override_settings(API_IDEMPOTENCY_REQUIRED=False)
    def test_opt_in_requires_key_but_same_server_still_accepts_legacy_client(self):
        self.grant('commerce.add_customerpayment')
        self.sale(creator=self.writer)
        url = reverse('api-receivable-allocate-payment', args=[self.customer.pk])
        payload = {'amount': '10.00', 'payment_type': 'cash'}
        strict = self.client.post(url, payload, format='json', HTTP_IDEMPOTENCY_POLICY='required-v1')
        self.assertEqual(strict.status_code, 400)
        self.assertEqual(strict.data['error']['code'], 'IDEMPOTENCY_KEY_REQUIRED')
        legacy = self.client.post(url, payload, format='json')
        self.assertEqual(legacy.status_code, 201, legacy.data)
        strict_valid = self.client.post(
            url, payload, format='json', HTTP_IDEMPOTENCY_POLICY='required-v1',
            HTTP_IDEMPOTENCY_KEY='new-client-key-required',
        )
        self.assertEqual(strict_valid.status_code, 201, strict_valid.data)
