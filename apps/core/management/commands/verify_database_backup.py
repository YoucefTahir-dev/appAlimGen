from django.core.management.base import BaseCommand, CommandError

from apps.core.backup import BackupError, verify_database_backup


class Command(BaseCommand):
    help = 'Vérifie l’empreinte et la structure d’une sauvegarde de base de données.'

    def add_arguments(self, parser):
        parser.add_argument('backup')
        parser.add_argument('--allow-missing-checksum', action='store_true')

    def handle(self, *args, **options):
        try:
            path = verify_database_backup(
                options['backup'],
                require_checksum=not options['allow_missing_checksum'],
            )
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Sauvegarde valide : {path}'))
