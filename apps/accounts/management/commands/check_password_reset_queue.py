import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

from apps.accounts.models import PasswordResetDispatch


class Command(BaseCommand):
    help = 'Read-only reset queue health. Nonzero exit if failures or dispatch delay exceed two minutes.'

    def handle(self, *args, **options):
        if not settings.PASSWORD_RESET_ASYNC_ENABLED:
            self.stdout.write(json.dumps({'status': 'disabled', 'queue_checked': False}))
            return
        try:
            now = timezone.now()
            queue = PasswordResetDispatch.objects
            failed = queue.filter(state='failed').count()
            overdue = queue.filter(state__in=('pending', 'processing'), created_at__lt=now - timedelta(minutes=2)).count()
        except DatabaseError:
            raise CommandError('Reset queue health unavailable; connection details withheld.') from None
        self.stdout.write(json.dumps({'status': 'attention' if failed or overdue else 'ok',
            'failed': failed, 'overdue': overdue, 'queue_checked': True, 'alert_delivery_verified': False}))
        if failed or overdue:
            raise CommandError('Reset queue needs operator attention.')
