from dataclasses import dataclass, field

from .models import Product, StockMovement


@dataclass
class StockLedgerAudit:
    checked_products: int = 0
    checked_movements: int = 0
    legacy_movements: int = 0
    unresolved_legacy_products: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def is_consistent(self):
        return not self.issues


def audit_stock_ledger():
    """Verify the append-only ledger without changing stock or historical data."""
    result = StockLedgerAudit()
    previous_balance = {}
    latest_balance = {}
    tracked_products = set()
    legacy_products = set()
    baseline_products = set()

    movements = StockMovement.objects.order_by('product_id', 'created_at', 'pk').values(
        'pk',
        'product_id',
        'movement_type',
        'quantity',
        'applied_delta',
        'balance_before',
        'balance_after',
        'source_type',
        'source_reference',
    )
    for movement in movements.iterator(chunk_size=1000):
        result.checked_movements += 1
        product_id = movement['product_id']
        snapshot = (
            movement['applied_delta'],
            movement['balance_before'],
            movement['balance_after'],
        )
        populated = tuple(value is not None for value in snapshot)

        if not any(populated):
            result.legacy_movements += 1
            legacy_products.add(product_id)
            previous_balance.pop(product_id, None)
            continue
        if not all(populated):
            result.issues.append(
                f"Mouvement #{movement['pk']}: instantané de stock incomplet."
            )
            previous_balance.pop(product_id, None)
            continue

        delta, balance_before, balance_after = snapshot
        tracked_products.add(product_id)
        if (
            movement['source_type'] == StockMovement.SOURCE_LEGACY
            and movement['source_reference'].startswith('baseline:')
        ):
            baseline_products.add(product_id)
        if balance_before + delta != balance_after:
            result.issues.append(
                f"Mouvement #{movement['pk']}: variation incompatible avec les soldes."
            )

        movement_type = movement['movement_type']
        quantity = movement['quantity']
        if movement_type == StockMovement.ENTRY and delta != quantity:
            result.issues.append(
                f"Mouvement #{movement['pk']}: entrée incompatible avec la variation appliquée."
            )
        elif movement_type == StockMovement.EXIT and delta != -quantity:
            result.issues.append(
                f"Mouvement #{movement['pk']}: sortie incompatible avec la variation appliquée."
            )
        elif movement_type == StockMovement.ADJUSTMENT and quantity != balance_after:
            result.issues.append(
                f"Mouvement #{movement['pk']}: ajustement incompatible avec le stock cible."
            )

        if (
            product_id in previous_balance
            and previous_balance[product_id] != balance_before
        ):
            result.issues.append(
                f"Mouvement #{movement['pk']}: rupture de chaîne de soldes."
            )
        previous_balance[product_id] = balance_after
        latest_balance[product_id] = balance_after

    products = Product.objects.order_by('pk').values('pk', 'reference', 'quantity')
    for product in products.iterator(chunk_size=1000):
        result.checked_products += 1
        product_id = product['pk']
        if product_id in latest_balance:
            if product['quantity'] != latest_balance[product_id]:
                result.issues.append(
                    f"Produit {product['reference']}: stock courant différent du dernier solde journalisé."
                )
        elif product['quantity'] != 0:
            result.issues.append(
                f"Produit {product['reference']}: stock non nul sans mouvement rapproché."
            )

    unresolved_legacy_products = legacy_products - baseline_products
    result.unresolved_legacy_products = len(unresolved_legacy_products)
    if unresolved_legacy_products:
        result.issues.append(
            f"{len(unresolved_legacy_products)} produit(s) possèdent des mouvements historiques non rapprochés."
        )
    return result
