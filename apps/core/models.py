from django.conf import settings
from django.db import models

class CompanySettings(models.Model):
    company_name = models.CharField('Nom entreprise', max_length=200, default='El Amine lil Mawad El Ghidhaiya wa Ghayr El Ghidhaiya')
    address = models.CharField('Adresse', max_length=255, blank=True)
    phone = models.CharField('Téléphone', max_length=50, blank=True)
    email = models.EmailField('Email', blank=True)
    rc_number = models.CharField('RC', max_length=100, blank=True)
    tax_number = models.CharField('NIF', max_length=100, blank=True)
    tax_rate = models.DecimalField('TVA (%)', max_digits=5, decimal_places=2, default=19.00)
    logo = models.ImageField('Logo entreprise', upload_to='logos/', blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Paramètre société'
        verbose_name_plural = 'Paramètres société'

    def __str__(self):
        return self.company_name

class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    action = models.CharField('Action', max_length=255)
    ip_address = models.GenericIPAddressField('Adresse IP', blank=True, null=True)
    created_at = models.DateTimeField('Date', auto_now_add=True)

    class Meta:
        verbose_name = 'Journal action'
        verbose_name_plural = 'Journal des actions'

    def __str__(self):
        return f"{self.user} - {self.action}"
