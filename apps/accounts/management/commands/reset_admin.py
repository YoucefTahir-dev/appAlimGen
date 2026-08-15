import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.management import CommandError
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.translation import gettext as _


class Command(BaseCommand):
    help = _('Réinitialiser le mot de passe du compte administrateur avec le hash Django standard.')

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin', help=_('Nom utilisateur admin cible.'))
        parser.add_argument('--email', default='admin@local.com', help=_('Email à utiliser si un admin doit être créé.'))
        parser.add_argument(
            '--password-env',
            help=_('Nom de variable d’environnement contenant le nouveau mot de passe. À réserver aux scripts/tests.'),
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options['username']
        email = options['email']
        password_env = options.get('password_env')

        if password_env:
            password = os.getenv(password_env)
            if not password:
                raise CommandError(
                    _('Variable d’environnement introuvable ou vide : %(name)s')
                    % {'name': password_env}
                )
        else:
            password = getpass.getpass(_('Nouveau mot de passe administrateur : '))
            confirmation = getpass.getpass(_('Confirmer le nouveau mot de passe administrateur : '))
            if password != confirmation:
                raise CommandError(_('Les deux mots de passe ne correspondent pas.'))

        if len(password) < 8:
            raise CommandError(_('Le mot de passe doit contenir au moins 8 caractères.'))

        admin_user = User.objects.filter(username=username).first()
        if admin_user is None:
            admin_user = User.objects.filter(is_superuser=True).first()

        validate_password(password, user=admin_user)

        with transaction.atomic():
            created = False
            if admin_user is None:
                admin_user = User(username=username, email=email)
                created = True
            elif admin_user.username != username and User.objects.filter(username=username).exclude(pk=admin_user.pk).exists():
                raise CommandError(
                    _("Le nom d'utilisateur « %(username)s » existe déjà.")
                    % {'username': username}
                )

            admin_user.username = username
            admin_user.email = admin_user.email or email
            admin_user.is_active = True
            admin_user.is_staff = True
            admin_user.is_superuser = True
            if hasattr(admin_user, 'role'):
                admin_user.role = User.ADMIN
            admin_user.set_password(password)
            admin_user.save()
            administrator = Group.objects.filter(name='Administrateur').first()
            if administrator:
                admin_user.groups.add(administrator)

            try:
                from apps.core.models import AuditLog

                action = (
                    _('Création administrateur de récupération')
                    if created
                    else _('Réinitialisation du mot de passe administrateur de récupération')
                )
                AuditLog.objects.create(user=admin_user, action=action)
            except Exception:
                pass

        message = (
            _('Administrateur %(username)s créé avec succès.')
            if created
            else _('Mot de passe de %(username)s mis à jour avec succès.')
        )
        self.stdout.write(
            self.style.SUCCESS(message % {'username': admin_user.username})
        )
