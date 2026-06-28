from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Créer un superutilisateur de secours'

    def handle(self, *args, **options):
        User = get_user_model()

        username = input('Nom d\'utilisateur : ').strip()
        email = input('Email : ').strip()
        while True:
            password = input('Mot de passe : ')
            password_confirm = input('Confirmation du mot de passe : ')
            if password != password_confirm:
                self.stdout.write(self.style.ERROR('Les mots de passe ne correspondent pas.'))
                continue
            if not password:
                self.stdout.write(self.style.ERROR('Le mot de passe ne peut pas être vide.'))
                continue
            break

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.ERROR('Un utilisateur avec ce nom existe déjà.'))
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f'Superutilisateur {username} créé avec succès.'))
