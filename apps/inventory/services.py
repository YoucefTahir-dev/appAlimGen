from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    LoadingOrder, LoadingOrderLine, LoadingOrderSequence, OperatorStock,
    OperatorStockMovement, Product, StockMovement,
)


@transaction.atomic
def create_product_from_form(form, *, user, packaging_formset=None):
    """Persist a validated product form and its initial stock through the ledger."""
    initial_quantity = form.cleaned_data.get('quantity', 0)
    product = form.save(commit=False)
    product.quantity = 0
    product.save()
    if packaging_formset is not None and packaging_formset.is_bound:
        packaging_formset.instance = product
        packaging_formset.save()
    if initial_quantity:
        record_stock_movement(
            product=product, movement_type=StockMovement.ENTRY,
            quantity=initial_quantity, reason=_('Stock initial du produit'),
            user=user, source_type=StockMovement.SOURCE_PRODUCT,
            source_reference=product.reference,
        )
    return product


def _movement_delta(movement_type, quantity, current_balance):
    if movement_type == StockMovement.ENTRY:
        return quantity
    if movement_type == StockMovement.EXIT:
        return -quantity
    if movement_type == StockMovement.ADJUSTMENT:
        return quantity - current_balance
    raise ValidationError({'movement_type': _('Type de mouvement de stock invalide.')})


@dataclass(frozen=True)
class StockChange:
    """One requested ledger entry, applied by ``record_stock_movements``."""

    product: object
    movement_type: str
    quantity: int
    reason: str = ''
    user: object = None
    source_type: str = StockMovement.SOURCE_MANUAL
    source_reference: str = ''
    reversal_of: object = None


def _product_id(product):
    product_id = product.pk if isinstance(product, Product) else product
    if not product_id:
        raise ValidationError({'product': _('Le produit doit exister avant de modifier son stock.')})
    return product_id


@transaction.atomic
def record_stock_movements(changes):
    """Apply and journal several stock changes as a single transaction.

    Products are locked in a stable order before the first write. The conditional
    update is deliberately retained as a compare-and-swap guard for databases or
    execution paths where row locking is degraded.
    """
    changes = [change if isinstance(change, StockChange) else StockChange(**change) for change in changes]
    if not changes:
        return []

    product_ids = sorted({_product_id(change.product) for change in changes})
    locked_products = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')
    }
    if len(locked_products) != len(product_ids):
        raise ObjectDoesNotExist(_('Un produit concerné par le mouvement de stock est introuvable.'))

    movements = []
    for change in changes:
        product = locked_products[_product_id(change.product)]
        current_balance = product.quantity
        delta = _movement_delta(change.movement_type, change.quantity, current_balance)
        new_balance = current_balance + delta

        movement = StockMovement(
            product=product,
            movement_type=change.movement_type,
            quantity=change.quantity,
            reason=change.reason,
            applied_delta=delta,
            balance_before=current_balance,
            balance_after=new_balance if new_balance >= 0 else None,
            source_type=change.source_type,
            source_reference=str(change.source_reference or '')[:100],
            created_by=change.user if getattr(change.user, 'is_authenticated', False) else None,
            reversal_of=change.reversal_of,
        )
        movement.full_clean()

        if new_balance < 0:
            raise ValidationError(
                {
                    'quantity': _(
                        'Stock insuffisant : %(available)s unité(s) disponible(s).'
                    ) % {'available': current_balance}
                }
            )

        updated = Product.objects.filter(pk=product.pk, quantity=current_balance).update(
            quantity=new_balance
        )
        if updated != 1:
            raise ValidationError(
                _('Le stock a été modifié simultanément. Veuillez recommencer l’opération.')
            )

        movement._ledger_write_allowed = True
        movement.save(force_insert=True)
        product.quantity = new_balance
        movement.product = product
        movements.append(movement)

    return movements


