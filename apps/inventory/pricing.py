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

    ``packaging`` is reserved for a future packaging model. Production stock is
    currently managed in base units only, so no conversion is applied here.
    """
    customer_type = getattr(customer, 'customer_type', 'RETAIL') or 'RETAIL'
    field_name = PRICE_FIELD_BY_CUSTOMER_TYPE.get(customer_type, 'retail_price')
    price = getattr(product, field_name, None)
    selected_price = product.sale_price if price is None else price
    return Decimal(str(selected_price))


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
