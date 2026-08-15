import os
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.backup import (
    BackupError,
    backup_database_to,
    restore_database_from,
    verify_database_backup,
)


class Command(BaseCommand):
    help = 'Restaure une sauvegarde vérifiée après création automatique d’une copie de sécurité.'

    def add_arguments(self, parser):
        parser.add_argument('backup')
        parser.add_argument('--confirm-restore', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_restore']:
            raise CommandError('Ajoutez --confirm-restore pour confirmer cette opération destructive.')
        if not settings.DEBUG and os.getenv('ALLOW_DATABASE_RESTORE', '').lower() not in {'1', 'true', 'yes'}:
            raise CommandError('Définissez temporairement ALLOW_DATABASE_RESTORE=True en production.')

        extension = '.sqlite3' if connection.vendor == 'sqlite' else '.dump'
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_dir = Path(os.getenv('BACKUP_DIR', settings.BASE_DIR / 'backups'))
        safety_backup = (backup_dir / f'pre-restore-{timestamp}{extension}').resolve()

        try:
            verify_database_backup(options['backup'], expected_vendor=connection.vendor)
            backup_database_to(safety_backup)
            restore_database_from(options['backup'])
        except (BackupError, OSError) as exc:
            safety_copy = (
                f' Copie de sécurité : {safety_backup}.'
                if safety_backup.is_file()
                and safety_backup.with_name(f'{safety_backup.name}.sha256').is_file()
                else ''
            )
            raise CommandError(
                f'Restauration interrompue.{safety_copy} Erreur : {exc}'
            ) from exc

        self.stdout.write(self.style.SUCCESS(f'Restauration terminée depuis : {options["backup"]}'))
        self.stdout.write(self.style.WARNING(f'Copie avant restauration : {safety_backup}'))
