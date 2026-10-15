from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.inventory.models import Client, LoadingOrder, OperatorStockMovement, Product, ProductPackaging, StockMovement, Supplier
from apps.inventory.services import adjust_operator_stock, record_stock_movements, stock_change_for_delta


ZERO = Decimal('0.00')


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _stock_reference(kind, parent_id, line_id, action):
    return f'{kind}:{parent_id}:line:{line_id}:{action}'


def _apply_line_stock_changes(*changes):
    record_stock_movements([change for change in changes if change is not None])


def _apply_sale_stock_delta(*, sale, product_id, delta, user, reference, reason):
    if not delta:
        return
    if sale.loading_order_id:
        if sale.loading_order.status not in (LoadingOrder.VALIDATED, LoadingOrder.IN_PROGRESS):
            raise ValidationError(_('Le chargement associé à cette vente n’est plus actif.'))
        adjust_operator_stock(
            loading_order=sale.loading_order, product=product_id, delta=delta,
            movement_type=OperatorStockMovement.SALE if delta < 0 else OperatorStockMovement.REVERSAL,
            user=user, source_reference=reference,
        )
        return
    _apply_line_stock_changes(stock_change_for_delta(
        product=product_id, delta=delta, reason=reason, user=user,
        source_type=StockMovement.SOURCE_SALE, source_reference=reference,
    ))


class StockAwareDeleteQuerySet(models.QuerySet):
    @transaction.atomic
    def delete(self):
        deleted_total = 0
        deleted_by_model = {}
        for obj in list(self.select_for_update()):
            count, details = obj.delete()
            deleted_total += count
            for label, value in details.items():
                deleted_by_model[label] = deleted_by_model.get(label, 0) + value
        return deleted_total, deleted_by_model


class StockTrackedLineQuerySet(StockAwareDeleteQuerySet):
    def update(self, **kwargs):
        if {
            'quantity', 'product', 'product_id', 'unit_cost', 'packaging',
            'packaging_id', 'packaging_factor', 'packaging_quantity',
        } & kwargs.keys():
            raise ValidationError(
                'La quantité et le produit d’une ligne commerciale doivent être modifiés avec save().'
            )
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('La création groupée contournerait le journal de stock.')

    def bulk_update(self, objs, fields, **kwargs):
        if {
            'quantity', 'product', 'product_id', 'unit_cost', 'packaging',
            'packaging_id', 'packaging_factor', 'packaging_quantity',
        } & set(fields):
            raise ValidationError(
                'La mise à jour groupée contournerait le journal de stock.'
            )
        return super().bulk_update(objs, fields, **kwargs)


class CommercialDocumentQuerySet(StockAwareDeleteQuerySet):
    def update(self, **kwargs):
        if 'total' in kwargs:
            raise ValidationError(_('Le total doit être modifié avec save() pour contrôler les règlements.'))
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if 'total' in fields:
            raise ValidationError(_('La mise à jour groupée des totaux est interdite.'))
        return super().bulk_update(objs, fields, **kwargs)


def _document_paid_amount(document):
    cached = getattr(document, '_prefetched_objects_cache', {}).get('payments')
    if cached is not None:
        return sum((payment.amount for payment in cached), ZERO)
    return document.payments.aggregate(total=Sum('amount'))['total'] or ZERO


def _payment_status(document):
    if not document.payment_tracking_initialized:
        return 'unreconciled'
    paid = _document_paid_amount(document)
    if paid >= _decimal(document.total):
        return 'paid'
    if paid > ZERO:
        return 'partial'
    return 'unpaid'


