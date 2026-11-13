"""Local A(off) -> B(on) -> A(off) contract, not a 1006fe9 rollout proof.

All fixtures are synthetic. Initial journals are deliberately always active;
rolling the code back below the qualified bridge remains out of scope.
"""

from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.models import IdempotencyRecord
from apps.commerce.models import OpeningBalancePayment, Payment
from apps.commerce.web_idempotency import new_web_token
from apps.core.models import AuditLog
from apps.inventory.balance_adjustments import (
    adjust_client_balance, client_balance_corrections_enabled,
    create_client_with_balance_audit, save_client_profile,
)
from apps.inventory.models import Client, ClientBalanceAdjustment


User = get_user_model()


class ClientBalanceBridgeDefaultTests(SimpleTestCase):
    def test_corrections_are_closed_in_the_default_test_configuration(self):
        self.assertIs(settings.CLIENT_BALANCE_CORRECTIONS_ENABLED, False)
        self.assertFalse(client_balance_corrections_enabled())

    def test_only_explicit_boolean_true_enables_the_switch(self):
        for value in (False, None, '', 'false', 'true', 1):
            with self.subTest(value=value), override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=value):
                self.assertFalse(client_balance_corrections_enabled())
        with override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=True):
            self.assertTrue(client_balance_corrections_enabled())


