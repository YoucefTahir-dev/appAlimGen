import json
import signal
from datetime import timedelta
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, DatabaseError
from django.utils import timezone

from apps.accounts.models import PasswordResetDispatch
from apps.accounts.services.reset_outbox import cipher, process_one


class Command(BaseCommand):
    help = 'Supervised durable reset worker. Requires explicit async activation.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--poll-seconds', type=int, default=5)
        parser.add_argument('--check-only', action='store_true')
        parser.add_argument('--ready-file')

    def handle(self, *args, **options):
        try:
            return self.run_worker(options)
        except DatabaseError:
            raise CommandError('Reset worker database unavailable; connection details withheld.') from None

    def run_worker(self, options):
        if not settings.PASSWORD_RESET_ASYNC_ENABLED or not settings.PASSWORD_RESET_PUBLIC_BASE_URL:
            raise CommandError('Async reset and trusted public base URL must be configured first.')
        if not 1 <= options['limit'] <= 1000 or not 1 <= options['poll_seconds'] <= 60:
            raise CommandError('Invalid worker limits.')
        # Never announce readiness with a missing crypto dependency/table. No
        # recipient or business row is read and this does not contact Brevo.
        try:
            url = urlsplit(settings.PASSWORD_RESET_PUBLIC_BASE_URL)
            if (url.scheme != 'https' or not url.hostname or url.username or
                    url.password or url.query or url.fragment or url.path not in ('', '/')):
                raise ValueError('Invalid public origin')
            token = cipher().encrypt(b'reset-worker-preflight')
            if cipher().decrypt(token) != b'reset-worker-preflight':
                raise ValueError('Invalid cipher')
            list(PasswordResetDispatch.objects.values_list('pk', flat=True)[:1])
        except Exception:
            raise CommandError('Reset worker preflight failed; configuration details withheld.') from None
        if options.get('check_only'):
            self.stdout.write(json.dumps({'status': 'ready', 'provider_contacted': False,
                                          'queue_modified': False}))
            return

        stop = Event()
        old_handlers = {}
        # --once remains callable from management tests/other threads. A real
        # supervised loop finishes its current provider call before stopping.
        if not options['once']:
            for signum in (signal.SIGTERM, signal.SIGINT):
                old_handlers[signum] = signal.signal(signum, lambda *_: stop.set())
        try:
            if options.get('ready_file'):
                Path(options['ready_file']).write_text('ready\n', encoding='ascii')
            self.work_loop(options, stop)
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
            if options.get('ready_file'):
                Path(options['ready_file']).unlink(missing_ok=True)

    def work_loop(self, options, stop):
        while not stop.is_set():
            close_old_connections()
            processed = 0
            for _ in range(options['limit']):
                if stop.is_set():
                    break
                if not process_one():
                    break
                processed += 1
            # Terminal rows contain no recipient; bounded technical retention.
            stale = list(PasswordResetDispatch.objects.filter(
                state__in=('sent', 'expired', 'failed'), created_at__lt=timezone.now() - timedelta(days=7),
            ).values_list('pk', flat=True)[:1000])
            PasswordResetDispatch.objects.filter(pk__in=stale).delete()
            self.stdout.write(json.dumps({'processed': processed, 'scope': 'reset_worker',
                                          'failed': PasswordResetDispatch.objects.filter(state='failed').count(),
                                          'pending': PasswordResetDispatch.objects.filter(state='pending').count()}))
            if options.get('ready_file'):
                Path(options['ready_file']).touch()
            if options['once']:
                return
            stop.wait(options['poll_seconds'])
