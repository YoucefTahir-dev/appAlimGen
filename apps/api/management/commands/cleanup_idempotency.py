from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.api.models import IdempotencyRecord


class Command(BaseCommand):
    help = 'Supprime les anciennes clés d’idempotence API.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=getattr(settings, 'API_IDEMPOTENCY_RETENTION_DAYS', 7))

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=max(options['days'], 1))
        deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f'{deleted} enregistrement(s) supprimé(s).'))
