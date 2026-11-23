from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.permissions import has_permission
from apps.core.models import AuditLog
from apps.inventory.pricing import get_sale_price


PRICE_OVERRIDE_PERMISSION = 'commerce.override_sale_price'
MONEY_QUANTUM = Decimal('0.01')


def _money(value):
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def can_override_sale_price(user):
    # Calls without a user are reserved for trusted internal jobs and legacy
    # data utilities. Every Web/API entry point supplies its authenticated user.
    return user is None or has_permission(user, PRICE_OVERRIDE_PERMISSION)


def historical_line_signatures(lines):
    return Counter(
        (
            line.product_id,
            line.packaging_id,
            int(line.packaging_quantity),
            _money(line.unit_price),
        )
        for line in lines
    )


def validate_sale_price(
    *, product, client, packaging, quantity, requested_price, user,
    unchanged_historical_lines=None,
):
    tariff = _money(get_sale_price(product, client, packaging))
    actual = tariff if requested_price is None else _money(requested_price)
    signature = (product.pk, getattr(packaging, 'pk', None), int(quantity), actual)
    unchanged = False
    if unchanged_historical_lines and unchanged_historical_lines[signature] > 0:
        unchanged_historical_lines[signature] -= 1
        unchanged = True
    overridden = actual != tariff
    if overridden and not unchanged and not can_override_sale_price(user):
        raise ValidationError({
            'unit_price': ValidationError(
                _('Vous n\'avez pas la permission de modifier le tarif de vente.'),
                code='sale_price_override_forbidden',
            )
        })
    return actual, tariff, overridden and not unchanged


def record_sale_price_overrides(*, sale, overrides, user):
    if not overrides:
        return
    AuditLog.objects.bulk_create([
        AuditLog(
            user=user if getattr(user, 'is_authenticated', False) else None,
            action=(
                f'sale.price_override invoice={sale.invoice_number} '
                f'product={item["product_id"]} tariff={item["tariff"]:.2f} '
                f'actual={item["actual"]:.2f}'
            )[:255],
        )
        for item in overrides
    ])