@transaction.atomic
def record_stock_movement(
    *,
    product,
    movement_type,
    quantity,
    reason='',
    user=None,
    source_type=StockMovement.SOURCE_MANUAL,
    source_reference='',
    reversal_of=None,
):
    """Apply one stock change and append its audit record in the same transaction."""
    return record_stock_movements(
        [
            StockChange(
                product=product,
                movement_type=movement_type,
                quantity=quantity,
                reason=reason,
                user=user,
                source_type=source_type,
                source_reference=source_reference,
                reversal_of=reversal_of,
            )
        ]
    )[0]


def stock_change_for_delta(*, product, delta, reason='', user=None, source_type, source_reference=''):
    """Build a ledger change from a signed quantity delta, or ``None`` for zero."""
    if delta == 0:
        return None
    return StockChange(
        product=product,
        movement_type=StockMovement.ENTRY if delta > 0 else StockMovement.EXIT,
        quantity=abs(delta),
        reason=reason,
        user=user,
        source_type=source_type,
        source_reference=source_reference,
    )


@transaction.atomic
def reverse_stock_movement(movement, *, user=None, reason=''):
    """Append an exact compensating movement without deleting audit history."""
    movement_id = movement.pk if isinstance(movement, StockMovement) else movement
    original = (
        StockMovement.objects.select_for_update()
        .select_related('product')
        .get(pk=movement_id)
    )
    if original.applied_delta is None:
        raise ValidationError(
            _('Ce mouvement historique ne peut pas être annulé automatiquement avant rapprochement.')
        )
    if original.reversal_of_id:
        raise ValidationError(_('Un mouvement d’annulation ne peut pas être annulé.'))
    if StockMovement.objects.filter(reversal_of=original).exists():
        raise ValidationError(_('Ce mouvement a déjà été annulé.'))

    product = Product.objects.select_for_update().get(pk=original.product_id)
    target_balance = product.quantity - original.applied_delta
    if target_balance < 0:
        raise ValidationError(
            _('Annulation impossible : le stock disponible ne permet pas de retirer les unités concernées.')
        )

    return record_stock_movement(
        product=product,
        movement_type=StockMovement.ADJUSTMENT,
        quantity=target_balance,
        reason=reason or _('Annulation du mouvement #%(movement_id)s') % {'movement_id': original.pk},
        user=user,
        source_type=StockMovement.SOURCE_REVERSAL,
        source_reference=str(original.pk),
        reversal_of=original,
    )


def generate_loading_number():
    year = timezone.now().year
    sequence, _ = LoadingOrderSequence.objects.select_for_update().get_or_create(year=year)
    sequence.last_number += 1
    sequence.save(update_fields=('last_number',))
    return f'CHG-{year}-{sequence.last_number:06d}'


