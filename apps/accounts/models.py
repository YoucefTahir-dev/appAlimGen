from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ADMIN = 'admin'
    MANAGER = 'manager'
    SELLER = 'seller'
    ROLE_CHOICES = [
        (ADMIN, 'Administrateur'),
        (MANAGER, 'Gestionnaire'),
        (SELLER, 'Vendeur'),
    ]

    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=SELLER)

    class Meta:
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'

    def is_administrator(self):
        return self.role == self.ADMIN

    def is_manager(self):
        return self.role == self.MANAGER

    def is_seller(self):
        return self.role == self.SELLER