@override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=False, LANGUAGE_CODE='fr')
class ClientBalanceBridgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser(username='debt-bridge-owner', password='unused')
        cls.customer = Client.objects.create(name='Synthetic historical bridge client', balance='100.00')

    def setUp(self):
        self.client.force_login(self.owner)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def web_url(self, customer=None):
        return reverse('client_balance_adjust', args=[(customer or self.customer).pk])

    def api_url(self, action='adjust-balance', customer=None):
        return reverse(f'api-client-{action}', args=[(customer or self.customer).pk])

    def payload(self, **overrides):
        return {
            'expected_balance': '100.00', 'new_balance': '80.00',
            'reason': 'Synthetic bridge correction', **overrides,
        }

    def api_correction(self, key='synthetic-bridge-api-key', **overrides):
        return self.api.post(
            self.api_url(), self.payload(**overrides), format='json',
            **({'HTTP_IDEMPOTENCY_KEY': key} if key is not None else {}),
        )

    def assert_balance(self, value):
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal(value))

    def assert_registry_not_consulted(self, captured):
        table = IdempotencyRecord._meta.db_table.lower()
        self.assertFalse(
            any(table in query['sql'].lower() for query in captured.captured_queries),
            'A disabled correction must be refused before consulting or writing its registry.',
        )

    def test_disabled_service_refuses_even_superuser_without_balance_or_journal_write(self):
        with patch('apps.inventory.balance_adjustments._record') as record:
            with self.assertRaisesMessage(PermissionDenied, 'désactivée'):
                adjust_client_balance(client_id=self.customer.pk, user=self.owner, **self.payload())
        record.assert_not_called()
        self.assert_balance('100.00')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_disabled_web_get_and_post_never_enter_registry_or_render_form(self):
        data = {**self.payload(), 'request_token': new_web_token()}
        for method in ('get', 'post'):
            with self.subTest(method=method), CaptureQueriesContext(connection) as captured:
                response = getattr(self.client, method)(self.web_url(), data if method == 'post' else {})
            self.assertContains(response, 'désactivée', status_code=403)
            self.assertNotContains(response, '<form', status_code=403)
            self.assert_registry_not_consulted(captured)
        self.assert_balance('100.00')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_disabled_api_refuses_before_key_and_payload_validation_or_registry(self):
        for key in (None, '', 'synthetic-disabled-key'):
            with self.subTest(key=key), CaptureQueriesContext(connection) as captured:
                with patch('apps.api.views.idempotent') as registry:
                    response = self.api_correction(key=key, new_balance='NaN', reason='')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertEqual(response.data['error']['code'], 'PERMISSION_DENIED')
            self.assertIn('désactivée', response.data['error']['message'])
            registry.assert_not_called()
            self.assert_registry_not_consulted(captured)
        self.assert_balance('100.00')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_regular_permissions_still_run_before_the_disabled_api_switch(self):
        user = User.objects.create_user(username='debt-bridge-no-financial-right', role=User.SELLER)
        self.api.force_authenticate(user)
        with patch('apps.api.views.require_client_balance_corrections_enabled') as guard:
            response = self.api_correction()
        self.assertEqual(response.status_code, 403)
        guard.assert_not_called()
        with self.assertRaises(PermissionDenied):
            adjust_client_balance(client_id=self.customer.pk, user=user, **self.payload())
        self.assert_balance('100.00')

    def test_disabling_blocks_a_previously_completed_web_replay_without_deleting_it(self):
        data = {**self.payload(), 'request_token': new_web_token()}
        with override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=True):
            first = self.client.post(self.web_url(), data)
        self.assertEqual(first.status_code, 302)
        before = list(IdempotencyRecord.objects.values())
        with CaptureQueriesContext(connection) as captured:
            replay = self.client.post(self.web_url(), data)
        self.assertContains(replay, 'désactivée', status_code=403)
        self.assertNotIn('Idempotency-Replayed', replay)
        self.assert_registry_not_consulted(captured)
        self.assertEqual(list(IdempotencyRecord.objects.values()), before)
        self.assert_balance('80.00')
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_disabling_blocks_a_previously_completed_api_replay_without_new_audit(self):
        with override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=True):
            first = self.api_correction()
        self.assertEqual(first.status_code, 201, first.data)
        before = list(IdempotencyRecord.objects.values())
        audits = AuditLog.objects.filter(action='api.client.adjust_balance', status_code=201).count()
        with CaptureQueriesContext(connection) as captured:
            replay = self.api_correction()
        self.assertEqual(replay.status_code, 403, replay.data)
        self.assertIn('désactivée', replay.data['error']['message'])
        self.assertNotIn('Idempotency-Replayed', replay)
        self.assert_registry_not_consulted(captured)
        self.assertEqual(list(IdempotencyRecord.objects.values()), before)
        self.assertEqual(AuditLog.objects.filter(action='api.client.adjust_balance', status_code=201).count(), audits)
        self.assert_balance('80.00')
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_disabled_cta_is_hidden_but_initial_history_remains_readable(self):
        customer = create_client_with_balance_audit(
            user=self.owner, name='Synthetic initial history', balance='42.50',
        )
        url = reverse('client_detail', args=[customer.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertIs(page.context['client_balance_corrections_enabled'], False)
        self.assertNotContains(page, f'href="{self.web_url(customer)}"')
        self.assertContains(page, 'Journal de dette antérieure')
        self.assertEqual(list(page.context['balance_adjustments']), list(customer.balance_adjustments.all()))
        history = self.api.get(self.api_url('balance-adjustments', customer))
        self.assertEqual(history.status_code, 200, history.data)
        rows = history.data.get('results', history.data) if isinstance(history.data, dict) else history.data
        self.assertEqual([row['kind'] for row in rows], [ClientBalanceAdjustment.INITIAL])
        with override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=True):
            self.assertContains(self.client.get(url), f'href="{self.web_url(customer)}"')

    def test_disabled_switch_keeps_service_initial_journals_and_zero_balance_creation(self):
        for balance in ('35.10', '-0.10'):
            with self.subTest(balance=balance):
                customer = create_client_with_balance_audit(
                    user=self.owner, name=f'Synthetic initial {balance}', balance=balance,
                )
                record = customer.balance_adjustments.get()
                self.assertEqual(record.kind, ClientBalanceAdjustment.INITIAL)
                self.assertEqual(record.balance_before, Decimal('0.00'))
                self.assertEqual(record.balance_after, Decimal(balance))
        zero = create_client_with_balance_audit(user=self.owner, name='Synthetic initial zero')
        self.assertFalse(zero.balance_adjustments.exists())
        zero_id = zero.pk
        zero.delete()
        self.assertFalse(Client.objects.filter(pk=zero_id).exists())

    def test_disabled_switch_keeps_initial_creation_via_web_api_and_admin(self):
        web = self.client.post(reverse('client_create'), {
            'name': 'Synthetic bridge web debtor', 'balance': '12.50',
        })
        self.assertEqual(web.status_code, 302)
        api = self.api.post(reverse('api-client-list'), {
            'name': 'Synthetic bridge api debtor', 'balance': '13.50',
        }, format='json')
        self.assertEqual(api.status_code, 201, api.data)
        request = RequestFactory().post('/admin/')
        request.user = self.owner
        created = Client(name='Synthetic bridge admin debtor', balance=Decimal('14.50'))
        admin.site._registry[Client].save_model(request, created, None, False)
        rows = list(ClientBalanceAdjustment.objects.order_by('balance_after').values_list('kind', 'balance_after'))
        self.assertEqual(rows, [
            (ClientBalanceAdjustment.INITIAL, Decimal('12.50')),
            (ClientBalanceAdjustment.INITIAL, Decimal('13.50')),
            (ClientBalanceAdjustment.INITIAL, Decimal('14.50')),
        ])

    def test_disabled_switch_cannot_silently_skip_a_failed_initial_journal(self):
        with patch('apps.inventory.balance_adjustments._record', side_effect=RuntimeError('synthetic initial failure')):
            with self.assertRaisesMessage(RuntimeError, 'synthetic initial failure'):
                create_client_with_balance_audit(user=self.owner, name='Must not survive bridge failure', balance='10.00')
        self.assertFalse(Client.objects.filter(name='Must not survive bridge failure').exists())
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_a_b_a_retains_money_traces_profile_safety_and_protection_on_the_same_schema(self):
        migrations_before = MigrationRecorder(connection).applied_migrations()
        with connection.cursor() as cursor:
            columns_before = connection.introspection.get_table_description(cursor, ClientBalanceAdjustment._meta.db_table)
        # A: safe bridge, corrections closed, initial debt still traceable.
        customer = create_client_with_balance_audit(user=self.owner, name='Synthetic A then B debtor', balance='35.00')
        stale = Client.objects.get(pk=customer.pk)
        initial = customer.balance_adjustments.get()
        # B: only the correction switch changes; no migration or historical backfill.
        with override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=True):
            correction = adjust_client_balance(
                client_id=customer.pk, expected_balance='35.00', new_balance='50.00',
                reason='Synthetic B evidence', user=self.owner,
            )
            receipt = OpeningBalancePayment.objects.create(
                client=customer, amount='7.00', payment_type=Payment.CASH, created_by=self.owner,
            )
        journal_before_rollback = list(customer.balance_adjustments.order_by('pk').values())
        # A again: even a privileged actor cannot correct, but tracing stays on.
        self.assertFalse(client_balance_corrections_enabled())
        with self.assertRaises(PermissionDenied):
            adjust_client_balance(
                client_id=customer.pk, expected_balance='43.00', new_balance='1.00',
                reason='Must be refused after code/config rollback', user=self.owner,
            )
        stale.balance = Decimal('999.00')
        stale.phone = '0123456789'
        save_client_profile(client=stale)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal('43.00'))
        self.assertEqual(customer.phone, '0123456789')
        profile = self.api.patch(self.api_url('detail', customer), {'phone': '0550000000'}, format='json')
        self.assertEqual(profile.status_code, 200, profile.data)
        web_profile = self.client.post(reverse('client_update', args=[customer.pk]), {
            'name': customer.name, 'phone': '0660000000', 'balance': '43.00',
        })
        self.assertEqual(web_profile.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal('43.00'))
        self.assertEqual(customer.phone, '0660000000')
        history = self.api.get(self.api_url('balance-adjustments', customer))
        self.assertEqual(history.status_code, 200, history.data)
        rows = history.data.get('results', history.data) if isinstance(history.data, dict) else history.data
        self.assertEqual({row['kind'] for row in rows}, {
            ClientBalanceAdjustment.INITIAL, ClientBalanceAdjustment.CORRECTION,
        })
        self.assertContains(
            self.client.get(reverse('client_detail', args=[customer.pk])), 'Synthetic B evidence',
        )
        receipt.cancel(user=self.owner, reason='Synthetic receipt cancellation in A')
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal('50.00'))
        self.assertEqual(list(customer.balance_adjustments.order_by('pk').values()), journal_before_rollback)
        self.assertEqual([initial.kind, correction.kind], [ClientBalanceAdjustment.INITIAL, ClientBalanceAdjustment.CORRECTION])
        later = create_client_with_balance_audit(user=self.owner, name='Synthetic after rollback debtor', balance='19.00')
        self.assertEqual(later.balance_adjustments.get().kind, ClientBalanceAdjustment.INITIAL)
        with self.assertRaises(ProtectedError):
            customer.delete()
        with self.assertRaises(ProtectedError):
            later.delete()
        with self.assertRaises(ValidationError):
            correction.save()
        with self.assertRaises(ValidationError):
            customer.balance_adjustments.update(reason='Forbidden rewrite in A')
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 3)
        self.assertEqual(MigrationRecorder(connection).applied_migrations(), migrations_before)
        with connection.cursor() as cursor:
            columns_after = connection.introspection.get_table_description(cursor, ClientBalanceAdjustment._meta.db_table)
        self.assertEqual(columns_after, columns_before)
