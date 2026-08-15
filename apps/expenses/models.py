from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.security import expense_receipt_upload_to, validate_receipt_upload
from apps.inventory.models import Supplier


class ExpenseCategory(models.Model):
    name = models.CharField(_('Nom'), max_length=120, unique=True)
    is_active = models.BooleanField(_('Active'), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Catégorie de charge')
        verbose_name_plural = _('Catégories de charges')
        ordering = ['name']

    def __str__(self):
        return self.name


class ExpenseSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Séquence charge')
        verbose_name_plural = _('Séquences charges')

    def __str__(self):
        return f'{self.year} - {self.last_number}'


class Expense(models.Model):
    CASH = 'cash'
    CHEQUE = 'cheque'
    TRANSFER = 'transfer'
    CARD = 'card'
    PAYMENT_CHOICES = [
        (CASH, _('Espèces')),
        (CHEQUE, _('Chèque')),
        (TRANSFER, _('Virement')),
        (CARD, _('Carte')),
    ]

    number = models.CharField(_('Numéro'), max_length=100, unique=True, editable=False)
    date = models.DateField(_('Date'), default=timezone.localdate)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    description = models.CharField(_('Description'), max_length=255)
    amount = models.DecimalField(_('Montant'), max_digits=14, decimal_places=2)
    payment_method = models.CharField(_('Moyen de paiement'), max_length=20, choices=PAYMENT_CHOICES, default=CASH)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    receipt = models.FileField(_('Pièce justificative'), upload_to=expense_receipt_upload_to, validators=[validate_receipt_upload], blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expenses')
    observation = models.TextField(_('Observation'), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Charge')
        verbose_name_plural = _('Charges')
        ordering = ['-date', '-pk']
        indexes = [
            models.Index(fields=['date', '-id'], name='expense_date_id_idx'),
        ]

    def __str__(self):
        return self.number

    @classmethod
    def generate_number(cls):
        year = timezone.localdate().year
        prefix = f'CHG-{year}-'
        with transaction.atomic():
            sequence, _created = ExpenseSequence.objects.select_for_update().get_or_create(
                year=year
            )
            while True:
                sequence.last_number += 1
                sequence.save(update_fields=['last_number', 'updated_at'])
                candidate = f'{prefix}{sequence.last_number:06d}'
                if not cls.objects.filter(number=candidate).exists():
                    return candidate

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.generate_number()
        super().save(*args, **kwargs)
