from django.db import models
from django.db.models import F
from django.utils import timezone

from apps.inventory.models import Client, Product, ProductPackaging, Supplier


class Purchase(models.Model):
    reference = models.CharField('Référence achat', max_length=100, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchases')
    total = models.DecimalField('Total TTC', max_digits=14, decimal_places=2)
    tax_rate = models.DecimalField('TVA (%)', max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField('Date', default=timezone.now)

    class Meta:
        verbose_name = "Bon d'achat"
        verbose_name_plural = "Bons d'achat"

    def __str__(self):
        return self.reference

    def delete(self, *args, **kwargs):
        for line in list(self.lines.all()):
            line.delete()
        super().delete(*args, **kwargs)


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField('Quantité')
    purchase_price = models.DecimalField("Prix d'achat", max_digits=12, decimal_places=2)

    def line_total(self):
        return self.quantity * self.purchase_price

    def save(self, *args, **kwargs):
        if self.pk is None:
            super().save(*args, **kwargs)
            self.product.quantity = F('quantity') + self.quantity
            self.product.save(update_fields=['quantity'])
            self.product.refresh_from_db(fields=['quantity'])
        else:
            old = PurchaseLine.objects.get(pk=self.pk)
            quantity_diff = self.quantity - old.quantity
            product_changed = old.product_id != self.product_id
            super().save(*args, **kwargs)
            if product_changed:
                old.product.quantity = F('quantity') - old.quantity
                old.product.save(update_fields=['quantity'])
                old.product.refresh_from_db(fields=['quantity'])
                self.product.quantity = F('quantity') + self.quantity
                self.product.save(update_fields=['quantity'])
                self.product.refresh_from_db(fields=['quantity'])
            elif quantity_diff != 0:
                self.product.quantity = F('quantity') + quantity_diff
                self.product.save(update_fields=['quantity'])
                self.product.refresh_from_db(fields=['quantity'])

    def delete(self, *args, **kwargs):
        self.product.quantity = F('quantity') - self.quantity
        self.product.save(update_fields=['quantity'])
        self.product.refresh_from_db(fields=['quantity'])
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"


class Sale(models.Model):
    invoice_number = models.CharField('Numéro facture', max_length=100, unique=True)
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='sales')
    total = models.DecimalField('Total TTC', max_digits=14, decimal_places=2)
    discount = models.DecimalField('Remise', max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField('TVA (%)', max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField('Date', default=timezone.now)

    class Meta:
        verbose_name = 'Vente'
        verbose_name_plural = 'Ventes'

    def __str__(self):
        return self.invoice_number

    def delete(self, *args, **kwargs):
        for line in list(self.lines.all()):
            line.delete()
        super().delete(*args, **kwargs)


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    packaging = models.ForeignKey(ProductPackaging, on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.PositiveIntegerField('Quantité')
    unit_price = models.DecimalField('Prix unitaire', max_digits=12, decimal_places=2)

    @property
    def stock_quantity(self):
        unit_quantity = self.packaging.unit_quantity if self.packaging_id and self.packaging else 1
        return self.quantity * unit_quantity

    @property
    def minimum_sale_price(self):
        unit_quantity = self.packaging.unit_quantity if self.packaging_id and self.packaging else 1
        return self.product.purchase_price * unit_quantity

    def line_total(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if self.pk is None:
            stock_quantity = self.stock_quantity
            super().save(*args, **kwargs)
            self.product.quantity = F('quantity') - stock_quantity
            self.product.save(update_fields=['quantity'])
            self.product.refresh_from_db(fields=['quantity'])
        else:
            old = SaleLine.objects.select_related('product', 'packaging').get(pk=self.pk)
            old_stock_quantity = old.stock_quantity
            new_stock_quantity = self.stock_quantity
            quantity_diff = new_stock_quantity - old_stock_quantity
            product_changed = old.product_id != self.product_id
            super().save(*args, **kwargs)
            if product_changed:
                old.product.quantity = F('quantity') + old_stock_quantity
                old.product.save(update_fields=['quantity'])
                old.product.refresh_from_db(fields=['quantity'])
                self.product.quantity = F('quantity') - new_stock_quantity
                self.product.save(update_fields=['quantity'])
                self.product.refresh_from_db(fields=['quantity'])
            elif quantity_diff != 0:
                self.product.quantity = F('quantity') - quantity_diff
                self.product.save(update_fields=['quantity'])
                self.product.refresh_from_db(fields=['quantity'])

    def delete(self, *args, **kwargs):
        self.product.quantity = F('quantity') + self.stock_quantity
        self.product.save(update_fields=['quantity'])
        self.product.refresh_from_db(fields=['quantity'])
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"


class Payment(models.Model):
    CASH = 'cash'
    CHEQUE = 'cheque'
    TRANSFER = 'transfer'
    PAYMENT_CHOICES = [
        (CASH, 'Espèces'),
        (CHEQUE, 'Chèque'),
        (TRANSFER, 'Virement'),
    ]

    reference = models.CharField('Référence paiement', max_length=100, unique=True)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    purchase = models.ForeignKey(Purchase, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    amount = models.DecimalField('Montant', max_digits=14, decimal_places=2)
    payment_type = models.CharField('Type paiement', max_length=20, choices=PAYMENT_CHOICES)
    created_at = models.DateTimeField('Date', default=timezone.now)

    class Meta:
        verbose_name = 'Paiement'
        verbose_name_plural = 'Paiements'

    def __str__(self):
        return self.reference
