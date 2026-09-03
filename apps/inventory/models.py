import io
import logging

import qrcode
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing

from apps.core.security import product_photo_upload_to, validate_image_upload


logger = logging.getLogger(__name__)


def normalize_business_text(value):
    return ' '.join(str(value or '').split())


class ReferenceDataManager(models.Manager):
    def resolve(self, value):
        """Return one normalized reference value, safely under concurrent imports."""
        cleaned = normalize_business_text(value)
        if not cleaned:
            raise ValidationError(_('Le nom du référentiel ne peut pas être vide.'))
        existing = self.filter(name__iexact=cleaned).order_by('pk').first()
        if existing:
            return existing
        try:
            with transaction.atomic():
                return self.create(name=cleaned)
        except IntegrityError:
            return self.get(name__iexact=cleaned)


class Category(models.Model):
    name = models.CharField(_('Nom catégorie'), max_length=120)
    objects = ReferenceDataManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_category_name_ci_trim',
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = normalize_business_text(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(_('Nom marque'), max_length=120)
    objects = ReferenceDataManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_brand_name_ci_trim',
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = normalize_business_text(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Unit(models.Model):
    name = models.CharField(_('Unité'), max_length=50)
    objects = ReferenceDataManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_unit_name_ci_trim',
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = normalize_business_text(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProductReferenceSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Séquence référence produit')
        verbose_name_plural = _('Séquences références produits')

    def __str__(self):
        return f'{self.year} - {self.last_number}'


class Product(models.Model):
    reference = models.CharField(_('Référence'), max_length=100, unique=True, editable=False)
    barcode = models.CharField(_('Code-barres'), max_length=100, unique=True, blank=True)
    name = models.CharField(_('Nom produit'), max_length=255)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, related_name='products')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, related_name='products')
    purchase_price = models.DecimalField(_("Prix d'achat"), max_digits=12, decimal_places=2)
    sale_price = models.DecimalField(_('Prix de vente'), max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(_('Quantité en stock'), default=0)
    minimum_stock = models.PositiveIntegerField(_('Stock minimum'), default=0)
    description = models.TextField(_('Description'), blank=True)
    photo = models.ImageField(_('Photo'), upload_to=product_photo_upload_to, validators=[validate_image_upload], blank=True, null=True)
    qr_code = models.ImageField(_('QR Code'), upload_to='qrcodes/', blank=True, null=True)
    barcode_image = models.FileField(_('Image code-barres'), upload_to='barcodes/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Produit')
        verbose_name_plural = _('Produits')
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def _generate_reference(cls):
        year = timezone.now().year
        prefix = f'PRD-{year}-'
        with transaction.atomic():
            sequence, _created = ProductReferenceSequence.objects.select_for_update().get_or_create(
                year=year
            )
            while True:
                sequence.last_number += 1
                sequence.save(update_fields=['last_number', 'updated_at'])
                candidate = f'{prefix}{sequence.last_number:06d}'
                if not cls.objects.filter(reference=candidate).exists():
                    return candidate

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
        self._generated_media_errors = []

        if not self.reference:
            self.reference = Product._generate_reference()

        if not self.barcode and self.reference:
            self.barcode = self._generate_barcode(self.reference)

        if self.barcode and not self.barcode_image:
            try:
                barcode_svg = self._build_barcode_svg(self.barcode)
                self.barcode_image.save(
                    f"{self.reference}_barcode.svg",
                    ContentFile(barcode_svg),
                    save=False,
                )
            except Exception:  # External media storage must not block the product record.
                self._generated_media_errors.append('barcode_image')
                logger.exception(
                    'Unable to generate or store the barcode image for product %s.',
                    self.reference,
                )

        super().save(*args, **kwargs)

        if not self.qr_code:
            try:
                qr_text = f"REF : {self.reference}\nPRODUIT : {self.name}\nPRIX : {self.sale_price} DA"
                img = qrcode.make(qr_text)
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                self.qr_code.save(f"{self.reference}.png", ContentFile(buf.getvalue()), save=False)
                super().save(update_fields=['qr_code'])
            except Exception:  # External media storage must not block the product record.
                self._generated_media_errors.append('qr_code')
                logger.exception(
                    'Unable to generate or store the QR code image for product %s.',
                    self.reference,
                )


class ImmutableStockMovementQuerySet(models.QuerySet):
    def _raise_immutable(self):
        raise ValidationError(
            _('Le journal de stock est immuable. Utilisez le service de stock et une contrepassation.')
        )

    def delete(self):
        self._raise_immutable()

    def update(self, **kwargs):
        self._raise_immutable()

    def bulk_create(self, objs, **kwargs):
        self._raise_immutable()

    def bulk_update(self, objs, fields, **kwargs):
        self._raise_immutable()


class StockMovement(models.Model):
    ENTRY = 'entry'
    EXIT = 'exit'
    ADJUSTMENT = 'adjustment'
    MOVEMENT_CHOICES = [
        (ENTRY, _('Entrée stock')),
        (EXIT, _('Sortie stock')),
        (ADJUSTMENT, _('Ajustement')),
    ]

    SOURCE_LEGACY = 'legacy'
    SOURCE_MANUAL = 'manual'
    SOURCE_PRODUCT = 'product'
    SOURCE_IMPORT = 'import'
    SOURCE_PURCHASE = 'purchase'
    SOURCE_SALE = 'sale'
    SOURCE_REVERSAL = 'reversal'
    SOURCE_CHOICES = [
        (SOURCE_LEGACY, _('Historique antérieur')),
        (SOURCE_MANUAL, _('Saisie manuelle')),
        (SOURCE_PRODUCT, _('Fiche produit')),
        (SOURCE_IMPORT, _('Import produits')),
        (SOURCE_PURCHASE, _('Achat')),
        (SOURCE_SALE, _('Vente')),
        (SOURCE_REVERSAL, _('Annulation')),
    ]

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='movements')
    movement_type = models.CharField(_('Type mouvement'), max_length=16, choices=MOVEMENT_CHOICES)
    quantity = models.IntegerField(_('Quantité'))
    reason = models.CharField(_('Motif'), max_length=255, blank=True)
    applied_delta = models.IntegerField(
        _('Variation appliquée'),
        null=True,
        blank=True,
        editable=False,
        help_text=_('Valeur nulle uniquement pour les mouvements historiques non rapprochés.'),
    )
    balance_before = models.PositiveIntegerField(
        _('Stock avant mouvement'),
        null=True,
        blank=True,
        editable=False,
    )
    balance_after = models.PositiveIntegerField(
        _('Stock après mouvement'),
        null=True,
        blank=True,
        editable=False,
    )
    source_type = models.CharField(
        _('Origine'),
        max_length=16,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
        editable=False,
    )
    source_reference = models.CharField(_('Référence origine'), max_length=100, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
        editable=False,
    )
    reversal_of = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversal',
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableStockMovementQuerySet.as_manager()

    class Meta:
        verbose_name = _('Mouvement stock')
        verbose_name_plural = _('Mouvements stock')
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['product', '-created_at'], name='inv_mov_prod_created_idx'),
            models.Index(fields=['source_type', 'source_reference'], name='inv_mov_source_ref_idx'),
        ]

    def clean(self):
        super().clean()
        if self.quantity is None:
            return
        if self.movement_type in (self.ENTRY, self.EXIT) and self.quantity <= 0:
            raise ValidationError({'quantity': _('La quantité doit être strictement positive.')})
        if self.movement_type == self.ADJUSTMENT and self.quantity < 0:
            raise ValidationError({'quantity': _('Le stock physique ne peut pas être négatif.')})
        if self.reversal_of_id and self.reversal_of_id == self.pk:
            raise ValidationError({'reversal_of': _('Un mouvement ne peut pas s’annuler lui-même.')})

    def save(self, *args, **kwargs):
        if self.pk or not self._state.adding:
            raise ValidationError(_('Un mouvement de stock validé est immuable.'))
        if not getattr(self, '_ledger_write_allowed', False):
            raise ValidationError(
                _('Utilisez le service de stock pour enregistrer un mouvement et mettre à jour le solde.')
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _('Un mouvement de stock ne peut pas être supprimé. Utilisez une annulation traçable.')
        )

    def __str__(self):
        return f"{self.product.name} - {self.movement_type}"


