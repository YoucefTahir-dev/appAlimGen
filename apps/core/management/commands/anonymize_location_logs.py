"""Minimise legacy GPS actions, never delete the audit record or business data."""
import json
import re
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditLog


LEGACY = re.compile(r'^client\.location\.updated id=(\d+) old=')
CONFIRMATION = 'ANONYMISER GPS 90 JOURS'


class Command(BaseCommand):
    help = 'Dry-run by default. Anonymise only legacy location actions older than 90 days.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--confirm')
        parser.add_argument('--limit', type=int, default=1000)

    def handle(self, *args, **options):
        if options['apply'] and options['confirm'] != CONFIRMATION:
            raise CommandError('Explicit GPS anonymisation confirmation required.')
        limit = options['limit']
        if not 1 <= limit <= 10000:
            raise CommandError('Limit must be between 1 and 10000.')
        cutoff = timezone.now() - timedelta(days=90)
        candidates = AuditLog.objects.filter(
            created_at__lt=cutoff, action__startswith='client.location.updated id=',
            action__contains=' old=',
        ).order_by('pk')
        changed, unrecognised, examined = 0, 0, 0
        # A bounded window; repeat explicitly if more rows remain. Never print
        # actions, coordinates, paths, user names, or identifiers to the console.
        with transaction.atomic():
            rows = candidates.select_for_update() if options['apply'] else candidates
            for row in rows[:limit]:
                examined += 1
                match = LEGACY.match(row.action)
                if not match:
                    unrecognised += 1
                    continue
                changed += 1
                if options['apply']:
                    AuditLog.objects.filter(pk=row.pk, action=row.action).update(
                        action=f'client.location.updated id={match[1]} change=legacy_redacted',
                    )
        self.stdout.write(json.dumps({
            'mode': 'apply' if options['apply'] else 'dry_run',
            'retention_days': 90, 'examined': examined,
            'eligible': changed, 'updated': changed if options['apply'] else 0,
            'unrecognised': unrecognised, 'limit_reached': examined == limit,
            'audit_records_deleted': False, 'business_locations_modified': False,
            'console_history_and_backups_processed': False,
        }, sort_keys=True))
