from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Créer un superutilisateur de secours'

    def handle(self, *args, **options):
        User = get_user_model()

        username = input("Nom d'utilisateur : ").strip()
        email = input('Email : ').strip()
        while True:
            password = getpass('Mot de passe : ')
            password_confirm = getpass('Confirmation du mot de passe : ')
            if password != password_confirm:
                self.stdout.write(self.style.ERROR('Les mots de passe ne correspondent pas.'))
                continue
            if not password:
                self.stdout.write(self.style.ERROR('Le mot de passe ne peut pas être vide.'))
                continue
            validate_password(password)
            break

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.ERROR('Un utilisateur avec ce nom existe déjà.'))
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f'Superutilisateur {username} créé avec succès.'))
