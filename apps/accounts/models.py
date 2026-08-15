import uuid
from pathlib import Path

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.security import validate_image_upload


def profile_photo_upload_to(instance, filename):
    """Store profile photos under an opaque name, never a user supplied path."""
    return f"profiles/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"

class User(AbstractUser):
    ADMIN = 'admin'
    MANAGER = 'manager'
    SELLER = 'seller'
    ROLE_CHOICES = [
        (ADMIN, _('Administrateur')),
        (MANAGER, _('Gestionnaire')),
        (SELLER, _('Vendeur')),
    ]
    ROLE_GROUP_NAMES = {
        ADMIN: 'Administrateur',
        MANAGER: 'Gestionnaire',
        SELLER: 'Vendeur',
    }

    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=SELLER)
    force_password_change = models.BooleanField(default=False)
    phone = models.CharField(_('Téléphone'), max_length=30, blank=True)
    photo = models.ImageField(
        _('Photo de profil'),
        upload_to=profile_photo_upload_to,
        validators=[validate_image_upload],
        blank=True,
        null=True,
    )
    denied_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name=_('Permissions individuelles refusées'),
        blank=True,
        related_name='denied_user_set',
        related_query_name='denied_user',
        help_text=_('Ces refus priment sur les permissions du rôle et les permissions directes.'),
    )

    class Meta:
        verbose_name = _('Utilisateur')
        verbose_name_plural = _('Utilisateurs')
        permissions = [
            ('view_dashboard', _('Peut voir le tableau de bord')),
            ('view_stock', _("Peut voir l'état du stock")),
            ('manage_stock', _('Peut enregistrer des mouvements de stock')),
            ('view_invoices', _('Peut voir les factures')),
            ('download_invoice_pdf', _('Peut télécharger les factures PDF')),
            ('print_invoice', _('Peut imprimer les factures et tickets')),
            ('view_reports', _('Peut voir les rapports')),
            ('export_reports_pdf', _('Peut exporter les rapports PDF')),
            ('export_reports_excel', _('Peut exporter les rapports Excel')),
            ('view_backups', _('Peut voir les sauvegardes')),
            ('create_backups', _('Peut créer des sauvegardes')),
            ('restore_backups', _('Peut restaurer des sauvegardes')),
        ]

    @property
    def primary_role(self):
        """Return the configured dynamic role without loading every group."""
        legacy_name = self.ROLE_GROUP_NAMES.get(self.role)
        if legacy_name:
            legacy_group = self.groups.filter(name=legacy_name).first()
            if legacy_group:
                return legacy_group
        return self.groups.order_by('name').first()

    def denied_permission_names(self):
        if self.is_superuser or not self.pk:
            return frozenset()
        if not hasattr(self, '_denied_permission_names_cache'):
            self._denied_permission_names_cache = frozenset(
                f'{app_label}.{codename}'
                for app_label, codename in self.denied_permissions.values_list(
                    'content_type__app_label', 'codename'
                )
            )
        return self._denied_permission_names_cache

    def is_permission_denied(self, permission_name):
        return not self.is_superuser and permission_name in self.denied_permission_names()

    def has_perm(self, perm, obj=None):
        if self.is_active and self.is_superuser:
            return True
        if self.is_permission_denied(perm):
            return False
        return super().has_perm(perm, obj=obj)

    def is_administrator(self):
        return self.is_superuser or self.groups.filter(name='Administrateur').exists() or (
            not self.groups.exists() and self.role == self.ADMIN
        )

    def is_manager(self):
        return self.is_administrator() or self.groups.filter(name='Gestionnaire').exists() or (
            not self.groups.exists() and self.role == self.MANAGER
        )

    def is_seller(self):
        return self.is_manager() or self.groups.filter(name='Vendeur').exists() or (
            not self.groups.exists() and self.role == self.SELLER
        )
