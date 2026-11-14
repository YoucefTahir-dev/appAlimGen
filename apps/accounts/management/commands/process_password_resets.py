import json
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, DatabaseError
from django.utils import timezone

from apps.accounts.models import PasswordResetDispatch
from apps.accounts.services.reset_outbox import process_one


class Command(BaseCommand):
    help = 'Supervised durable reset worker. Requires explicit async activation.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--poll-seconds', type=int, default=5)

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
        while True:
            close_old_connections()
            processed = 0
            for _ in range(options['limit']):
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
            if options['once']:
                return
            time.sleep(options['poll_seconds'])