class Purchase(models.Model):
    reference = models.CharField('Référence achat', max_length=100, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchases')
    total = models.DecimalField('Total TTC', max_digits=14, decimal_places=2)
    tax_rate = models.DecimalField('TVA (%)', max_digits=5, decimal_places=2, default=0)
    payment_tracking_initialized = models.BooleanField('Suivi des règlements initialisé', default=True)
    created_at = models.DateTimeField('Date', default=timezone.now, db_index=True)

    objects = CommercialDocumentQuerySet.as_manager()

    class Meta:
        verbose_name = "Bon d'achat"
        verbose_name_plural = "Bons d'achat"

    def __str__(self):
        return self.reference

    @property
    def amount_paid(self):
        return _document_paid_amount(self)

    @property
    def balance_due(self):
        return max(_decimal(self.total) - self.amount_paid, ZERO)

    @property
    def payment_status(self):
        return _payment_status(self)

    @transaction.atomic
    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if self.pk and (update_fields is None or 'total' in update_fields):
            Purchase.objects.select_for_update().get(pk=self.pk)
            if self.total < _document_paid_amount(self):
                raise ValidationError({'total': _('Le total ne peut pas être inférieur aux règlements déjà enregistrés.')})
        return super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        Purchase.objects.select_for_update().get(pk=self.pk)
        user = getattr(self, '_stock_user', None)
        for line in list(self.lines.all()):
            line._stock_user = user
            line.delete()
        return super().delete(*args, **kwargs)


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField('Quantité')
    packaging = models.ForeignKey(
        ProductPackaging, on_delete=models.PROTECT, null=True, blank=True,
        related_name='purchase_lines', verbose_name=_('Conditionnement'),
    )
    packaging_name = models.CharField(_('Conditionnement historique'), max_length=100, blank=True)
    packaging_factor = models.PositiveIntegerField(_('Facteur historique'), default=1, editable=False)
    packaging_quantity = models.PositiveIntegerField(
        _('Quantité achetée'), null=True, blank=True,
        help_text=_('Quantité saisie dans le conditionnement sélectionné.'),
    )
    purchase_price = models.DecimalField("Prix d'achat", max_digits=12, decimal_places=2)

    objects = StockTrackedLineQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(packaging_factor__gte=1),
                name='purchase_line_packaging_factor_gte_1',
            ),
            models.CheckConstraint(
                condition=Q(packaging_quantity__isnull=True) | Q(packaging_quantity__gte=1),
                name='purchase_line_packaging_quantity_positive',
            ),
        ]

    @property
    def entered_quantity(self):
        return self.packaging_quantity if self.packaging_quantity is not None else self.quantity

    @property
    def stock_quantity(self):
        return self.quantity

    def line_total(self):
        return self.entered_quantity * self.purchase_price

    def _set_packaging_snapshot(self, old=None):
        # Lock only Product. ``Product.unit`` is nullable and joining it in a
        # FOR UPDATE query is rejected by PostgreSQL.
        product = Product.objects.select_for_update().get(pk=self.product_id)
        entered_quantity = self.packaging_quantity
        if entered_quantity is None:
            entered_quantity = self.quantity
        entered_quantity = int(entered_quantity)
        if entered_quantity < 1:
            raise ValidationError({'packaging_quantity': _('La quantité doit être strictement positive.')})

        packaging = None
        same_historical_unit = bool(
            old
            and old.product_id == self.product_id
            and old.packaging_id == self.packaging_id
        )
        if same_historical_unit:
            factor = old.packaging_factor
            packaging_name = old.packaging_name
        elif self.packaging_id:
            packaging = ProductPackaging.objects.select_for_update().get(pk=self.packaging_id)
            if (
                packaging.product_id != product.pk
                or not packaging.is_active
                or not packaging.purchase_enabled
            ):
                raise ValidationError({'packaging': _('Conditionnement d’achat invalide ou inactif.')})
            if product.unit_mode in {Product.UnitMode.LEGACY, Product.UnitMode.PIECE_ONLY}:
                raise ValidationError({'packaging': _('Ce produit ne peut être acheté qu’en unité de base.')})
            if product.unit_mode == Product.UnitMode.CARTON_ONLY and packaging.kind != ProductPackaging.CARTON:
                raise ValidationError({'packaging': _('Ce produit doit être acheté en carton.')})
            factor = packaging.conversion_factor
            packaging_name = packaging.name
        elif product.unit_mode == Product.UnitMode.CARTON_ONLY:
            raise ValidationError({'packaging': _('Ce produit doit être acheté en carton.')})
        else:
            factor = 1
            packaging_name = product.unit.name if product.unit_id else str(_('Unité de base'))

        self.packaging_quantity = entered_quantity
        self.packaging_factor = factor
        self.packaging_name = packaging_name
        self.quantity = entered_quantity * factor

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.purchase_price = Decimal(str(self.purchase_price))
        user = getattr(self, '_stock_user', None)
        old = None
        if self.pk:
            old = PurchaseLine.objects.select_for_update().get(pk=self.pk)
        if (
            old
            and not self.packaging_id
            and (
                self.packaging_quantity is None
                or self.packaging_quantity == old.packaging_quantity
            )
            and self.quantity != old.quantity
        ):
            # Preserve the historical direct-model API where ``quantity`` is the
            # entered base-unit quantity.
            self.packaging_quantity = self.quantity
        self._set_packaging_snapshot(old=old)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if update_fields & {
                'product', 'product_id', 'quantity', 'packaging',
                'packaging_id', 'packaging_quantity',
            }:
                update_fields.update({
                    'product', 'quantity', 'packaging', 'packaging_name',
                    'packaging_factor', 'packaging_quantity',
                })
                update_fields.discard('product_id')
                update_fields.discard('packaging_id')
            kwargs['update_fields'] = update_fields
        if self._state.adding:
            super().save(*args, **kwargs)
            _apply_line_stock_changes(
                stock_change_for_delta(
                    product=self.product_id,
                    delta=self.quantity,
                    reason=f'Création ligne achat {self.purchase.reference}',
                    user=user,
                    source_type=StockMovement.SOURCE_PURCHASE,
                    source_reference=_stock_reference('purchase', self.purchase_id, self.pk, 'create'),
                )
            )
            return

        persisted_product_id = self.product_id
        persisted_quantity = self.quantity
        if update_fields is not None:
            update_fields = set(update_fields)
            if not {'product', 'product_id'} & update_fields:
                persisted_product_id = old.product_id
            if 'quantity' not in update_fields:
                persisted_quantity = old.quantity
        super().save(*args, **kwargs)
        self.product_id = persisted_product_id
        self.quantity = persisted_quantity
        reference = _stock_reference('purchase', self.purchase_id, self.pk, 'update')
        if old.product_id != persisted_product_id:
            _apply_line_stock_changes(
                stock_change_for_delta(
                    product=old.product_id,
                    delta=-old.quantity,
                    reason=f'Changement produit ligne achat {self.purchase.reference}',
                    user=user,
                    source_type=StockMovement.SOURCE_PURCHASE,
                    source_reference=reference,
                ),
                stock_change_for_delta(
                    product=persisted_product_id,
                    delta=persisted_quantity,
                    reason=f'Changement produit ligne achat {self.purchase.reference}',
                    user=user,
                    source_type=StockMovement.SOURCE_PURCHASE,
                    source_reference=reference,
                ),
            )
        else:
            _apply_line_stock_changes(
                stock_change_for_delta(
                    product=persisted_product_id,
                    delta=persisted_quantity - old.quantity,
                    reason=f'Modification ligne achat {self.purchase.reference}',
                    user=user,
                    source_type=StockMovement.SOURCE_PURCHASE,
                    source_reference=reference,
                )
            )

    @transaction.atomic
    def delete(self, *args, **kwargs):
        locked = PurchaseLine.objects.select_for_update().select_related('purchase').get(pk=self.pk)
        _apply_line_stock_changes(
            stock_change_for_delta(
                product=locked.product_id,
                delta=-locked.quantity,
                reason=f'Suppression ligne achat {locked.purchase.reference}',
                user=getattr(self, '_stock_user', None),
                source_type=StockMovement.SOURCE_PURCHASE,
                source_reference=_stock_reference('purchase', locked.purchase_id, locked.pk, 'delete'),
            )
        )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.entered_quantity} {self.packaging_name}"


