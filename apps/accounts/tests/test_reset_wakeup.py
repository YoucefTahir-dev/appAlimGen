"""Ephemeral local hints, durable SQL and a zero-query idle worker."""
import io
import os
from pathlib import Path
import tempfile
from datetime import timedelta
from itertools import chain, repeat
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.management.commands.process_password_resets import Command
from apps.accounts.models import PasswordResetDispatch
from apps.accounts.services.password_reset import PasswordResetDeliveryError
from apps.accounts.services.reset_outbox import enqueue_reset, process_one
from apps.accounts.services.reset_wakeup import markers_present, notify_reset, prune_markers


@override_settings(PASSWORD_RESET_ASYNC_ENABLED=True,
                   PASSWORD_RESET_PUBLIC_BASE_URL='https://erp.example.test')
class ResetWakeupTests(TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory(prefix='synthetic-reset-wakes-'))
        self.enterContext(override_settings(PASSWORD_RESET_WAKE_DIRECTORY=self.folder))

    def test_hint_has_only_technical_id_and_recipient_stays_encrypted(self):
        enqueue_reset('synthetic-wake@example.invalid')
        row = PasswordResetDispatch.objects.get()
        files = list(Path(self.folder).iterdir())
        self.assertEqual([p.name for p in files], [str(row.pk)])
        self.assertEqual(files[0].read_bytes(), b'')
        self.assertNotIn('synthetic-wake', row.encrypted_email)
        self.assertTrue(markers_present())

    def test_notification_failure_rolls_back_insertion_and_redacts_error(self):
        with patch('apps.accounts.services.reset_outbox.notify_reset', side_effect=OSError('private sentinel')), \
                self.assertRaises(PasswordResetDeliveryError) as caught:
            enqueue_reset('synthetic-wake@example.invalid')
        self.assertEqual(PasswordResetDispatch.objects.count(), 0)
        self.assertNotIn('private sentinel', str(caught.exception))

    def test_notification_is_inside_outer_commit_boundary(self):
        def observe(pk):
            self.assertTrue(connection.in_atomic_block)
            self.assertTrue(PasswordResetDispatch.objects.filter(pk=pk).exists())
            notify_reset(pk)
        with patch('apps.accounts.services.reset_outbox.notify_reset', side_effect=observe):
            enqueue_reset('synthetic-wake@example.invalid')

    def test_unsupported_nested_transaction_fails_without_admitting_request(self):
        with transaction.atomic(), self.assertRaises(PasswordResetDeliveryError):
            enqueue_reset('synthetic-wake@example.invalid')
        self.assertEqual(PasswordResetDispatch.objects.count(), 0)

    def test_precommit_hint_survives_until_commit_or_ten_minute_rollback_cleanup(self):
        path = Path(self.folder) / '999'
        path.touch()
        prune_markers()
        self.assertTrue(path.exists())
        os.utime(path, (0, 0))
        prune_markers()
        self.assertFalse(path.exists())

    def test_pending_hint_retained_but_terminal_hint_removed(self):
        enqueue_reset('synthetic-wake@example.invalid')
        row = PasswordResetDispatch.objects.get()
        path = Path(self.folder) / str(row.pk)
        os.utime(path, (0, 0))
        prune_markers()
        self.assertTrue(path.exists())
        PasswordResetDispatch.objects.update(state='failed', encrypted_email='')
        prune_markers()
        self.assertFalse(path.exists())

    def test_no_numeric_hint_does_not_query_database(self):
        (Path(self.folder) / 'not-a-dispatch').touch()
        with self.assertNumQueries(0):
            self.assertFalse(markers_present())
            prune_markers()

    def test_idle_after_startup_grace_is_zero_database_queries(self):
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch('apps.accounts.management.commands.process_password_resets.time.monotonic',
                   side_effect=[0, 0, 601, 601]), \
                patch('apps.accounts.management.commands.process_password_resets.close_old_connections'), \
                patch.object(connections, 'close_all') as close, self.assertNumQueries(0):
            Command().work_loop({'limit': 10, 'poll_seconds': 15, 'once': False}, stop)
        close.assert_called_once()
        stop.wait.assert_called_once_with(15)

    def test_hint_wakes_worker_after_startup_grace_and_is_cleared_on_completion(self):
        enqueue_reset('absent-wake@example.invalid')
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        command = Command()
        command.stdout = io.StringIO()
        with patch('apps.accounts.management.commands.process_password_resets.time.monotonic',
                   return_value=1000), \
                patch('apps.accounts.management.commands.process_password_resets.close_old_connections'), \
                patch.object(connections, 'close_all'), \
                patch('apps.accounts.services.password_reset.DjangoEmailDeliveryService.send',
                      side_effect=AssertionError('No real provider allowed')):
            # Expire the startup grace by returning 0 for initialization only.
            with patch('apps.accounts.management.commands.process_password_resets.time.monotonic',
                       side_effect=[0, 0, 1000, 1000, 1000]):
                command.work_loop({'limit': 1, 'poll_seconds': 15, 'once': False}, stop)
        self.assertEqual(PasswordResetDispatch.objects.get().state, 'sent')
        self.assertFalse(markers_present())

    def test_missing_directory_does_not_commit_a_request(self):
        with override_settings(PASSWORD_RESET_WAKE_DIRECTORY=str(Path(self.folder) / 'missing')), \
                self.assertRaises(PasswordResetDeliveryError):
            enqueue_reset('synthetic-wake@example.invalid')
        self.assertEqual(PasswordResetDispatch.objects.count(), 0)

    def test_old_technical_backlog_is_drained_in_bounded_batches_before_idle(self):
        old = timezone.now() - timedelta(days=8)
        PasswordResetDispatch.objects.bulk_create([
            PasswordResetDispatch(state='sent', encrypted_email='', created_at=old, expires_at=old)
            for _ in range(1001)
        ])
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, False, True]
        command = Command()
        command.stdout = io.StringIO()
        with patch('apps.accounts.management.commands.process_password_resets.time.monotonic',
                   side_effect=chain([0, 0], repeat(1000))), \
                patch('apps.accounts.management.commands.process_password_resets.close_old_connections'), \
                patch.object(connections, 'close_all'):
            # Force the first daily maintenance pass without a wake hint. Its
            # full batch must keep the loop active for the remaining one row.
            with patch('apps.accounts.management.commands.process_password_resets.markers_present',
                       side_effect=[True]):
                command.work_loop({'limit': 1, 'poll_seconds': 15, 'once': False}, stop)
        self.assertEqual(PasswordResetDispatch.objects.count(), 0)


