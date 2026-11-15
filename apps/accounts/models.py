import uuid
from pathlib import Path

from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models import Case, F, Q, Value, When
from django.db.models.functions import Greatest, Lower, Trim
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.security import validate_image_upload

from .email_policy import normalize_user_email, validate_user_email


class PasswordResetDispatch(models.Model):
    """Durable technical outbox; no plaintext recipient, password or reset token."""
    encrypted_email = models.TextField(blank=True)
    state = models.CharField(max_length=16, default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    available_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    lease = models.UUIDField(null=True, editable=False)
    lease_until = models.DateTimeField(null=True)

    class Meta:
        default_permissions = ()
        indexes = [models.Index(fields=['state', 'available_at'], name='reset_dispatch_ready_idx')]


def profile_photo_upload_to(instance, filename):
    """Store profile photos under an opaque name, never a user supplied path."""
    return f"profiles/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class UserQuerySet(models.QuerySet):
    """Keep supported bulk status changes atomic with JWT revocation.

    Raw SQL and deliberately bypassing this manager are not supported account
    mutation APIs. Expressions/bulk_update cannot describe an unambiguous
    account transition, so callers must use save() or update(is_active=bool).
    """

    def update(self, **kwargs):
        version_field = self.model._meta.get_field('auth_token_version')
        if 'is_active' in kwargs:
            active = kwargs['is_active']
            if not isinstance(active, bool):
                raise ValueError('Account status updates require a boolean.')
            if 'auth_token_version' in kwargs:
                raise ValueError('Do not combine status and token version updates.')
            if not active:
                kwargs['auth_token_version'] = Case(
                    When(
                        is_active=True,
                        then=F('auth_token_version') + Value(1, output_field=version_field),
                    ),
                    default=F('auth_token_version'),
                    output_field=version_field,
                )
        elif 'auth_token_version' in kwargs:
            version = kwargs['auth_token_version']
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError('Token versions must be positive integers.')
            kwargs['auth_token_version'] = Greatest(
                F('auth_token_version'),
                Value(version, output_field=version_field),
                output_field=version_field,
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        names = {getattr(field, 'name', field) for field in fields}
        if {'is_active', 'auth_token_version'}.intersection(names):
            raise ValueError('Use save() or a boolean status update for account revocation.')
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self, objs, batch_size=None, ignore_conflicts=False,
        update_conflicts=False, update_fields=None, unique_fields=None,
    ):
        names = {getattr(field, 'name', field) for field in (update_fields or ())}
        if update_conflicts and {'is_active', 'auth_token_version'}.intersection(names):
            raise ValueError(
                'Conflict updates cannot bypass account status revocation; use save().'
            )
        return super().bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def _revoke_api_tokens(self):
        return super().update(auth_token_version=F('auth_token_version') + 1)


class UserManager(DjangoUserManager):
    """Normalize accounts created through Django's supported manager API."""

    @classmethod
    def normalize_email(cls, email):
        return normalize_user_email(email)

    def get_queryset(self):
        return UserQuerySet(self.model, using=self._db, hints=self._hints)


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
    auth_token_version = models.PositiveBigIntegerField(default=1, editable=False)
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

    objects = UserManager()

    _AUTH_STATE_FIELDS = ('is_active', 'password', 'force_password_change')

    def _remember_auth_state(self):
        self._loaded_auth_state = {
            name: getattr(self, name) for name in self._AUTH_STATE_FIELDS
        }

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_auth_state = {
            name: instance.__dict__[name]
            for name in cls._AUTH_STATE_FIELDS if name in instance.__dict__
        }
        return instance

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        loaded = getattr(self, '_loaded_auth_state', {}).copy()
        for name in self._AUTH_STATE_FIELDS:
            if (fields is None or name in fields) and name in self.__dict__:
                loaded[name] = self.__dict__[name]
        self._loaded_auth_state = loaded

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        """Serialize status transitions and never restore a stale JWT version.

        A full save preserves concurrently changed authentication fields when
        they were not changed since loading. To intentionally update a field
        without loading it first, use explicit update_fields.
        """
        if update_fields is not None:
            update_fields = frozenset(update_fields)
            if not update_fields:
                return
        using = using or router.db_for_write(type(self), instance=self)
        if not self.pk or force_insert:
            result = super().save(
                force_insert=force_insert, force_update=force_update,
                using=using, update_fields=update_fields,
            )
            self._remember_auth_state()
            return result
        with transaction.atomic(using=using):
            current = type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
            if current is None:
                return super().save(
                    force_insert=force_insert, force_update=force_update,
                    using=using, update_fields=update_fields,
                )
            if update_fields is None:
                # Preserve Django's deferred-field save semantics.
                deferred = self.get_deferred_fields()
                persisted = {
                    field.attname for field in self._meta.concrete_fields
                    if not field.primary_key and field.attname not in deferred
                }
                loaded = getattr(self, '_loaded_auth_state', {})
                for name in self._AUTH_STATE_FIELDS:
                    if name not in persisted or name not in loaded or self.__dict__.get(name) == loaded[name]:
                        setattr(self, name, getattr(current, name))
            else:
                persisted = set(update_fields)
                for name in self._AUTH_STATE_FIELDS:
                    if name not in persisted:
                        setattr(self, name, getattr(current, name))
            # A model save may carry a deliberate synthetic/higher generation,
            # but no stale instance can ever lower the persisted generation.
            requested_version = self.auth_token_version
            self.auth_token_version = max(
                current.auth_token_version,
                requested_version if 'auth_token_version' in persisted else current.auth_token_version,
            )
            if 'is_active' in persisted and current.is_active and not self.is_active:
                self.auth_token_version = max(
                    self.auth_token_version, current.auth_token_version + 1,
                )
                persisted.add('auth_token_version')
            result = super().save(
                force_insert=force_insert, force_update=force_update,
                using=using, update_fields=persisted,
            )
            self._remember_auth_state()
            return result

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
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('email')),
                condition=~Q(email=''),
                name='uniq_user_email_ci_trim',
            ),
        ]

    def clean(self):
        super().clean()
        try:
            self.email = validate_user_email(
                self.email,
                queryset=type(self)._default_manager.all(),
                exclude_pk=self.pk,
            )
        except ValidationError as exc:
            raise ValidationError({'email': exc}) from exc

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

    def revoke_api_tokens(self):
        """Invalidate every JWT previously issued for this user."""
        if not self.pk:
            return
        using = router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            type(self).objects.using(using).filter(pk=self.pk)._revoke_api_tokens()
            self.refresh_from_db(using=using, fields=['auth_token_version'])
