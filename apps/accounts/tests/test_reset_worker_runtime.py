"""Worker preflight/lifecycle; synthetic database and providers only."""
import io
import json
from pathlib import Path
import signal
from unittest.mock import Mock, patch

from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings

from apps.accounts.management.commands.process_password_resets import Command
from apps.accounts.services.reset_outbox import enqueue_reset


@override_settings(PASSWORD_RESET_ASYNC_ENABLED=True,
                   PASSWORD_RESET_PUBLIC_BASE_URL='https://erp.example.test')
class ResetWorkerRuntimeTests(TestCase):
    def test_check_only_does_not_dispatch_or_change_pending_request(self):
        enqueue_reset('synthetic@example.invalid')
        with patch('apps.accounts.management.commands.process_password_resets.process_one') as process:
            output = io.StringIO()
            call_command('process_password_resets', check_only=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {
            'status': 'ready', 'provider_contacted': False, 'queue_modified': False,
        })
        process.assert_not_called()
        from apps.accounts.models import PasswordResetDispatch
        self.assertEqual(PasswordResetDispatch.objects.get().state, 'pending')

    @override_settings(PASSWORD_RESET_ASYNC_ENABLED=False)
    def test_disabled_is_refused_before_reading_queue(self):
        with self.assertNumQueries(0), self.assertRaises(CommandError):
            call_command('process_password_resets', check_only=True)

    def test_invalid_public_origin_is_refused_without_details(self):
        for origin in ('http://erp.example.test', 'https://u:private@erp.example.test',
                       'https://erp.example.test/path', 'https://erp.example.test/?private=token'):
            with self.subTest(origin=origin), override_settings(PASSWORD_RESET_PUBLIC_BASE_URL=origin):
                with self.assertNumQueries(0), self.assertRaises(CommandError) as caught:
                    call_command('process_password_resets', check_only=True)
                self.assertNotIn(origin, str(caught.exception))

    def test_crypto_and_database_errors_are_redacted(self):
        for exception in (ImportError('synthetic-private'), DatabaseError('synthetic-private')):
            with patch('apps.accounts.management.commands.process_password_resets.cipher', side_effect=exception), \
                    self.assertRaises(CommandError) as caught:
                call_command('process_password_resets', check_only=True)
            self.assertNotIn('synthetic-private', str(caught.exception))

    def test_readiness_marker_is_written_after_preflight_and_removed_on_exit(self):
        command = Command()
        options = {'once': False, 'limit': 10, 'poll_seconds': 15, 'ready_file': '/synthetic/ready'}
        with patch('apps.accounts.management.commands.process_password_resets.signal.signal') as handler, \
                patch.object(Path, 'write_text') as write, patch.object(Path, 'unlink') as remove, \
                patch.object(command, 'work_loop') as loop:
            command.run_worker(options)
        write.assert_called_once_with('ready\n', encoding='ascii')
        loop.assert_called_once()
        remove.assert_called_once_with(missing_ok=True)
        self.assertEqual(handler.call_count, 4)  # Install two, restore two.

    def test_preflight_failure_never_creates_marker(self):
        with patch('apps.accounts.management.commands.process_password_resets.cipher', side_effect=ImportError), \
                patch.object(Path, 'write_text') as write, self.assertRaises(CommandError):
            call_command('process_password_resets', once=True, ready_file='/synthetic/ready')
        write.assert_not_called()

    def test_signal_during_item_finishes_item_and_does_not_claim_next(self):
        command, handlers = Command(), {}
        options = {'once': False, 'limit': 10, 'poll_seconds': 15}

        def install(signum, handler):
            previous = handlers.get(signum)
            handlers[signum] = handler
            return previous

        def process():
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return True

        with patch('apps.accounts.management.commands.process_password_resets.signal.signal', side_effect=install), \
                patch('apps.accounts.management.commands.process_password_resets.close_old_connections'), \
                patch('apps.accounts.management.commands.process_password_resets.process_one', side_effect=process) as item:
            command.stdout = io.StringIO()
            command.run_worker(options)
        self.assertEqual(item.call_count, 1)
        self.assertEqual(json.loads(command.stdout.getvalue())['processed'], 1)
