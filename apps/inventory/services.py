from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import Product, StockMovement


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
