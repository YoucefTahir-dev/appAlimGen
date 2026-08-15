from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.security import company_logo_upload_to, validate_image_upload

class CompanySettings(models.Model):
    company_name = models.CharField(_('Nom entreprise'), max_length=200, default='El Amine lil Mawad El Ghidhaiya wa Ghayr El Ghidhaiya')
    address = models.CharField(_('Adresse'), max_length=255, blank=True)
    phone = models.CharField(_('Téléphone'), max_length=50, blank=True)
    email = models.EmailField(_('Email'), blank=True)
    rc_number = models.CharField(_('RC'), max_length=100, blank=True)
    tax_number = models.CharField(_('NIF'), max_length=100, blank=True)
    tax_rate = models.DecimalField(_('TVA (%)'), max_digits=5, decimal_places=2, default=19.00)
    logo = models.ImageField(_('Logo entreprise'), upload_to=company_logo_upload_to, validators=[validate_image_upload], blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Paramètre société')
        verbose_name_plural = _('Paramètres société')

    def __str__(self):
        return self.company_name

class AuditLog(models.Model):
    LEVEL_INFO = 'info'
    LEVEL_WARNING = 'warning'
    LEVEL_ERROR = 'error'
    LEVEL_CHOICES = [
        (LEVEL_INFO, _('Info')),
        (LEVEL_WARNING, _('Avertissement')),
        (LEVEL_ERROR, _('Erreur')),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    action = models.CharField(_('Action'), max_length=255)
    level = models.CharField(_('Niveau'), max_length=16, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    ip_address = models.GenericIPAddressField(_('Adresse IP'), blank=True, null=True)
    path = models.CharField(_('URL'), max_length=255, blank=True)
    status_code = models.PositiveIntegerField(_('Code HTTP'), blank=True, null=True)
    created_at = models.DateTimeField(_('Date'), auto_now_add=True)

    class Meta:
        verbose_name = _('Journal action')
        verbose_name_plural = _('Journal des actions')
        indexes = [
            models.Index(fields=['action', 'ip_address', '-created_at'], name='audit_action_ip_date_idx'),
            models.Index(fields=['created_at'], name='audit_created_at_idx'),
        ]

    def __str__(self):
        return f"{self.user} - {self.action}"
