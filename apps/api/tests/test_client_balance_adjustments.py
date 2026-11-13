"""API debt corrections use synthetic clients only; no deployment resources."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.api.models import IdempotencyRecord
from apps.api.serializers import ClientSerializer
from apps.commerce.models import OpeningBalancePayment, Payment
from apps.core.models import AuditLog
from apps.inventory.models import Client, ClientBalanceAdjustment


@override_settings(CLIENT_BALANCE_CORRECTIONS_ENABLED=True)
class ClientBalanceApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='debt-api-admin', password='unused')
        cls.writer = User.objects.create_user(username='debt-api-writer', password='unused')
        cls.group = Group.objects.create(name='Synthetic client profile writers')
        cls.writer.groups.add(cls.group)
        cls.group.permissions.add(*Permission.objects.filter(
            content_type__app_label='inventory',
            codename__in=('view_client', 'add_client', 'change_client', 'delete_client'),
        ))
        cls.customer = Client.objects.create(name='Synthetic client', balance='100.00')

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def url(self, action='adjust-balance', customer=None):
        return reverse(f'api-client-{action}', args=[(customer or self.customer).pk])

    def correction(self, payload=None, key='synthetic-debt-correction'):
        return self.client.post(
            self.url(), payload or {
                'expected_balance': '100.00', 'new_balance': '80.00',
                'reason': 'Correction documentée du solde de reprise.',
            }, format='json', **({'HTTP_IDEMPOTENCY_KEY': key} if key is not None else {}),
        )

    def balance(self):
        self.customer.refresh_from_db()
        return self.customer.balance

    def test_ordinary_profile_permission_cannot_change_balance_or_read_history(self):
        self.client.force_authenticate(self.writer)
        response = self.client.patch(self.url('detail'), {'balance': '75.00'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.correction().status_code, 403)
        self.assertEqual(self.client.get(self.url('balance-adjustments')).status_code, 403)
        self.assertEqual(self.balance(), Decimal('100.00'))
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_equal_legacy_balance_is_accepted_without_financial_write(self):
        self.client.force_authenticate(self.writer)
        response = self.client.patch(
            self.url('detail'), {'phone': '0123456789', 'balance': '100.00'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.balance(), Decimal('100.00'))
        self.assertEqual(self.customer.phone, '0123456789')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_initial_nonzero_balance_is_journalled_without_new_correction_permission(self):
        self.client.force_authenticate(self.writer)
        response = self.client.post(
            reverse('api-client-list'), {'name': 'New synthetic debtor', 'balance': '42.50'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        entry = ClientBalanceAdjustment.objects.get(client_id=response.data['id'])
        self.assertEqual(entry.kind, ClientBalanceAdjustment.INITIAL)
        self.assertEqual(entry.balance_before, Decimal('0.00'))
        self.assertEqual(entry.balance_after, Decimal('42.50'))
        self.assertEqual(entry.created_by_id, self.writer.pk)

    def test_zero_initial_balance_has_no_fictitious_financial_change(self):
        response = self.client.post(reverse('api-client-list'), {'name': 'Zero client'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_correction_requires_stable_key_even_for_superuser(self):
        response = self.correction(key=None)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error']['code'], 'IDEMPOTENCY_KEY_REQUIRED')
        self.assertEqual(self.balance(), Decimal('100.00'))
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_same_key_replays_after_balance_changes_without_new_entry_or_audit(self):
        first = self.correction()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.balance(), Decimal('80.00'))
        first_audit = AuditLog.objects.filter(action='api.client.adjust_balance').count()
        OpeningBalancePayment.objects.create(
            client=self.customer, amount='5.00', payment_type=Payment.CASH, created_by=self.admin,
        )
        replay = self.correction()
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(replay.data, first.data)
        self.assertEqual(self.balance(), Decimal('75.00'))
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='api.client.adjust_balance').count(), first_audit)
        self.assertEqual(first_audit, 1)

    def test_changed_body_same_key_is_rejected(self):
        self.assertEqual(self.correction().status_code, 201)
        response = self.correction({
            'expected_balance': '80.00', 'new_balance': '60.00', 'reason': 'Another correction',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error']['code'], 'IDEMPOTENCY_KEY_REUSED')
        self.assertEqual(self.balance(), Decimal('80.00'))
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_stale_balance_and_noop_leave_no_reservation(self):
        for expected, target in [('90.00', '80.00'), ('100.00', '100.00')]:
            with self.subTest(expected=expected, target=target):
                response = self.correction({
                    'expected_balance': expected, 'new_balance': target, 'reason': 'Synthetic reason',
                })
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(IdempotencyRecord.objects.exists())
                self.assertFalse(ClientBalanceAdjustment.objects.exists())
                self.assertEqual(self.balance(), Decimal('100.00'))

    def test_blank_reason_and_invalid_money_rejected_without_effect(self):
        for target, reason in [('80.00', ' '), ('NaN', 'Reason'), ('1.001', 'Reason')]:
            with self.subTest(target=target, reason=reason):
                response = self.correction({
                    'expected_balance': '100.00', 'new_balance': target, 'reason': reason,
                })
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.balance(), Decimal('100.00'))
        self.assertFalse(ClientBalanceAdjustment.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_explicit_permission_can_correct_but_revocation_blocks_cached_replay(self):
        permission = Permission.objects.get(
            content_type__app_label='inventory', codename='adjust_client_balance',
        )
        self.group.permissions.add(permission)
        self.client.force_authenticate(User.objects.get(pk=self.writer.pk))
        first = self.correction()
        self.assertEqual(first.status_code, 201, first.data)
        self.writer.denied_permissions.add(permission)
        self.client.force_authenticate(User.objects.get(pk=self.writer.pk))
        response = self.correction()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.balance(), Decimal('80.00'))
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_history_is_immutable_and_filtered_to_target_client(self):
        self.assertEqual(self.correction().status_code, 201)
        other = Client.objects.create(name='Unrelated synthetic debtor')
        response = self.client.get(self.url('balance-adjustments'))
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get('results', response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['client'], self.customer.pk)
        other_response = self.client.get(self.url('balance-adjustments', other))
        other_rows = other_response.data.get('results', other_response.data) if isinstance(other_response.data, dict) else other_response.data
        self.assertEqual(other_rows, [])
        # Unknown actions are rejected by the API's default-closed permission
        # guard before DRF's method handler (403, rather than 405).
        self.assertEqual(self.client.put(self.url('balance-adjustments'), {}, format='json').status_code, 403)
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assertEqual(self.balance(), Decimal('80.00'))

    def test_journal_failure_rolls_back_balance_registry_and_success_audit(self):
        with patch('apps.inventory.balance_adjustments._record', side_effect=RuntimeError('synthetic journal failure')):
            with self.assertRaisesMessage(RuntimeError, 'synthetic journal failure'):
                self.correction()
        self.assertEqual(self.balance(), Decimal('100.00'))
        self.assertFalse(ClientBalanceAdjustment.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action='api.client.adjust_balance').exists())


class StaleClientSerializerTests(TestCase):
    def test_profile_serializer_does_not_overwrite_concurrent_receipt(self):
        user = User.objects.create_superuser(username='debt-stale-admin', password='unused')
        customer = Client.objects.create(name='Stale synthetic profile', balance=Decimal('100.00'))
        serializer = ClientSerializer(
            customer, data={'phone': '0123456789', 'balance': '100.00'}, partial=True,
            context={'request': SimpleNamespace(user=user)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        OpeningBalancePayment.objects.create(
            client_id=customer.pk, amount='20.00', payment_type=Payment.CASH, created_by=user,
        )
        self.assertEqual(customer.balance, Decimal('100.00'))
        result = serializer.save()
        self.assertEqual(result.balance, Decimal('80.00'))
        self.assertEqual(Client.objects.get(pk=customer.pk).balance, Decimal('80.00'))
        self.assertFalse(ClientBalanceAdjustment.objects.exists())