class Sale(models.Model):
    CASH = 'cash'
    CHEQUE = 'cheque'
    TRANSFER = 'transfer'
    PAYMENT_CHOICES = [
        (CASH, 'Espèces'),
        (CHEQUE, 'Chèque'),
        (TRANSFER, 'Virement'),
    ]
    SNAPSHOT_LEGACY_UNKNOWN = 'legacy_unknown'
    SNAPSHOT_COMPLETE = 'complete'
    SNAPSHOT_MANUALLY_VERIFIED = 'manually_verified'
    SNAPSHOT_INTEGRITY_CHOICES = [
        (SNAPSHOT_LEGACY_UNKNOWN, _('Historique non certifié')),
        (SNAPSHOT_COMPLETE, _('Snapshot historique complet')),
        (SNAPSHOT_MANUALLY_VERIFIED, _('Historique vérifié manuellement')),
    ]

    invoice_number = models.CharField('Numéro facture', max_length=100, unique=True)
    ticket_number = models.CharField('Numéro ticket', max_length=100, unique=True, blank=True, null=True)
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='sales')
    total = models.DecimalField('Total TTC', max_digits=14, decimal_places=2)
    discount = models.DecimalField('Remise', max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField('TVA (%)', max_digits=5, decimal_places=2, default=0)
    payment_type = models.CharField('Mode de paiement', max_length=20, choices=PAYMENT_CHOICES, default=CASH, blank=True)
    payment_tracking_initialized = models.BooleanField('Suivi des règlements initialisé', default=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_sales', verbose_name='Caissier',
    )
    loading_order = models.ForeignKey(
        'inventory.LoadingOrder', on_delete=models.PROTECT, null=True, blank=True,
        related_name='sales', verbose_name=_('Chargement opérateur'),
    )
    created_at = models.DateTimeField('Date', default=timezone.now, db_index=True)
    issued_at = models.DateTimeField(_('Date d’émission'), null=True, blank=True, editable=False)
    snapshot_version = models.PositiveSmallIntegerField(default=0, editable=False)
    snapshot_integrity_status = models.CharField(
        _('Intégrité historique'), max_length=24, choices=SNAPSHOT_INTEGRITY_CHOICES,
        default=SNAPSHOT_LEGACY_UNKNOWN, editable=False,
    )
    currency_snapshot = models.CharField(_('Devise historique'), max_length=3, default='DZD', editable=False)
    subtotal_ht_snapshot = models.DecimalField(
        _('Total HT historique'), max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
    )
    tax_amount_snapshot = models.DecimalField(
        _('Montant TVA historique'), max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
    )
    company_name_snapshot = models.CharField(max_length=200, blank=True, editable=False)
    company_name_ar_snapshot = models.CharField(max_length=200, blank=True, editable=False)
    company_address_snapshot = models.CharField(max_length=255, blank=True, editable=False)
    company_phone_snapshot = models.CharField(max_length=50, blank=True, editable=False)
    company_email_snapshot = models.EmailField(blank=True, editable=False)
    company_rc_number_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    company_tax_number_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    company_nis_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    company_article_number_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    company_logo_snapshot = models.CharField(max_length=500, blank=True, editable=False)
    client_name_snapshot = models.CharField(max_length=200, blank=True, editable=False)
    client_phone_snapshot = models.CharField(max_length=50, blank=True, editable=False)
    client_address_snapshot = models.CharField(max_length=255, blank=True, editable=False)
    client_tax_number_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    client_nis_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    client_article_number_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    client_trade_register_number_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    client_customer_type_snapshot = models.CharField(max_length=20, blank=True, editable=False)

    objects = CommercialDocumentQuerySet.as_manager()

    class Meta:
        verbose_name = 'Vente'
        verbose_name_plural = 'Ventes'

    def __str__(self):
        return self.invoice_number

    @property
    def amount_paid(self):
        return _document_paid_amount(self)

    @property
    def balance_due(self):
        return max(_decimal(self.total) - self.amount_paid, ZERO)

    @property
    def payment_status(self):
        return _payment_status(self)

    @property
    def has_certified_snapshot(self):
        return self.snapshot_integrity_status in {
            self.SNAPSHOT_COMPLETE,
            self.SNAPSHOT_MANUALLY_VERIFIED,
        }

    @transaction.atomic
    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if self.pk and (update_fields is None or 'total' in update_fields):
            Sale.objects.select_for_update().get(pk=self.pk)
            if self.total < _document_paid_amount(self):
                raise ValidationError({'total': _('Le total ne peut pas être inférieur aux règlements déjà enregistrés.')})
        return super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        Sale.objects.select_for_update().get(pk=self.pk)
        user = getattr(self, '_stock_user', None)
        for line in list(self.lines.all()):
            line._stock_user = user
            line.delete()
        return super().delete(*args, **kwargs)


class InvoiceSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Séquence facture'
        verbose_name_plural = 'Séquences factures'

    def __str__(self):
        return f'{self.year} - {self.last_number}'


class TicketSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Séquence ticket'
        verbose_name_plural = 'Séquences tickets'

    def __str__(self):
        return f'{self.year} - {self.last_number}'


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField('Quantité')
    packaging = models.ForeignKey(
        ProductPackaging, on_delete=models.PROTECT, null=True, blank=True,
        related_name='sale_lines', verbose_name='Conditionnement',
    )
    packaging_name = models.CharField('Conditionnement historique', max_length=100, blank=True)
    packaging_factor = models.PositiveIntegerField('Facteur historique', default=1, editable=False)
    packaging_quantity = models.PositiveIntegerField('Quantité vendue', default=1)
    unit_price = models.DecimalField('Prix unitaire', max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(
        'Coût unitaire historique',
        max_digits=12,
        decimal_places=2,
        editable=False,
    )
    product_name_snapshot = models.CharField(max_length=255, blank=True, editable=False)
    product_reference_snapshot = models.CharField(max_length=100, blank=True, editable=False)
    unit_name_snapshot = models.CharField(max_length=50, blank=True, editable=False)
    line_total_ht_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
    )

    objects = StockTrackedLineQuerySet.as_manager()

    @property
    def stock_quantity(self):
        return self.quantity

    @property
    def minimum_sale_price(self):
        return self.unit_cost * self.packaging_factor

    def line_total(self):
        if self.line_total_ht_snapshot is not None:
            return self.line_total_ht_snapshot
        return self.packaging_quantity * self.unit_price

    @property
    def invoice_product_name(self):
        if self.sale.has_certified_snapshot:
            return self.product_name_snapshot
        return self.product.name

    @property
    def invoice_product_reference(self):
        if self.sale.has_certified_snapshot:
            return self.product_reference_snapshot
        return self.product.reference

    @property
    def invoice_unit_name(self):
        if self.sale.has_certified_snapshot:
            return self.unit_name_snapshot
        return self.product.unit.name if self.product.unit_id else ''

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.quantity = int(self.quantity)
        self.packaging_quantity = int(self.packaging_quantity)
        self.unit_price = Decimal(str(self.unit_price))
        user = getattr(self, '_stock_user', None)
        if self._state.adding:
            # Lock only the product row. ``Product.unit`` is nullable, so joining it
            # in a ``SELECT ... FOR UPDATE`` makes PostgreSQL reject the query with
            # "FOR UPDATE cannot be applied to the nullable side of an outer join".
            locked_product = Product.objects.select_for_update().get(pk=self.product_id)
            if self.packaging_id:
                packaging = ProductPackaging.objects.select_for_update().get(pk=self.packaging_id)
                if packaging.product_id != self.product_id or not packaging.is_active:
                    raise ValidationError({'packaging': _('Conditionnement invalide ou inactif.')})
                self.packaging_factor = packaging.conversion_factor
                self.packaging_name = packaging.name
            else:
                self.packaging_factor = 1
                self.packaging_name = locked_product.unit.name if locked_product.unit_id else _('Unité')
                self.packaging_quantity = self.quantity
            if self.packaging_quantity < 1:
                raise ValidationError({'packaging_quantity': _('La quantité doit être strictement positive.')})
            if self.quantity != self.packaging_quantity * self.packaging_factor:
                raise ValidationError({'quantity': _('La conversion du conditionnement est incohérente.')})
            self.unit_cost = locked_product.purchase_price
            self.product_name_snapshot = locked_product.name
            self.product_reference_snapshot = locked_product.reference
            self.unit_name_snapshot = locked_product.unit.name if locked_product.unit_id else ''
            self.line_total_ht_snapshot = (
                Decimal(self.packaging_quantity) * self.unit_price
            ).quantize(Decimal('0.01'))
            if self.unit_price < self.minimum_sale_price:
                raise ValidationError({'unit_price': _(
                    "Impossible de vendre un produit à un prix inférieur à son prix d'achat."
                )})
            super().save(*args, **kwargs)
            _apply_sale_stock_delta(
                sale=self.sale, product_id=self.product_id, delta=-self.quantity, user=user,
                reason=f'Création ligne vente {self.sale.invoice_number}',
                reference=_stock_reference('sale', self.sale_id, self.pk, 'create'),
            )
            return

        old = SaleLine.objects.select_for_update().get(pk=self.pk)
        update_fields = kwargs.get('update_fields')
        persisted_product_id = self.product_id
        persisted_quantity = self.quantity
        if update_fields is not None:
            update_fields = set(update_fields)
            if not {'product', 'product_id'} & update_fields:
                persisted_product_id = old.product_id
            if 'quantity' not in update_fields:
                persisted_quantity = old.quantity
        locked_products = {
            product.pk: product
            for product in Product.objects.select_for_update()
            .filter(pk__in={old.product_id, persisted_product_id})
            .order_by('pk')
        }
        if self.packaging_id:
            packaging = ProductPackaging.objects.select_for_update().get(pk=self.packaging_id)
            if packaging.product_id != persisted_product_id or not packaging.is_active:
                raise ValidationError({'packaging': _('Conditionnement invalide ou inactif.')})
            self.packaging_factor = packaging.conversion_factor
            self.packaging_name = packaging.name
        else:
            product_for_label = locked_products[persisted_product_id]
            self.packaging_factor = 1
            self.packaging_name = product_for_label.unit.name if product_for_label.unit_id else _('Unité')
            self.packaging_quantity = persisted_quantity
        if self.packaging_quantity < 1 or persisted_quantity != self.packaging_quantity * self.packaging_factor:
            raise ValidationError({'quantity': _('La conversion du conditionnement est incohérente.')})
        if old.product_id != persisted_product_id:
            self.unit_cost = locked_products[persisted_product_id].purchase_price
            if update_fields is not None:
                update_fields.add('unit_cost')
                kwargs['update_fields'] = update_fields
        else:
            self.unit_cost = old.unit_cost
        if self.unit_price < self.minimum_sale_price:
            raise ValidationError({'unit_price': _(
                "Impossible de vendre un produit à un prix inférieur à son prix d'achat."
            )})
        product_for_snapshot = locked_products[persisted_product_id]
        self.product_name_snapshot = product_for_snapshot.name
        self.product_reference_snapshot = product_for_snapshot.reference
        self.unit_name_snapshot = product_for_snapshot.unit.name if product_for_snapshot.unit_id else ''
        self.line_total_ht_snapshot = (
            Decimal(self.packaging_quantity) * self.unit_price
        ).quantize(Decimal('0.01'))
        if update_fields is not None:
            update_fields.update({
                'product_name_snapshot', 'product_reference_snapshot',
                'unit_name_snapshot', 'line_total_ht_snapshot',
            })
            kwargs['update_fields'] = update_fields
        super().save(*args, **kwargs)
        self.product_id = persisted_product_id
        self.quantity = persisted_quantity
        reference = _stock_reference('sale', self.sale_id, self.pk, 'update')
        if old.product_id != persisted_product_id:
            _apply_sale_stock_delta(
                sale=self.sale, product_id=old.product_id, delta=old.quantity, user=user,
                reason=f'Changement produit ligne vente {self.sale.invoice_number}', reference=reference,
            )
            _apply_sale_stock_delta(
                sale=self.sale, product_id=persisted_product_id, delta=-persisted_quantity, user=user,
                reason=f'Changement produit ligne vente {self.sale.invoice_number}', reference=reference,
            )
        else:
            _apply_sale_stock_delta(
                sale=self.sale, product_id=persisted_product_id,
                delta=old.quantity - persisted_quantity, user=user,
                reason=f'Modification ligne vente {self.sale.invoice_number}', reference=reference,
            )

    @transaction.atomic
    def delete(self, *args, **kwargs):
        locked = SaleLine.objects.select_for_update().select_related('sale').get(pk=self.pk)
        _apply_sale_stock_delta(
            sale=locked.sale, product_id=locked.product_id, delta=locked.quantity,
            reason=f'Suppression ligne vente {locked.sale.invoice_number}',
            user=getattr(self, '_stock_user', None),
            reference=_stock_reference('sale', locked.sale_id, locked.pk, 'delete'),
        )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"


class PaymentQuerySet(StockAwareDeleteQuerySet):
    def update(self, **kwargs):
        raise ValidationError(_('Un règlement doit être modifié avec save().'))

    def bulk_create(self, objs, **kwargs):
        raise ValidationError(_('La création groupée de règlements est interdite.'))

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError(_('La mise à jour groupée de règlements est interdite.'))


class Payment(models.Model):
    CASH = 'cash'
    CHEQUE = 'cheque'
    TRANSFER = 'transfer'
    PAYMENT_CHOICES = [
        (CASH, 'Espèces'),
        (CHEQUE, 'Chèque'),
        (TRANSFER, 'Virement'),
    ]

    reference = models.CharField(
        'Référence paiement',
        max_length=100,
        unique=True,
        blank=True,
        editable=False,
    )
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, null=True, blank=True, related_name='payments')
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, null=True, blank=True, related_name='payments')
    amount = models.DecimalField('Montant', max_digits=14, decimal_places=2)
    payment_type = models.CharField('Type paiement', max_length=20, choices=PAYMENT_CHOICES)
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='recorded_payments',
    )
    created_at = models.DateTimeField('Date', default=timezone.now, db_index=True)

    objects = PaymentQuerySet.as_manager()

    class Meta:
        verbose_name = 'Paiement'
        verbose_name_plural = 'Paiements'
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(sale__isnull=False, purchase__isnull=True)
                    | Q(sale__isnull=True, purchase__isnull=False)
                ),
                name='commerce_payment_exactly_one_document',
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='commerce_payment_amount_positive',
            ),
        ]
        indexes = [
            models.Index(
                fields=['sale', '-created_at'],
                name='commerce_pay_sale_date_idx',
            ),
            models.Index(
                fields=['purchase', '-created_at'],
                name='commerce_pay_buy_date_idx',
            ),
        ]

    @property
    def document(self):
        return self.sale or self.purchase

    def clean(self):
        super().clean()
        if bool(self.sale_id) == bool(self.purchase_id):
            raise ValidationError(
                _('Un règlement doit concerner exactement une vente ou un achat.'),
                code='invalid_payment_document',
            )
        if self.amount is not None and self.amount <= ZERO:
            raise ValidationError({'amount': _('Le montant doit être strictement positif.')})

    def _generate_reference(self):
        year = (self.created_at or timezone.now()).year
        for _attempt in range(10):
            candidate = f'PAY-{year}-{uuid4().hex[:16].upper()}'
            if not Payment.objects.filter(reference=candidate).exists():
                return candidate
        raise ValidationError({'reference': _('Impossible de générer une référence de règlement unique.')})

    @transaction.atomic
    def save(self, *args, **kwargs):
        old = None
        if self.pk:
            old = Payment.objects.select_for_update().get(pk=self.pk)
            if self.reference != old.reference:
                raise ValidationError(
                    {'reference': _('La rÃ©fÃ©rence dâ€™un rÃ¨glement est immuable.')}
                )

        sale_ids = {value for value in (self.sale_id, getattr(old, 'sale_id', None)) if value}
        purchase_ids = {value for value in (self.purchase_id, getattr(old, 'purchase_id', None)) if value}
        locked_sales = {
            sale.pk: sale
            for sale in Sale.objects.select_for_update().filter(pk__in=sale_ids).order_by('pk')
        }
        locked_purchases = {
            purchase.pk: purchase
            for purchase in Purchase.objects.select_for_update().filter(pk__in=purchase_ids).order_by('pk')
        }

        self.full_clean()
        document = locked_sales.get(self.sale_id) or locked_purchases.get(self.purchase_id)
        if document is None:
            raise ValidationError(_('Le document associé au règlement est introuvable.'))
        paid_without_current = (
            Payment.objects.filter(
                sale_id=self.sale_id if self.sale_id else None,
                purchase_id=self.purchase_id if self.purchase_id else None,
            )
            .exclude(pk=self.pk)
            .aggregate(total=Sum('amount'))['total']
            or ZERO
        )
        remaining = document.total - paid_without_current
        if self.amount > remaining:
            raise ValidationError(
                {
                    'amount': _(
                        'Surpaiement interdit : le solde restant est de %(remaining).2f DZD.'
                    ) % {'remaining': remaining}
                }
            )

        if not self.reference:
            self.reference = self._generate_reference()
        result = super().save(*args, **kwargs)
        if self.sale_id and not document.payment_tracking_initialized:
            Sale.objects.filter(pk=self.sale_id).update(payment_tracking_initialized=True)
            document.payment_tracking_initialized = True
        if self.purchase_id and not document.payment_tracking_initialized:
            Purchase.objects.filter(pk=self.purchase_id).update(payment_tracking_initialized=True)
            document.payment_tracking_initialized = True
        return result

    @transaction.atomic
    def delete(self, *args, **kwargs):
        locked = Payment.objects.select_for_update().get(pk=self.pk)
        if locked.sale_id:
            Sale.objects.select_for_update().get(pk=locked.sale_id)
        else:
            Purchase.objects.select_for_update().get(pk=locked.purchase_id)
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.reference