@skipUnless(connection.vendor == 'postgresql', 'Requires real precommit visibility isolation.')
@override_settings(PASSWORD_RESET_ASYNC_ENABLED=True,
                   PASSWORD_RESET_PUBLIC_BASE_URL='https://erp.example.test')
class ResetWakeupCommitRaceTests(TransactionTestCase):
    def test_worker_keeps_hint_for_not_yet_committed_row(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-reset-race-') as folder, \
                override_settings(PASSWORD_RESET_WAKE_DIRECTORY=folder):
            hint_written, commit_allowed = Event(), Event()

            def stage_hint(pk):
                notify_reset(pk)
                hint_written.set()
                if not commit_allowed.wait(10):
                    raise OSError('Synthetic commit gate timed out')

            def admission():
                close_old_connections()
                try:
                    enqueue_reset('absent-race@example.invalid')
                finally:
                    connections.close_all()

            with patch('apps.accounts.services.reset_outbox.notify_reset', side_effect=stage_hint), \
                    ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(admission)
                try:
                    self.assertTrue(hint_written.wait(10))
                    self.assertTrue(markers_present())
                    self.assertFalse(process_one(service=Mock()))
                    prune_markers()
                    self.assertTrue(markers_present())
                finally:
                    commit_allowed.set()
                future.result(timeout=10)
            self.assertTrue(process_one(service=Mock()))
            prune_markers()
            self.assertFalse(markers_present())