def active_loading_for_operator(operator, *, lock=False):
    queryset = LoadingOrder.objects.filter(
        operator=operator, status__in=(LoadingOrder.VALIDATED, LoadingOrder.IN_PROGRESS),
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.order_by('pk').first()


@transaction.atomic
def adjust_operator_stock(*, loading_order, product, delta, movement_type, user=None, source_reference=''):
    if not delta:
        return None
    order = LoadingOrder.objects.select_for_update().get(pk=loading_order.pk if hasattr(loading_order, 'pk') else loading_order)
    product_id = product.pk if hasattr(product, 'pk') else product
    stock, _ = OperatorStock.objects.get_or_create(operator_id=order.operator_id, product_id=product_id)
    stock = OperatorStock.objects.select_for_update().get(pk=stock.pk)
    before = stock.quantity
    after = before + int(delta)
    if after < 0:
        raise ValidationError(
            {'quantity': ValidationError(
                _('Stock opérateur insuffisant : %(available)s unité(s) disponible(s).') % {'available': before},
                code='insufficient_operator_stock',
            )}
        )
    updated = OperatorStock.objects.filter(pk=stock.pk, quantity=before).update(quantity=after)
    if updated != 1:
        raise ValidationError(_('Le stock opérateur a été modifié simultanément. Veuillez recommencer.'))
    movement = OperatorStockMovement(
        operator_id=order.operator_id, product_id=product_id, loading_order=order,
        movement_type=movement_type, quantity=abs(int(delta)), applied_delta=int(delta),
        balance_before=before, balance_after=after,
        source_reference=str(source_reference or '')[:100],
        created_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    movement._ledger_write_allowed = True
    movement.save(force_insert=True)
    return movement


@transaction.atomic
def validate_loading_order(loading_order, *, user=None):
    order_id = loading_order.pk if hasattr(loading_order, 'pk') else loading_order
    order = LoadingOrder.objects.select_for_update().select_related('operator').get(pk=order_id)
    if order.status != LoadingOrder.DRAFT:
        raise ValidationError({'status': _('Seul un chargement brouillon peut être validé.')})
    if LoadingOrder.objects.select_for_update().filter(
        operator=order.operator, status__in=(LoadingOrder.VALIDATED, LoadingOrder.IN_PROGRESS),
    ).exclude(pk=order.pk).exists():
        raise ValidationError({'operator': _('Cet opérateur possède déjà un chargement actif.')})
    lines = list(order.lines.select_related('product').order_by('product_id'))
    if not lines:
        raise ValidationError({'lines': _('Le chargement doit contenir au moins un produit.')})
    if any(line.quantity <= 0 for line in lines):
        raise ValidationError({'lines': _('Toutes les quantités doivent être strictement positives.')})
    changes = [
        StockChange(
            product=line.product_id, movement_type=StockMovement.EXIT, quantity=line.quantity,
            reason=_('Validation du chargement %(number)s') % {'number': order.number},
            user=user, source_type=StockMovement.SOURCE_LOADING, source_reference=order.number,
        ) for line in lines
    ]
    record_stock_movements(changes)
    for line in lines:
        adjust_operator_stock(
            loading_order=order, product=line.product_id, delta=line.quantity,
            movement_type=OperatorStockMovement.LOAD, user=user, source_reference=order.number,
        )
    order.status = LoadingOrder.IN_PROGRESS
    order.validated_at = timezone.now()
    order.save(update_fields=('status', 'validated_at'))
    return order


@transaction.atomic
def close_loading_order(loading_order, *, user=None):
    order_id = loading_order.pk if hasattr(loading_order, 'pk') else loading_order
    order = LoadingOrder.objects.select_for_update().get(pk=order_id)
    if order.status not in (LoadingOrder.VALIDATED, LoadingOrder.IN_PROGRESS):
        raise ValidationError({'status': _('Seul un chargement actif peut être clôturé.')})
    lines = list(order.lines.select_for_update().order_by('product_id'))
    stocks = {
        stock.product_id: stock
        for stock in OperatorStock.objects.select_for_update().filter(
            operator_id=order.operator_id, product_id__in=[line.product_id for line in lines],
        ).order_by('product_id')
    }
    depot_returns = []
    for line in lines:
        remaining = stocks.get(line.product_id).quantity if line.product_id in stocks else 0
        if remaining > line.quantity:
            raise ValidationError({'quantity': _('Le solde opérateur est incohérent avec ce chargement.')})
        if remaining:
            depot_returns.append(StockChange(
                product=line.product_id, movement_type=StockMovement.ENTRY, quantity=remaining,
                reason=_('Retour du chargement %(number)s') % {'number': order.number},
                user=user, source_type=StockMovement.SOURCE_LOADING, source_reference=order.number,
            ))
            adjust_operator_stock(
                loading_order=order, product=line.product_id, delta=-remaining,
                movement_type=OperatorStockMovement.RETURN, user=user, source_reference=order.number,
            )
        line.returned_quantity = remaining
        line.save(update_fields=('returned_quantity',))
    record_stock_movements(depot_returns)
    order.status = LoadingOrder.CLOSED
    order.closed_at = timezone.now()
    order.save(update_fields=('status', 'closed_at'))
    return order


@transaction.atomic
def cancel_loading_order(loading_order):
    order_id = loading_order.pk if hasattr(loading_order, 'pk') else loading_order
    order = LoadingOrder.objects.select_for_update().get(pk=order_id)
    if order.status != LoadingOrder.DRAFT:
        raise ValidationError({'status': _('Seul un brouillon peut être annulé.')})
    order.status = LoadingOrder.CANCELLED
    order.save(update_fields=('status',))
    return order
