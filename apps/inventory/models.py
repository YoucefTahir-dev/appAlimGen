import io

import qrcode
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing

from apps.core.security import product_photo_upload_to, validate_image_upload


class Category(models.Model):
    name = models.CharField('Nom catégorie', max_length=120)

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField('Nom marque', max_length=120)

    def __str__(self):
        return self.name


class Unit(models.Model):
    name = models.CharField('Unité', max_length=50)

    def __str__(self):
        return self.name


class Product(models.Model):
    reference = models.CharField('Référence', max_length=100, unique=True, editable=False)
    barcode = models.CharField('Code-barres', max_length=100, unique=True, blank=True)
    name = models.CharField('Nom produit', max_length=255)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, related_name='products')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, related_name='products')
    purchase_price = models.DecimalField("Prix d'achat", max_digits=12, decimal_places=2)
    sale_price = models.DecimalField('Prix de vente', max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField('Quantité en stock', default=0)
    minimum_stock = models.PositiveIntegerField('Stock minimum', default=0)
    description = models.TextField('Description', blank=True)
    photo = models.ImageField('Photo', upload_to=product_photo_upload_to, validators=[validate_image_upload], blank=True, null=True)
    qr_code = models.ImageField('QR Code', upload_to='qrcodes/', blank=True, null=True)
    barcode_image = models.FileField('Image code-barres', upload_to='barcodes/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Produit'
        verbose_name_plural = 'Produits'
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def _generate_reference(cls):
        year = timezone.now().year
        prefix = f'PRD-{year}-'
        last = cls.objects.filter(reference__startswith=prefix).order_by('-reference').first()
        if last and last.reference:
            try:
                last_num = int(last.reference.split('-')[-1])
            except Exception:
                last_num = 0
        else:
            last_num = 0
        return f"{prefix}{last_num + 1:06d}"

    @classmethod
    def _generate_barcode(cls, reference):
        return f'BC-{reference}'

    @staticmethod
    def _build_barcode_svg(barcode_value):
        drawing = createBarcodeDrawing(
            'Code128',
            value=barcode_value,
            barHeight=44,
            humanReadable=True,
        )
        return renderSVG.drawToString(drawing).encode('utf-8')

    def save(self, *args, **kwargs):
        if not self.reference:
            for _ in range(10):
                candidate = Product._generate_reference()
                if not Product.objects.filter(reference=candidate).exists():
                    self.reference = candidate
                    break

        if not self.barcode and self.reference:
            self.barcode = self._generate_barcode(self.reference)

        if self.barcode and not self.barcode_image:
            barcode_svg = self._build_barcode_svg(self.barcode)
            self.barcode_image.save(f"{self.reference}_barcode.svg", ContentFile(barcode_svg), save=False)

        super().save(*args, **kwargs)

        if not self.qr_code:
            qr_text = f"REF : {self.reference}\nPRODUIT : {self.name}\nPRIX : {self.sale_price} DA"
            img = qrcode.make(qr_text)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            self.qr_code.save(f"{self.reference}.png", ContentFile(buf.getvalue()), save=False)
            super().save(update_fields=['qr_code'])


class StockMovement(models.Model):
    ENTRY = 'entry'
    EXIT = 'exit'
    ADJUSTMENT = 'adjustment'
    MOVEMENT_CHOICES = [
        (ENTRY, 'Entrée stock'),
        (EXIT, 'Sortie stock'),
        (ADJUSTMENT, 'Ajustement'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField('Type mouvement', max_length=16, choices=MOVEMENT_CHOICES)
    quantity = models.IntegerField('Quantité')
    reason = models.CharField('Motif', max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Mouvement stock'
        verbose_name_plural = 'Mouvements stock'

    def __str__(self):
        return f"{self.product.name} - {self.movement_type}"


class Client(models.Model):
    name = models.CharField('Nom', max_length=200)
    phone = models.CharField('Téléphone', max_length=50, blank=True)
    address = models.CharField('Adresse', max_length=255, blank=True)
    wilaya = models.CharField('Wilaya', max_length=100, blank=True)
    email = models.EmailField('Email', blank=True)
    tax_number = models.CharField('NIF', max_length=100, blank=True)
    balance = models.DecimalField('Solde', max_digits=12, decimal_places=2, default=0)
    notes = models.TextField('Notes', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField('Nom', max_length=200)
    phone = models.CharField('Téléphone', max_length=50, blank=True)
    address = models.CharField('Adresse', max_length=255, blank=True)
    wilaya = models.CharField('Wilaya', max_length=100, blank=True)
    email = models.EmailField('Email', blank=True)
    rc_number = models.CharField('RC', max_length=100, blank=True)
    tax_number = models.CharField('NIF', max_length=100, blank=True)
    notes = models.TextField('Notes', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Fournisseur'
        verbose_name_plural = 'Fournisseurs'

    def __str__(self):
        return self.name
