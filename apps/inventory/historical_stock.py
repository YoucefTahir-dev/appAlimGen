from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    ConditioningCorrection,
    HistoricalStockConversion,
    HistoricalStockConversionLocation,
    LoadingOrder,
    LoadingOrderLine,
    OperatorStock,
    OperatorStockMovement,
    Product,
    ProductPackaging,
    StockMovement,
    Unit,
)
from .services import record_stock_movement


ACTIVE_LOADING_STATUSES = (
    LoadingOrder.DRAFT,
    LoadingOrder.VALIDATED,
    LoadingOrder.IN_PROGRESS,
)


@dataclass(frozen=True)
class ConversionLocationPlan:
    operator_id: int
    operator_name: str
    cartons_before: int
    pieces_after: int


@dataclass(frozen=True)
class ConversionPlan:
    product_id: int
    reference: str
    name: str
    factor: int
    factor_source: str
    conditioning_status: str
    depot_cartons_before: int
    depot_pieces_after: int
    minimum_cartons_before: int
    minimum_pieces_after: int
    operator_locations: tuple[ConversionLocationPlan, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    already_converted: bool = False

    @property
    def eligible(self):
        return not self.blockers and not self.already_converted


def _carton_for(product):
    return next(
        (packaging for packaging in product.packagings.all() if packaging.kind == ProductPackaging.CARTON),
        None,
    )


def build_conversion_plans(*, default_factor=HistoricalStockConversion.DEFAULT_FACTOR, queryset=None):
    """Return a complete read-only simulation for every historical product."""
    default_factor = int(default_factor)
    if default_factor < 2:
        raise ValidationError({'default_factor': _('Le facteur par défaut doit être au moins égal à 2.')})
    queryset = queryset if queryset is not None else Product.objects.filter(unit_mode=Product.UnitMode.LEGACY)
    products = list(queryset.prefetch_related('packagings').order_by('pk'))
    product_ids = [product.pk for product in products]
    operator_rows = OperatorStock.objects.filter(product_id__in=product_ids).select_related(
        'operator',
    ).order_by('product_id', 'operator_id')
    operators_by_product = {}
    for stock in operator_rows:
        operators_by_product.setdefault(stock.product_id, []).append(stock)
    converted_ids = set(
        HistoricalStockConversion.objects.filter(product_id__in=product_ids).values_list(
            'product_id', flat=True,
        )
    )
    blocked_ids = set(
        LoadingOrderLine.objects.filter(
            product_id__in=product_ids,
            loading_order__status__in=ACTIVE_LOADING_STATUSES,
        ).values_list('product_id', flat=True)
    )

    plans = []
    for product in products:
        carton = _carton_for(product)
        factor = carton.conversion_factor if carton and carton.conversion_factor >= 2 else default_factor
        source = 'confirmed' if carton and carton.conversion_factor >= 2 else 'default'
        locations = tuple(
            ConversionLocationPlan(
                operator_id=stock.operator_id,
                operator_name=stock.operator.get_full_name() or stock.operator.get_username(),
                cartons_before=stock.quantity,
                pieces_after=stock.quantity * factor,
            )
            for stock in operators_by_product.get(product.pk, ())
        )
        blockers = []
        if product.pk in blocked_ids:
            blockers.append('open_loading_order')
        if carton is None and any(
            item.kind != ProductPackaging.CARTON and item.name.strip().casefold() == 'carton'
            for item in product.packagings.all()
        ):
            blockers.append('ambiguous_carton_packaging')
        plans.append(ConversionPlan(
            product_id=product.pk,
            reference=product.reference,
            name=product.name,
            factor=factor,
            factor_source=source,
            conditioning_status=(
                Product.ConditioningStatus.CONFIRMED
                if source == 'confirmed' else Product.ConditioningStatus.PROVISIONAL
            ),
            depot_cartons_before=product.quantity,
            depot_pieces_after=product.quantity * factor,
            minimum_cartons_before=product.minimum_stock,
            minimum_pieces_after=product.minimum_stock * factor,
            operator_locations=locations,
            blockers=tuple(blockers),
            already_converted=product.pk in converted_ids,
        ))
    return plans


def _write_operator_adjustment(*, stock, target, user, reference):
    before = stock.quantity
    target = int(target)
    delta = target - before
    if not delta:
        return None
    updated = OperatorStock.objects.filter(pk=stock.pk, quantity=before).update(quantity=target)
    if updated != 1:
        raise ValidationError(_('Le stock opérateur a été modifié simultanément.'))
    movement = OperatorStockMovement(
        operator_id=stock.operator_id,
        product_id=stock.product_id,
        loading_order=None,
        movement_type=OperatorStockMovement.ADJUSTMENT,
        quantity=abs(delta),
        applied_delta=delta,
        balance_before=before,
        balance_after=target,
        source_reference=str(reference)[:100],
        created_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    movement._ledger_write_allowed = True
    movement.save(force_insert=True)
    stock.quantity = target
    return movement


def _ensure_no_open_loading(product_id):
    if LoadingOrderLine.objects.filter(
        product_id=product_id,
        loading_order__status__in=ACTIVE_LOADING_STATUSES,
    ).exists():
        raise ValidationError(
            _('Fermez ou annulez les bons de chargement ouverts avant de convertir ce produit.')
        )


@transaction.atomic
def apply_historical_stock_conversion(
    *, product, user=None, default_factor=30, backup_reference='', simulation_checksum='',
):
    """Convert one legacy product exactly once; callers must opt in explicitly."""
    product_id = product.pk if isinstance(product, Product) else int(product)
    locked = Product.objects.select_for_update().get(pk=product_id)
    existing = HistoricalStockConversion.objects.select_for_update().filter(product=locked).first()
    if existing:
        return existing, False
    if locked.unit_mode != Product.UnitMode.LEGACY:
        raise ValidationError(_('Seuls les produits historiques non convertis sont éligibles.'))
    _ensure_no_open_loading(locked.pk)

    carton = ProductPackaging.objects.select_for_update().filter(
        product=locked, kind=ProductPackaging.CARTON,
    ).first()
    if carton is None and ProductPackaging.objects.select_for_update().filter(
        product=locked, name__iexact='Carton',
    ).exists():
        raise ValidationError(
            _('Un ancien conditionnement nommé Carton doit être validé manuellement avant la conversion.')
        )
    used_default = carton is None or carton.conversion_factor < 2
    factor = int(default_factor if used_default else carton.conversion_factor)
    if factor < 2:
        raise ValidationError(_('Le facteur de conversion doit être au moins égal à 2.'))
    if carton is None:
        carton = ProductPackaging.objects.create(
            product=locked,
            name=str(_('Carton')),
            kind=ProductPackaging.CARTON,
            conversion_factor=factor,
            default_sale_price=0,
            purchase_enabled=False,
            sale_enabled=False,
            is_active=False,
        )

    operator_stocks = list(
        OperatorStock.objects.select_for_update().filter(product=locked).select_related(
            'operator',
        ).order_by('operator_id')
    )
    depot_before = locked.quantity
    conversion = HistoricalStockConversion.objects.create(
        product=locked,
        original_depot_cartons=depot_before,
        original_minimum_stock_cartons=locked.minimum_stock,
        factor=factor,
        used_default_factor=used_default,
        converted_depot_quantity=depot_before * factor,
        converted_minimum_stock=locked.minimum_stock * factor,
        backup_reference=str(backup_reference or '')[:255],
        simulation_checksum=str(simulation_checksum or '')[:64],
        applied_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    HistoricalStockConversionLocation.objects.bulk_create([
        HistoricalStockConversionLocation(
            conversion=conversion,
            operator_id=stock.operator_id,
            original_cartons=stock.quantity,
            converted_quantity=stock.quantity * factor,
        )
        for stock in operator_stocks
    ])

    target_depot = depot_before * factor
    record_stock_movement(
        product=locked,
        movement_type=StockMovement.ADJUSTMENT,
        quantity=target_depot,
        reason=_('Conversion du stock historique de cartons vers pièces'),
        user=user,
        source_type=StockMovement.SOURCE_CONVERSION,
        source_reference=f'historical-conversion:{conversion.pk}',
    )
    for stock in operator_stocks:
        _write_operator_adjustment(
            stock=stock,
            target=stock.quantity * factor,
            user=user,
            reference=f'historical-conversion:{conversion.pk}',
        )

    piece_unit = Unit.objects.resolve('Pièce')
    Product.objects.filter(pk=locked.pk).update(
        unit=piece_unit,
        unit_mode=Product.UnitMode.CARTON_AND_PIECE,
        minimum_stock=locked.minimum_stock * factor,
        conditioning_status=(
            Product.ConditioningStatus.PROVISIONAL
            if used_default else Product.ConditioningStatus.CONFIRMED
        ),
    )
    return conversion, True


def has_post_conversion_movements(conversion):
    if conversion.physical_inventory_required:
        return True
    cutoff = conversion.movement_cutoff
    depot_changed = StockMovement.objects.filter(
        product_id=conversion.product_id, created_at__gt=cutoff,
    ).exclude(source_type__in=(
        StockMovement.SOURCE_CONVERSION,
        StockMovement.SOURCE_CONDITIONING,
    )).exists()
    operator_changed = OperatorStockMovement.objects.filter(
        product_id=conversion.product_id, created_at__gt=cutoff,
    ).exclude(
        Q(movement_type=OperatorStockMovement.ADJUSTMENT)
        & (
            Q(source_reference__startswith='historical-conversion:')
            | Q(source_reference__startswith='conditioning-correction:')
        )
    ).exists()
    return depot_changed or operator_changed


def correction_preview(*, conversion, new_factor):
    new_factor = int(new_factor)
    moved = has_post_conversion_movements(conversion)
    locations = list(conversion.operator_locations.select_related('operator').order_by('operator_id'))
    depot_target = None if moved else conversion.original_depot_cartons * new_factor
    return {
        'requires_physical_inventory': moved,
        'depot_target': depot_target,
        'depot_difference': None if moved else depot_target - conversion.product.quantity,
        'operator_targets': {} if moved else {
            location.operator_id: location.original_cartons * new_factor for location in locations
        },
    }


@transaction.atomic
def correct_conditioning(
    *, product, new_factor, reason, user, physical_depot_quantity=None,
    physical_operator_quantities=None,
):
    product_id = product.pk if isinstance(product, Product) else int(product)
    locked = Product.objects.select_for_update().get(pk=product_id)
    conversion = HistoricalStockConversion.objects.select_for_update().select_related(
        'product',
    ).get(product=locked)
    _ensure_no_open_loading(locked.pk)
    new_factor = int(new_factor)
    if new_factor < 2:
        raise ValidationError({'new_factor': _('Un carton doit contenir au moins 2 pièces.')})
    reason = str(reason or '').strip()
    if not reason:
        raise ValidationError({'reason': _('Le motif est obligatoire.')})

    carton = ProductPackaging.objects.select_for_update().get(
        product=locked, kind=ProductPackaging.CARTON,
    )
    operator_stocks = {
        stock.operator_id: stock
        for stock in OperatorStock.objects.select_for_update().filter(product=locked).order_by('operator_id')
    }
    original_locations = {
        location.operator_id: location
        for location in conversion.operator_locations.select_for_update().order_by('operator_id')
    }
    operator_ids = sorted(set(operator_stocks) | set(original_locations))
    moved = has_post_conversion_movements(conversion)

    if moved:
        if physical_depot_quantity is None:
            raise ValidationError({'physical_depot_quantity': _('Un inventaire physique du dépôt est obligatoire.')})
        provided = {int(key): int(value) for key, value in (physical_operator_quantities or {}).items()}
        missing = set(operator_ids) - set(provided)
        if missing:
            raise ValidationError({'operator_stocks': _('Un inventaire physique est obligatoire pour chaque stock opérateur.')})
        depot_target = int(physical_depot_quantity)
        operator_targets = {operator_id: provided[operator_id] for operator_id in operator_ids}
    else:
        depot_target = conversion.original_depot_cartons * new_factor
        operator_targets = {
            operator_id: (
                original_locations[operator_id].original_cartons * new_factor
                if operator_id in original_locations else 0
            )
            for operator_id in operator_ids
        }
    if depot_target < 0 or any(value < 0 for value in operator_targets.values()):
        raise ValidationError(_('Les stocks physiques ne peuvent pas être négatifs.'))

    depot_before = locked.quantity
    operator_before = {
        str(operator_id): operator_stocks.get(operator_id).quantity if operator_id in operator_stocks else 0
        for operator_id in operator_ids
    }
    correction = ConditioningCorrection.objects.create(
        product=locked,
        conversion=conversion,
        old_factor=conversion.factor,
        new_factor=new_factor,
        depot_stock_before=depot_before,
        depot_stock_after=depot_target,
        depot_difference=depot_target - depot_before,
        minimum_stock_before=locked.minimum_stock,
        minimum_stock_after=conversion.original_minimum_stock_cartons * new_factor,
        operator_stocks_before=operator_before,
        operator_stocks_after={str(key): value for key, value in operator_targets.items()},
        automatic_recalculation=not moved,
        reason=reason,
        created_by=user,
    )
    record_stock_movement(
        product=locked,
        movement_type=StockMovement.ADJUSTMENT,
        quantity=depot_target,
        reason=reason,
        user=user,
        source_type=StockMovement.SOURCE_CONDITIONING,
        source_reference=f'conditioning-correction:{correction.pk}',
    )
    for operator_id in operator_ids:
        stock = operator_stocks.get(operator_id)
        if stock is None:
            stock = OperatorStock.objects.create(
                operator_id=operator_id, product=locked, quantity=0,
            )
        _write_operator_adjustment(
            stock=stock,
            target=operator_targets[operator_id],
            user=user,
            reference=f'conditioning-correction:{correction.pk}',
        )

    ProductPackaging.objects.filter(pk=carton.pk).update(conversion_factor=new_factor)
    now = timezone.now()
    conversion.factor = new_factor
    conversion.last_adjusted_at = now
    conversion.physical_inventory_required = conversion.physical_inventory_required or moved
    conversion.save(update_fields=('factor', 'last_adjusted_at', 'physical_inventory_required'))
    Product.objects.filter(pk=locked.pk).update(
        conditioning_status=Product.ConditioningStatus.CORRECTED,
        minimum_stock=conversion.original_minimum_stock_cartons * new_factor,
    )
    return correction
