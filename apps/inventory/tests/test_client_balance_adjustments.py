"""Opening-debt corrections are separate, journalled and never payments."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from apps.accounts.permissions import has_permission
from apps.api.models import IdempotencyRecord
from apps.commerce.models import OpeningBalancePayment, Payment
from apps.commerce.receivables import receivable_account, receivables_queryset
from apps.commerce.web_idempotency import new_web_token
from apps.inventory.admin import ClientAdminForm
from apps.inventory.balance_adjustments import (
    adjust_client_balance, create_client_with_balance_audit,
    save_client_profile, save_new_client_with_balance_audit,
)
from apps.inventory.forms import ClientForm
from apps.inventory.models import Client, ClientBalanceAdjustment


User = get_user_model()
CORRECT = 'inventory.adjust_client_balance'


class ClientBalanceAdjustmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser(username='opening-debt-owner', password='unused')
        cls.editor = User.objects.create_user(username='opening-debt-profile', role=User.SELLER)
        cls.editor.user_permissions.add(Permission.objects.get(codename='change_client', content_type__app_label='inventory'))
        cls.corrector = User.objects.create_user(username='opening-debt-finance', role=User.SELLER)
        cls.corrector.user_permissions.add(Permission.objects.get(codename='adjust_client_balance', content_type__app_label='inventory'))
        cls.customer = Client.objects.create(name='Historical opening debt', balance=Decimal('100.00'))

    def correct(self, **overrides):
        return adjust_client_balance(**{
            'client_id': self.customer.pk, 'expected_balance': '100.00',
            'new_balance': '120.10', 'reason': '  Pièce historique vérifiée  ',
            'user': self.corrector, **overrides,
        })

    def post_data(self, **overrides):
        return {
            'expected_balance': '100.00', 'new_balance': '120.10',
            'reason': 'Pièce historique vérifiée', 'request_token': new_web_token(),
            **overrides,
        }

    def url(self):
        return reverse('client_balance_adjust', args=[self.customer.pk])

    def assert_balance(self, value):
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal(value))

    def test_explicit_financial_right_updates_remaining_balance_and_one_append_only_entry(self):
        record = self.correct()
        self.assert_balance('120.10')
        self.assertEqual(record.kind, ClientBalanceAdjustment.CORRECTION)
        self.assertEqual(record.balance_before, Decimal('100.00'))
        self.assertEqual(record.balance_after, Decimal('120.10'))
        self.assertEqual(record.delta, Decimal('20.10'))
        self.assertEqual(record.reason, 'Pièce historique vérifiée')
        self.assertEqual(record.created_by_id, self.corrector.pk)
        self.assertEqual(record.actor_id_snapshot, self.corrector.pk)
        self.assertEqual(record.actor_name_snapshot, self.corrector.username)
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assertFalse(OpeningBalancePayment.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_ordinary_profile_permission_cannot_call_correction_service_or_direct_url(self):
        with self.assertRaises(PermissionDenied):
            self.correct(user=self.editor)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.post_data()).status_code, 403)
        self.assert_balance('100.00')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_legacy_administrator_and_manager_do_not_gain_financial_permission(self):
        for role in (User.ADMIN, User.MANAGER, User.SELLER):
            with self.subTest(role=role):
                user = User.objects.create_user(username=f'debt-legacy-{role}', role=role)
                self.assertFalse(has_permission(user, CORRECT))
                with self.assertRaises(PermissionDenied):
                    self.correct(user=user)
        self.assertTrue(has_permission(self.owner, CORRECT))

    def test_explicit_denial_takes_precedence_over_individual_grant(self):
        self.corrector.denied_permissions.add(Permission.objects.get(codename='adjust_client_balance', content_type__app_label='inventory'))
        fresh = User.objects.get(pk=self.corrector.pk)
        self.assertFalse(has_permission(fresh, CORRECT))
        with self.assertRaises(PermissionDenied):
            self.correct(user=fresh)
        self.assert_balance('100.00')

    def test_signed_values_and_decimal_precision_are_preserved_without_new_credit_policy(self):
        record = self.correct(new_balance='-0.10')
        self.assertEqual(record.delta, Decimal('-100.10'))
        self.assert_balance('-0.10')
        record = self.correct(expected_balance='-0.10', new_balance='9999999999.99')
        self.assertEqual(record.delta, Decimal('10000000000.09'))
        self.assert_balance('9999999999.99')

    def test_invalid_amounts_reason_and_noop_never_write_or_journal(self):
        cases = (
            {'new_balance': 'NaN'}, {'new_balance': 'Infinity'}, {'new_balance': '0.001'},
            {'new_balance': '10000000000.00'}, {'new_balance': 'not-a-number'},
            {'expected_balance': 'NaN'}, {'reason': '   '}, {'reason': 'x' * 1001},
            {'new_balance': '100.00'},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.correct(**overrides)
                self.assert_balance('100.00')
                self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_stale_expected_balance_refuses_after_payment_without_changing_payment(self):
        payment = OpeningBalancePayment.objects.create(
            client=self.customer, amount='25.00', payment_type=Payment.CASH, created_by=self.owner,
        )
        with self.assertRaises(ValidationError) as raised:
            self.correct()
        self.assertIn('expected_balance', raised.exception.message_dict)
        self.assert_balance('75.00')
        self.assertEqual(OpeningBalancePayment.objects.count(), 1)
        self.assertFalse(payment.is_cancelled)
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_journal_failure_rolls_back_the_balance_change(self):
        with patch('apps.inventory.balance_adjustments._record', side_effect=RuntimeError('synthetic journal failure')):
            with self.assertRaises(RuntimeError):
                self.correct()
        self.assert_balance('100.00')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_creation_keeps_add_client_policy_and_journals_nonzero_initial_debt_only(self):
        creator = User.objects.create_user(username='opening-debt-creator', role=User.SELLER)
        self.assertTrue(has_permission(creator, 'inventory.add_client'))
        self.assertFalse(has_permission(creator, CORRECT))
        zero = create_client_with_balance_audit(user=creator, name='New without debt')
        customer = create_client_with_balance_audit(user=creator, name='New with opening debt', balance='35.10')
        self.assertFalse(zero.balance_adjustments.exists())
        record = customer.balance_adjustments.get()
        self.assertEqual(record.kind, ClientBalanceAdjustment.INITIAL)
        self.assertEqual(record.balance_before, Decimal('0.00'))
        self.assertEqual(record.balance_after, Decimal('35.10'))
        self.assertEqual(record.delta, Decimal('35.10'))
        self.assertEqual(record.created_by_id, creator.pk)
        self.assertFalse(self.customer.balance_adjustments.exists())

    def test_initial_journal_failure_rolls_back_new_client(self):
        with patch('apps.inventory.balance_adjustments._record', side_effect=RuntimeError('synthetic initial journal failure')):
            with self.assertRaises(RuntimeError):
                create_client_with_balance_audit(user=self.owner, name='Must not survive', balance='30.00')
        self.assertFalse(Client.objects.filter(name='Must not survive').exists())

    def test_new_client_helper_refuses_existing_instance(self):
        with self.assertRaises(ValidationError):
            save_new_client_with_balance_audit(client=self.customer, user=self.owner)
        self.assert_balance('100.00')

    def test_profile_form_refuses_balance_change_and_accepts_same_legacy_value(self):
        data = {'name': 'Profile updated', 'balance': '1.00'}
        form = ClientForm(data, instance=Client.objects.get(pk=self.customer.pk))
        self.assertFalse(form.is_valid())
        self.assertIn('balance', form.errors)
        form = ClientForm({**data, 'balance': '100.00'}, instance=Client.objects.get(pk=self.customer.pk))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assert_balance('100.00')
        self.assertEqual(self.customer.name, 'Profile updated')

    def test_stale_profile_save_does_not_overwrite_concurrent_receipt(self):
        stale = Client.objects.get(pk=self.customer.pk)
        form = ClientForm({'name': 'Profile after receipt', 'balance': '100.00'}, instance=stale)
        self.assertTrue(form.is_valid(), form.errors)
        OpeningBalancePayment.objects.create(
            client=self.customer, amount='30.00', payment_type=Payment.CASH, created_by=self.owner,
        )
        form.save()
        self.assert_balance('70.00')
        self.assertEqual(self.customer.name, 'Profile after receipt')

    def test_profile_service_ignores_even_mutated_stale_balance(self):
        stale = Client.objects.get(pk=self.customer.pk)
        self.correct()
        stale.balance = Decimal('0.00')
        stale.phone = '0550000000'
        save_client_profile(client=stale)
        self.assert_balance('120.10')
        self.assertEqual(self.customer.phone, '0550000000')

    def test_web_profile_forgery_is_not_a_financial_correction(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('client_update', args=[self.customer.pk]), {
            'name': 'Forged financial change', 'balance': '0.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'balance', 'La dette antérieure ne peut pas être modifiée dans la fiche. Utilisez la correction dédiée.')
        self.assert_balance('100.00')
        self.assertEqual(self.customer.name, 'Historical opening debt')
        self.assertFalse(ClientBalanceAdjustment.objects.exists())

    def test_web_correction_retry_one_journal_then_modified_payload_rejected(self):
        self.client.force_login(self.corrector)
        page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context['form']['request_token'].value())
        data = self.post_data()
        first = self.client.post(self.url(), data)
        second = self.client.post(self.url(), data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second['Idempotency-Replayed'], 'true')
        self.assertEqual(first['Location'], second['Location'])
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assert_balance('120.10')
        modified = self.client.post(self.url(), {**data, 'new_balance': '0.00'})
        self.assertEqual(modified.status_code, 400)
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_web_missing_token_cannot_mutate_new_financial_workflow(self):
        self.client.force_login(self.corrector)
        for token in ('', 'not-signed'):
            response = self.client.post(self.url(), self.post_data(request_token=token))
            self.assertIn(response.status_code, (200, 400))
            self.assert_balance('100.00')
            self.assertFalse(ClientBalanceAdjustment.objects.exists())
            self.assertFalse(IdempotencyRecord.objects.exists())

    def test_web_validation_failure_releases_token_and_success_can_retry_it(self):
        self.client.force_login(self.corrector)
        data = self.post_data(reason='   ')
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IdempotencyRecord.objects.exists())
        response = self.client.post(self.url(), {**data, 'reason': 'Motif corrigé'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_web_replay_denied_after_right_revoked(self):
        self.client.force_login(self.corrector)
        data = self.post_data()
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.corrector.user_permissions.clear()
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assert_balance('120.10')

    def test_payment_correction_and_cancellation_do_not_double_count_in_accounts(self):
        receipt = OpeningBalancePayment.objects.create(
            client=self.customer, amount='25.00', payment_type=Payment.CASH, created_by=self.owner,
        )
        self.correct(expected_balance='75.00', new_balance='60.00')
        self.assert_balance('60.00')
        account = receivable_account(self.owner, self.customer)
        self.assertEqual(account['total_invoiced'], Decimal('85.00'))
        self.assertEqual(account['total_paid'], Decimal('25.00'))
        self.assertEqual(account['balance_due'], Decimal('60.00'))
        annotated = receivables_queryset(self.owner).get(pk=self.customer.pk)
        self.assertEqual(annotated.receivable_invoiced - annotated.receivable_paid, Decimal('60.00'))
        receipt.cancel(user=self.owner, reason='Erreur de saisie du règlement')
        self.assert_balance('85.00')
        account = receivable_account(self.owner, self.customer)
        self.assertEqual(account['total_invoiced'], Decimal('85.00'))
        self.assertEqual(account['total_paid'], Decimal('0.00'))
        self.assertEqual(account['balance_due'], Decimal('85.00'))
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_journal_mutation_delete_and_bulk_methods_are_refused(self):
        record = self.correct()
        for mutate in (
            lambda: record.save(), lambda: record.delete(),
            lambda: ClientBalanceAdjustment.objects.filter(pk=record.pk).update(reason='changed'),
            lambda: ClientBalanceAdjustment.objects.all().delete(),
            lambda: ClientBalanceAdjustment.objects.bulk_create([]),
            lambda: ClientBalanceAdjustment.objects.bulk_update([record], ['reason']),
        ):
            with self.subTest(mutate=mutate):
                with self.assertRaises(ValidationError):
                    mutate()
        with self.assertRaises(ValidationError):
            ClientBalanceAdjustment.objects.create(
                client=self.customer, kind=ClientBalanceAdjustment.CORRECTION,
                balance_before='120.10', balance_after='0.00', delta='-120.10',
                reason='Attempted bypass', created_by=self.owner,
                actor_id_snapshot=self.owner.pk, actor_name_snapshot=self.owner.username,
            )
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        self.assert_balance('120.10')

    def test_journal_actor_snapshot_survives_actor_deletion_and_client_is_protected(self):
        record = self.correct()
        actor_id, actor_name = self.corrector.pk, self.corrector.username
        self.corrector.delete()
        record.refresh_from_db()
        self.assertIsNone(record.created_by_id)
        self.assertEqual(record.actor_id_snapshot, actor_id)
        self.assertEqual(record.actor_name_snapshot, actor_name)
        with self.assertRaises(ProtectedError):
            self.customer.delete()
        self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)

    def test_admin_cannot_edit_balance_or_mutate_journal_even_for_superuser(self):
        request = RequestFactory().get('/admin/')
        request.user = self.owner
        client_admin = admin.site._registry[Client]
        self.assertIn('balance', client_admin.get_readonly_fields(request, self.customer))
        form = ClientAdminForm({'name': self.customer.name, 'balance': '0.00'}, instance=self.customer)
        self.assertFalse(form.is_valid())
        journal_admin = admin.site._registry[ClientBalanceAdjustment]
        self.assertFalse(journal_admin.has_add_permission(request))
        self.assertFalse(journal_admin.has_change_permission(request))
        self.assertFalse(journal_admin.has_delete_permission(request))

    def test_admin_creation_journals_and_stale_profile_save_excludes_balance(self):
        request = RequestFactory().post('/admin/')
        request.user = self.owner
        client_admin = admin.site._registry[Client]
        created = Client(name='Admin initial debt', balance=Decimal('12.35'))
        client_admin.save_model(request, created, None, False)
        self.assertEqual(created.balance_adjustments.get().balance_after, Decimal('12.35'))
        stale = Client.objects.get(pk=self.customer.pk)
        self.correct()
        stale.notes = 'Admin profile only'
        client_admin.save_model(request, stale, None, True)
        self.assert_balance('120.10')
        self.assertEqual(self.customer.notes, 'Admin profile only')


class PostgreSQLClientBalanceConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Independent row-lock concurrency requires PostgreSQL.')
        self.owner = User.objects.create_superuser(username='opening-debt-race', password='unused')
        self.customer = Client.objects.create(name='Synthetic debt race', balance=Decimal('100.00'))

    def test_simultaneous_corrections_on_distinct_connections_have_one_winner(self):
        barrier = Barrier(2)

        def update(target):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.owner.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=20)
                try:
                    record = adjust_client_balance(
                        client_id=self.customer.pk, expected_balance='100.00',
                        new_balance=target, reason='Independent synthetic race', user=user,
                    )
                    return backend_pid, 'saved', record.balance_after
                except ValidationError as exc:
                    return backend_pid, 'conflict', tuple(exc.message_dict)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, ('80.00', '120.00')))
        self.assertEqual(len({result[0] for result in results}), 2)
        self.assertEqual(sorted(result[1] for result in results), ['conflict', 'saved'])
        record = ClientBalanceAdjustment.objects.get()
        self.customer.refresh_from_db()
        self.assertEqual(record.balance_before, Decimal('100.00'))
        self.assertEqual(self.customer.balance, record.balance_after)

    def test_receipt_and_correction_share_client_lock_without_lost_money(self):
        barrier = Barrier(2)

        def mutate(kind):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.owner.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=20)
                try:
                    if kind == 'receipt':
                        OpeningBalancePayment.objects.create(
                            client_id=self.customer.pk, amount='25.00',
                            payment_type=Payment.CASH, created_by=user,
                        )
                    else:
                        adjust_client_balance(
                            client_id=self.customer.pk, expected_balance='100.00', new_balance='120.00',
                            reason='Synthetic correction with concurrent receipt', user=user,
                        )
                    return backend_pid, kind, 'saved'
                except ValidationError as exc:
                    return backend_pid, kind, tuple(exc.message_dict)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(mutate, ('receipt', 'correction')))
        self.assertEqual(len({result[0] for result in results}), 2)
        self.assertEqual(next(row[2] for row in results if row[1] == 'receipt'), 'saved')
        self.customer.refresh_from_db()
        self.assertEqual(OpeningBalancePayment.objects.count(), 1)
        if ClientBalanceAdjustment.objects.exists():
            self.assertEqual(self.customer.balance, Decimal('95.00'))
            self.assertEqual(ClientBalanceAdjustment.objects.count(), 1)
        else:
            self.assertEqual(self.customer.balance, Decimal('75.00'))
            self.assertEqual(next(row[2] for row in results if row[1] == 'correction'), ('expected_balance',))
