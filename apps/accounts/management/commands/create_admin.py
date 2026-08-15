from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _


class Command(BaseCommand):
    help = _('Créer un superutilisateur de secours')

    def handle(self, *args, **options):
        User = get_user_model()

        username = input(_("Nom d'utilisateur : ")).strip()
        email = input(_('Email : ')).strip()
        while True:
            password = getpass(_('Mot de passe : '))
            password_confirm = getpass(_('Confirmation du mot de passe : '))
            if password != password_confirm:
                self.stdout.write(self.style.ERROR(_('Les mots de passe ne correspondent pas.')))
                continue
            if not password:
                self.stdout.write(self.style.ERROR(_('Le mot de passe ne peut pas être vide.')))
                continue
            validate_password(password)
            break

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.ERROR(_('Un utilisateur avec ce nom existe déjà.')))
            return

        user = User.objects.create_superuser(username=username, email=email, password=password)
        administrator = Group.objects.filter(name='Administrateur').first()
        if administrator:
            user.groups.add(administrator)
        self.stdout.write(
            self.style.SUCCESS(
                _('Superutilisateur %(username)s créé avec succès.')
                % {'username': username}
            )
        )
