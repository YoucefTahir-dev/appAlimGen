"""Synthetic recipients only; no network/provider/production connection."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, connection, connections, DatabaseError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import PasswordResetDispatch
from apps.accounts.services import PasswordResetService, PasswordResetDeliveryError
from apps.accounts.services.reset_outbox import enqueue_reset, process_one

SETTINGS = dict(PASSWORD_RESET_ASYNC_ENABLED=True,
                PASSWORD_RESET_PUBLIC_BASE_URL='https://erp.example.test',
                PASSWORD_RESET_LIMIT=50, PASSWORD_RESET_IP_LIMIT=100)


@override_settings(**SETTINGS)
class ResetOutboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='queued-synthetic', email='queued@example.invalid', password='Synthetic-2040!',
        )
        get_user_model().objects.create_user(
            username='queued-inactive', email='inactive@example.invalid', is_active=False,
        )

    def test_public_web_api_never_lookup_accounts_or_wait_for_email(self):
        with patch('apps.accounts.services.password_reset.users_with_normalized_email', side_effect=AssertionError('public lookup')), \
                patch('apps.accounts.services.password_reset.DjangoEmailDeliveryService.send', side_effect=AssertionError('public mail')):
            web, api = [], []
            for email in (self.user.email, 'absent@example.invalid', 'inactive@example.invalid'):
                r = self.client.post(reverse('password_reset'), {'email': email})
                web.append((r.status_code, r.get('Location'), r.content))
                r = APIClient().post(reverse('api-password-reset-request'), {'email': email}, format='json')
                api.append((r.status_code, r.json()))
        self.assertTrue(all(item == web[0] for item in web))
        self.assertTrue(all(item == api[0] for item in api))
        self.assertEqual(web[0][0], 302)
        self.assertEqual(api[0][0], 200)
        self.assertEqual(PasswordResetDispatch.objects.count(), 6)
        for item in PasswordResetDispatch.objects.all():
            self.assertNotIn('@', item.encrypted_email)
            self.assertNotIn('example.invalid', item.encrypted_email)

    def test_worker_sends_only_eligible_user_and_erases_recipient_afterwards(self):
        service = PasswordResetService(delivery=Mock())
        for email in (self.user.email, 'absent@example.invalid', 'inactive@example.invalid'):
            enqueue_reset(email)
        for _ in range(3):
            self.assertTrue(process_one(service=service))
        self.assertFalse(process_one(service=service))
        self.assertEqual(service.delivery.deliver.call_count, 1)
        self.assertEqual(PasswordResetDispatch.objects.filter(state='sent', encrypted_email='').count(), 3)
        self.assertTrue(service.delivery.deliver.call_args.kwargs['reset_url'].startswith('https://erp.example.test/'))

    def test_pending_item_survives_new_service_and_rechecks_deactivated_user(self):
        enqueue_reset(self.user.email)
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        delivery = Mock()
        self.assertTrue(process_one(service=PasswordResetService(delivery=delivery)))
        delivery.deliver.assert_not_called()

    def test_failure_retries_with_bound_then_wipes_ciphertext(self):
        enqueue_reset(self.user.email)
        service = Mock()
        service.dispatch_request.side_effect = PasswordResetDeliveryError('sensitive-provider-detail')
        with self.assertLogs('security', level='ERROR') as logs:
            for attempt in range(3):
                PasswordResetDispatch.objects.update(available_at=timezone.now() - timedelta(seconds=1))
                self.assertTrue(process_one(service=service))
        item = PasswordResetDispatch.objects.get()
        self.assertEqual((item.state, item.attempts, item.encrypted_email), ('failed', 3, ''))
        self.assertNotIn('sensitive-provider-detail', str(logs.output))

    def test_expired_and_exhausted_lease_recovery_never_send(self):
        service = Mock()
        for exhausted in (False, True):
            enqueue_reset(self.user.email)
            item = PasswordResetDispatch.objects.latest('pk')
            PasswordResetDispatch.objects.filter(pk=item.pk).update(
                state='processing', lease_until=timezone.now() - timedelta(seconds=1),
                expires_at=timezone.now() + timedelta(seconds=20 if exhausted else -1),
                attempts=3 if exhausted else 0,
            )
            self.assertTrue(process_one(service=service))
            item.refresh_from_db()
            self.assertEqual(item.state, 'failed' if exhausted else 'expired')
            self.assertEqual(item.encrypted_email, '')
        service.dispatch_request.assert_not_called()

    def test_live_lease_is_not_stolen_but_abandoned_lease_is_retried(self):
        enqueue_reset(self.user.email)
        PasswordResetDispatch.objects.update(state='processing', lease_until=timezone.now() + timedelta(minutes=1))
        self.assertFalse(process_one(service=Mock()))
        PasswordResetDispatch.objects.update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertTrue(process_one(service=Mock()))

    def test_queue_failure_is_wrapped_without_raw_exception(self):
        with patch.object(PasswordResetDispatch.objects, 'create', side_effect=DatabaseError('private sentinel')):
            with self.assertRaises(PasswordResetDeliveryError) as caught:
                enqueue_reset(self.user.email)
        self.assertNotIn('private sentinel', str(caught.exception))

    def test_command_once_and_technical_retention(self):
        PasswordResetDispatch.objects.create(state='sent', encrypted_email='',
            created_at=timezone.now() - timedelta(days=8), expires_at=timezone.now())
        output = io.StringIO()
        # Management workers normally run outside request/TestCase transactions.
        # Do not close the TestCase-owned SQLite transaction in this unit test.
        with patch('apps.accounts.management.commands.process_password_resets.close_old_connections'):
            call_command('process_password_resets', once=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['processed'], 0)
        self.assertEqual(PasswordResetDispatch.objects.count(), 0)

    def test_queue_health_detects_unsupervised_or_failed_delivery(self):
        enqueue_reset(self.user.email)
        PasswordResetDispatch.objects.update(created_at=timezone.now() - timedelta(minutes=3))
        output = io.StringIO()
        from django.core.management import CommandError
        with self.assertRaises(CommandError):
            call_command('check_password_reset_queue', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['overdue'], 1)

    @override_settings(PASSWORD_RESET_ASYNC_ENABLED=False)
    def test_disabled_health_check_does_not_query_missing_outbox(self):
        output = io.StringIO()
        with self.assertNumQueries(0):
            call_command('check_password_reset_queue', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['status'], 'disabled')

    @override_settings(PASSWORD_RESET_ASYNC_ENABLED=False)
    def test_flag_off_preserves_existing_synchronous_contract(self):
        delivery = Mock()
        self.assertEqual(PasswordResetService(delivery=delivery).request(self.user.email), 1)
        self.assertEqual(delivery.deliver.call_count, 1)
        self.assertFalse(PasswordResetDispatch.objects.exists())


@override_settings(**SETTINGS)
class ResetOutboxWorkerCommandTests(TransactionTestCase):
    def test_real_command_outside_request_transaction_drains_durable_request(self):
        enqueue_reset('absent-command@example.invalid')
        output = io.StringIO()
        # No account exists: the real dispatcher must do no provider I/O.
        with patch('apps.accounts.services.password_reset.DjangoEmailDeliveryService.send',
                   side_effect=AssertionError('Unexpected provider call')):
            call_command('process_password_resets', once=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['processed'], 1)
        item = PasswordResetDispatch.objects.get()
        self.assertEqual((item.state, item.encrypted_email), ('sent', ''))


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL claim contention.')
@override_settings(**SETTINGS)
class ResetOutboxClaimTests(TransactionTestCase):
    def test_two_workers_cannot_deliver_same_live_claim(self):
        enqueue_reset('synthetic@example.invalid')
        gate = Barrier(2)
        service = Mock()

        def worker():
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return process_one(service=service)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(service.dispatch_request.call_count, 1)
        self.assertEqual(PasswordResetDispatch.objects.get().attempts, 1)
