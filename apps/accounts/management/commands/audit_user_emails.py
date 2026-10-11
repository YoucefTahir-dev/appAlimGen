from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import BaseCommand, CommandError
from django.core.validators import validate_email

from apps.accounts.email_policy import normalize_user_email


def mask_email(value):
    normalized = normalize_user_email(value)
    if '@' not in normalized:
        return '[adresse invalide masquée]'
    local, domain = normalized.rsplit('@', 1)
    domain_name, separator, suffix = domain.partition('.')
    masked_local = f'{local[:1]}***' if local else '***'
    masked_domain = f'{domain_name[:1]}***' if domain_name else '***'
    return f'{masked_local}@{masked_domain}{separator}{suffix}'


class Command(BaseCommand):
    help = (
        'Auditer les e-mails utilisateurs en lecture seule, sans afficher '
        'les adresses complètes.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fail-on-conflict',
            action='store_true',
            help='Retourner une erreur si une migration d’unicité serait dangereuse.',
        )

    def handle(self, *args, **options):
        records = list(
            get_user_model()._default_manager.order_by('pk').values(
                'pk', 'email', 'is_active',
            )
        )
        groups = defaultdict(list)
        missing = []
        invalid = []
        noncanonical = []

        for record in records:
            raw_email = record['email'] or ''
            normalized = normalize_user_email(raw_email)
            if not normalized:
                missing.append(record)
                continue
            groups[normalized].append(record)
            if raw_email != normalized:
                noncanonical.append(record)
            try:
                validate_email(normalized)
            except ValidationError:
                invalid.append(record)

        active_duplicates = [
            (email, [row for row in rows if row['is_active']])
            for email, rows in groups.items()
            if sum(1 for row in rows if row['is_active']) > 1
        ]
        all_duplicates = [
            (email, rows) for email, rows in groups.items() if len(rows) > 1
        ]
        active_inactive_conflicts = [
            (email, rows)
            for email, rows in groups.items()
            if any(row['is_active'] for row in rows)
            and any(not row['is_active'] for row in rows)
        ]

        self.stdout.write(f'Utilisateurs analysés : {len(records)}')
        self._write_groups('Doublons actifs', active_duplicates)
        self._write_groups('Doublons globaux', all_duplicates)
        self._write_rows('E-mails non normalisés', noncanonical)
        self._write_rows('Comptes sans e-mail', missing)
        self._write_rows('E-mails invalides', invalid)
        self._write_groups(
            'Conflits actifs/inactifs', active_inactive_conflicts,
        )

        blocking = bool(all_duplicates or noncanonical or invalid)
        self.stdout.write(
            self.style.ERROR('État migration : BLOQUÉE')
            if blocking
            else self.style.SUCCESS('État migration : COMPATIBLE')
        )
        if blocking and options['fail_on_conflict']:
            raise CommandError(
                'Les données doivent être corrigées manuellement avant la contrainte SQL.'
            )

    def _write_groups(self, title, groups):
        self.stdout.write(f'{title} : {len(groups)}')
        for email, rows in groups:
            ids = ','.join(str(row['pk']) for row in rows)
            self.stdout.write(f'  ids=[{ids}] email={mask_email(email)}')

    def _write_rows(self, title, rows):
        self.stdout.write(f'{title} : {len(rows)}')
        for row in rows:
            self.stdout.write(
                f"  id={row['pk']} email={mask_email(row['email'])}"
            )
