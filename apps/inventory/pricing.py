from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


PRICE_FIELD_BY_CUSTOMER_TYPE = {
    'SUPER_WHOLESALE': 'super_wholesale_price',
    'WHOLESALE': 'wholesale_price',
    'RETAIL': 'retail_price',
}


def get_sale_price(product, customer=None, packaging=None):
    """Return the authoritative tariff for a product/customer pair.

    When a packaging is supplied, prices follow the base-unit tariff and are
    multiplied by its conversion factor. This keeps one authoritative pricing
    rule for Web, Android and future desktop clients.
    """
    customer_type = getattr(customer, 'customer_type', 'RETAIL') or 'RETAIL'
    field_name = PRICE_FIELD_BY_CUSTOMER_TYPE.get(customer_type, 'retail_price')
    price = getattr(product, field_name, None)
    selected_price = product.sale_price if price is None else price
    selected_price = Decimal(str(selected_price))
    if packaging is not None:
        selected_price *= int(packaging.conversion_factor)
    return selected_price


def get_sale_price_context(product, customer, packaging=None):
    """Build the shared, non-sensitive pricing payload used by Web and API."""
    active_packagings = sorted(
        (item for item in product.packagings.all() if item.is_active),
        key=lambda item: (item.name, item.pk),
    )
    return {
        'product_id': product.pk,
        'product_name': product.name,
        'reference': product.reference,
        'stock': product.quantity,
        'customer_type': customer.customer_type,
        'customer_type_label': customer.get_customer_type_display(),
        'packaging_id': packaging.pk if packaging else None,
        'price': f'{get_sale_price(product, customer, packaging):.2f}',
        'packagings': [
            {
                'id': item.pk,
                'name': item.name,
                'conversion_factor': item.conversion_factor,
                'price': f'{get_sale_price(product, customer, item):.2f}',
            }
            for item in active_packagings
        ],
    }


def validate_product_prices(product):
    prices = (
        ('super_wholesale_price', _('Le prix Super Gros ne peut pas être inférieur au prix d’achat.')),
        ('wholesale_price', _('Le prix Gros ne peut pas être inférieur au prix d’achat.')),
        ('retail_price', _('Le prix Détail ne peut pas être inférieur au prix d’achat.')),
    )
    errors = {}
    purchase_price = (
        Decimal(str(product.purchase_price))
        if product.purchase_price is not None
        else None
    )
    if purchase_price is not None:
        for field_name, message in prices:
            value = getattr(product, field_name, None)
            if value is not None and Decimal(str(value)) < purchase_price:
                errors[field_name] = message

    values = [
        Decimal(str(value)) if value is not None else None
        for value in (getattr(product, field_name, None) for field_name, _message in prices)
    ]
    if all(value is not None for value in values) and not values[0] <= values[1] <= values[2]:
        errors['retail_price'] = _(
            'Les tarifs doivent respecter l’ordre : Super Gros ≤ Gros ≤ Détail.'
        )
    if errors:
        raise ValidationError(errors)