class Client(models.Model):
    name = models.CharField(_('Nom'), max_length=200)
    phone = models.CharField(_('Téléphone'), max_length=50, blank=True)
    address = models.CharField(_('Adresse'), max_length=255, blank=True)
    wilaya = models.CharField(_('Wilaya'), max_length=100, blank=True)
    email = models.EmailField(_('Email'), blank=True)
    tax_number = models.CharField(_('NIF'), max_length=100, blank=True)
    balance = models.DecimalField(_('Solde'), max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(_('Notes'), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Client')
        verbose_name_plural = _('Clients')
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('email')),
                condition=~Q(email=''),
                name='uniq_client_email_ci_trim',
            ),
            models.UniqueConstraint(
                Lower(Trim('tax_number')),
                condition=~Q(tax_number=''),
                name='uniq_client_nif_ci_trim',
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = normalize_business_text(self.name)
        self.phone = normalize_business_text(self.phone)
        self.email = normalize_business_text(self.email).lower()
        self.tax_number = normalize_business_text(self.tax_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(_('Nom'), max_length=200)
    phone = models.CharField(_('Téléphone'), max_length=50, blank=True)
    address = models.CharField(_('Adresse'), max_length=255, blank=True)
    wilaya = models.CharField(_('Wilaya'), max_length=100, blank=True)
    email = models.EmailField(_('Email'), blank=True)
    rc_number = models.CharField(_('RC'), max_length=100, blank=True)
    tax_number = models.CharField(_('NIF'), max_length=100, blank=True)
    notes = models.TextField(_('Notes'), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Fournisseur')
        verbose_name_plural = _('Fournisseurs')
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('email')),
                condition=~Q(email=''),
                name='uniq_supplier_email_ci_trim',
            ),
            models.UniqueConstraint(
                Lower(Trim('rc_number')),
                condition=~Q(rc_number=''),
                name='uniq_supplier_rc_ci_trim',
            ),
            models.UniqueConstraint(
                Lower(Trim('tax_number')),
                condition=~Q(tax_number=''),
                name='uniq_supplier_nif_ci_trim',
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = normalize_business_text(self.name)
        self.phone = normalize_business_text(self.phone)
        self.email = normalize_business_text(self.email).lower()
        self.rc_number = normalize_business_text(self.rc_number)
        self.tax_number = normalize_business_text(self.tax_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
