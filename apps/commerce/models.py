from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.inventory.models import Client, Product, StockMovement, Supplier
from apps.inventory.services import record_stock_movements, stock_change_for_delta


ZERO = Decimal('0.00')


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _stock_reference(kind, parent_id, line_id, action):
    return f'{kind}:{parent_id}:line:{line_id}:{action}'


def _apply_line_stock_changes(*changes):
    record_stock_movements([change for change in changes if change is not None])


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
        if {'quantity', 'product', 'product_id', 'unit_cost'} & kwargs.keys():
            raise ValidationError(
                'La quantité et le produit d’une ligne commerciale doivent être modifiés avec save().'
            )
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('La création groupée contournerait le journal de stock.')

    def bulk_update(self, objs, fields, **kwargs):
        if {'quantity', 'product', 'product_id', 'unit_cost'} & set(fields):
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
    purchase_price = models.DecimalField("Prix d'achat", max_digits=12, decimal_places=2)

    objects = StockTrackedLineQuerySet.as_manager()

    def line_total(self):
        return self.quantity * self.purchase_price

    @transaction.atomic
    def save(self, *args, **kwargs):
        user = getattr(self, '_stock_user', None)
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

        old = PurchaseLine.objects.select_for_update().get(pk=self.pk)
        update_fields = kwargs.get('update_fields')
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
        return f"{self.product.name} - {self.quantity}"


class Sale(models.Model):
    CASH = 'cash'
    CHEQUE = 'cheque'
    TRANSFER = 'transfer'
    PAYMENT_CHOICES = [
        (CASH, 'Espèces'),
        (CHEQUE, 'Chèque'),
        (TRANSFER, 'Virement'),
    ]

    invoice_number = models.CharField('Numéro facture', max_length=100, unique=True)
    ticket_number = models.CharField('Numéro ticket', max_length=100, unique=True, blank=True, null=True)
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='sales')
    total = models.DecimalField('Total TTC', max_digits=14, decimal_places=2)
    discount = models.DecimalField('Remise', max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField('TVA (%)', max_digits=5, decimal_places=2, default=0)
    payment_type = models.CharField('Mode de paiement', max_length=20, choices=PAYMENT_CHOICES, default=CASH, blank=True)
    payment_tracking_initialized = models.BooleanField('Suivi des règlements initialisé', default=True)
    created_at = models.DateTimeField('Date', default=timezone.now, db_index=True)

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
    unit_price = models.DecimalField('Prix unitaire', max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(
        'Coût unitaire historique',
        max_digits=12,
        decimal_places=2,
        editable=False,
    )

    objects = StockTrackedLineQuerySet.as_manager()

    @property
    def stock_quantity(self):
        return self.quantity

    @property
    def minimum_sale_price(self):
        return self.unit_cost

    def line_total(self):
        return self.quantity * self.unit_price

    @transaction.atomic
    def save(self, *args, **kwargs):
        user = getattr(self, '_stock_user', None)
        if self._state.adding:
            locked_product = Product.objects.select_for_update().only('pk', 'purchase_price').get(
                pk=self.product_id
            )
            self.unit_cost = locked_product.purchase_price
            super().save(*args, **kwargs)
            _apply_line_stock_changes(
                stock_change_for_delta(
                    product=self.product_id,
                    delta=-self.quantity,
                    reason=f'Création ligne vente {self.sale.invoice_number}',
                    user=user,
                    source_type=StockMovement.SOURCE_SALE,
                    source_reference=_stock_reference('sale', self.sale_id, self.pk, 'create'),
                )
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
            .only('pk', 'purchase_price')
            .filter(pk__in={old.product_id, persisted_product_id})
            .order_by('pk')
        }
        if old.product_id != persisted_product_id:
            self.unit_cost = locked_products[persisted_product_id].purchase_price
            if update_fields is not None:
                update_fields.add('unit_cost')
                kwargs['update_fields'] = update_fields
        else:
            self.unit_cost = old.unit_cost
        super().save(*args, **kwargs)
        self.product_id = persisted_product_id
        self.quantity = persisted_quantity
        reference = _stock_reference('sale', self.sale_id, self.pk, 'update')
        if old.product_id != persisted_product_id:
            _apply_line_stock_changes(
                stock_change_for_delta(
                    product=old.product_id,
                    delta=old.quantity,
                    reason=f'Changement produit ligne vente {self.sale.invoice_number}',
                    user=user,
                    source_type=StockMovement.SOURCE_SALE,
                    source_reference=reference,
                ),
                stock_change_for_delta(
                    product=persisted_product_id,
                    delta=-persisted_quantity,
                    reason=f'Changement produit ligne vente {self.sale.invoice_number}',
                    user=user,
                    source_type=StockMovement.SOURCE_SALE,
                    source_reference=reference,
                ),
            )
        else:
            _apply_line_stock_changes(
                stock_change_for_delta(
                    product=persisted_product_id,
                    delta=old.quantity - persisted_quantity,
                    reason=f'Modification ligne vente {self.sale.invoice_number}',
                    user=user,
                    source_type=StockMovement.SOURCE_SALE,
                    source_reference=reference,
                )
            )

    @transaction.atomic
    def delete(self, *args, **kwargs):
        locked = SaleLine.objects.select_for_update().select_related('sale').get(pk=self.pk)
        _apply_line_stock_changes(
            stock_change_for_delta(
                product=locked.product_id,
                delta=locked.quantity,
                reason=f'Suppression ligne vente {locked.sale.invoice_number}',
                user=getattr(self, '_stock_user', None),
                source_type=StockMovement.SOURCE_SALE,
                source_reference=_stock_reference('sale', locked.sale_id, locked.pk, 'delete'),
            )
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
