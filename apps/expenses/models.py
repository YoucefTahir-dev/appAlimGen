from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.security import expense_receipt_upload_to, validate_receipt_upload
from apps.inventory.models import Supplier


class ExpenseCategory(models.Model):
    name = models.CharField('Nom', max_length=120, unique=True)
    is_active = models.BooleanField('Active', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Catégorie de charge'
        verbose_name_plural = 'Catégories de charges'
        ordering = ['name']

    def __str__(self):
        return self.name


class Expense(models.Model):
    CASH = 'cash'
    CHEQUE = 'cheque'
    TRANSFER = 'transfer'
    CARD = 'card'
    PAYMENT_CHOICES = [
        (CASH, 'Espèces'),
        (CHEQUE, 'Chèque'),
        (TRANSFER, 'Virement'),
        (CARD, 'Carte'),
    ]

    number = models.CharField('Numéro', max_length=100, unique=True, editable=False)
    date = models.DateField('Date', default=timezone.localdate)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    description = models.CharField('Description', max_length=255)
    amount = models.DecimalField('Montant', max_digits=14, decimal_places=2)
    payment_method = models.CharField('Moyen de paiement', max_length=20, choices=PAYMENT_CHOICES, default=CASH)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    receipt = models.FileField('Pièce justificative', upload_to=expense_receipt_upload_to, validators=[validate_receipt_upload], blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expenses')
    observation = models.TextField('Observation', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Charge'
        verbose_name_plural = 'Charges'
        ordering = ['-date', '-pk']

    def __str__(self):
        return self.number

    @classmethod
    def generate_number(cls):
        year = timezone.localdate().year
        prefix = f'CHG-{year}-'
        last = cls.objects.filter(number__startswith=prefix).order_by('-number').first()
        if last and last.number:
            try:
                last_number = int(last.number.split('-')[-1])
            except (TypeError, ValueError):
                last_number = 0
        else:
            last_number = 0
        return f'{prefix}{last_number + 1:06d}'

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.generate_number()
        super().save(*args, **kwargs)
