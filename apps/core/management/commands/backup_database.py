import os
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.backup import BackupError, backup_database_to, upload_backup_to_object_storage


class Command(BaseCommand):
    help = 'Crée une sauvegarde cohérente SQLite ou PostgreSQL avec empreinte SHA-256.'

    def add_arguments(self, parser):
        parser.add_argument('--output')
        parser.add_argument('--overwrite', action='store_true')
        parser.add_argument('--upload', action='store_true')
        parser.add_argument('--delete-local-after-upload', action='store_true')

    def handle(self, *args, **options):
        extension = '.sqlite3' if connection.vendor == 'sqlite' else '.dump'
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_dir = Path(os.getenv('BACKUP_DIR', settings.BASE_DIR / 'backups'))
        output = Path(options['output'] or backup_dir / f'database-{timestamp}{extension}').resolve()
        checksum_output = output.with_name(f'{output.name}.sha256')
        if (output.exists() or checksum_output.exists()) and not options['overwrite']:
            raise CommandError(f'La sauvegarde ou son empreinte existe déjà : {output}')
        if options['delete_local_after_upload'] and not options['upload']:
            raise CommandError('--delete-local-after-upload exige --upload.')

        try:
            backup_database_to(output)
            remote_uri = upload_backup_to_object_storage(output) if options['upload'] else None
        except (BackupError, ImproperlyConfigured, OSError) as exc:
            local_copy = f' Copie locale conservée : {output}.' if output.is_file() else ''
            raise CommandError(f'{exc}{local_copy}') from exc

        self.stdout.write(self.style.SUCCESS(f'Sauvegarde validée : {output}'))
        self.stdout.write(self.style.SUCCESS(f'Empreinte : {output.name}.sha256'))
        if remote_uri:
            self.stdout.write(self.style.SUCCESS(f'Sauvegarde hors site : {remote_uri}'))
        if options['delete_local_after_upload']:
            try:
                output.unlink()
                checksum_output.unlink()
            except OSError as exc:
                raise CommandError(
                    f'Sauvegarde hors site validée, mais suppression locale incomplète : {exc}'
                ) from exc
            self.stdout.write('Copie locale supprimée après envoi validé.')
