from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import Product, ProductPackaging

from .models import InvoiceSequence, Payment, Purchase, PurchaseLine, Sale, SaleLine, TicketSequence


MONEY_QUANTUM = Decimal('0.01')


def money(value):
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def generate_invoice_number():
    year = timezone.now().year
    sequence, _ = InvoiceSequence.objects.select_for_update().get_or_create(year=year)
    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])
    return f'FAC-{year}-{sequence.last_number:06d}'


def generate_ticket_number():
    year = timezone.now().year
    sequence, _ = TicketSequence.objects.select_for_update().get_or_create(year=year)
    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])
    return f'TCK-{year}-{sequence.last_number:06d}'


@transaction.atomic
def ensure_ticket_number(sale):
    locked_sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if not locked_sale.ticket_number:
        locked_sale.ticket_number = generate_ticket_number()
        locked_sale.save(update_fields=['ticket_number'])
    sale.ticket_number = locked_sale.ticket_number
    return sale.ticket_number


def _normalized_lines(lines, price_field):
    normalized = []
    for raw_line in lines:
        product = raw_line['product']
        product_id = product.pk if isinstance(product, Product) else int(product)
        quantity = int(raw_line['quantity'])
        price = money(raw_line[price_field])
        if quantity <= 0:
            raise ValidationError({'quantity': _('La quantité doit être strictement positive.')})
        if price < 0:
            raise ValidationError({price_field: _('Le prix ne peut pas être négatif.')})
        normalized.append({'product_id': product_id, 'quantity': quantity, price_field: price})
    if not normalized:
        raise ValidationError({'lines': _('Une vente ou un achat doit contenir au moins un produit.')})
    return normalized


@transaction.atomic
def create_sale(*, client, lines, discount=0, tax_rate=0, payment_type=Sale.CASH, user=None, pay_full=False):
    if not lines:
        raise ValidationError({'lines': _('Une vente ou un achat doit contenir au moins un produit.')})
    product_ids = sorted({line['product'].pk if isinstance(line['product'], Product) else int(line['product']) for line in lines})
    products = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')
    }
    if len(products) != len(product_ids):
        raise ValidationError({'lines': _('Un produit est introuvable.')})

    packaging_ids = sorted({
        line['packaging'].pk if isinstance(line.get('packaging'), ProductPackaging) else int(line['packaging'])
        for line in lines if line.get('packaging')
    })
    packagings = {
        packaging.pk: packaging
        for packaging in ProductPackaging.objects.select_for_update().filter(pk__in=packaging_ids).order_by('pk')
    }
    if len(packagings) != len(packaging_ids):
        raise ValidationError({'packaging': _('Conditionnement invalide ou inactif.')})

    requested = {}
    subtotal = Decimal('0')
    margin = Decimal('0')
    normalized_lines = []
    for raw_line in lines:
        product_id = raw_line['product'].pk if isinstance(raw_line['product'], Product) else int(raw_line['product'])
        product = products[product_id]
        packaging_value = raw_line.get('packaging')
        packaging_id = packaging_value.pk if isinstance(packaging_value, ProductPackaging) else packaging_value
        packaging = packagings.get(int(packaging_id)) if packaging_id else None
        if packaging and (packaging.product_id != product.pk or not packaging.is_active):
            raise ValidationError({'packaging': _('Conditionnement invalide ou inactif.')})
        packaging_quantity = int(raw_line['quantity'])
        if packaging_quantity <= 0:
            raise ValidationError({'quantity': _('La quantité doit être strictement positive.')})
        factor = packaging.conversion_factor if packaging else 1
        package_price = raw_line.get('unit_price')
        if package_price is None:
            package_price = packaging.default_sale_price if packaging else product.sale_price
        package_price = money(package_price)
        stock_quantity = packaging_quantity * factor
        requested[product.pk] = requested.get(product.pk, 0) + stock_quantity
        minimum_price = product.purchase_price * factor
        if package_price < minimum_price:
            raise ValidationError(
                {'unit_price': ValidationError(
                    _("Impossible de vendre un produit à un prix inférieur à son prix d'achat."),
                    code='sale_price_below_cost',
                )}
            )
        subtotal += packaging_quantity * package_price
        margin += packaging_quantity * (package_price - minimum_price)
        normalized_lines.append({
            'product_id': product.pk,
            'quantity': stock_quantity,
            'packaging': packaging,
            'packaging_name': packaging.name if packaging else (product.unit.name if product.unit_id else str(_('Unité'))),
            'packaging_factor': factor,
            'packaging_quantity': packaging_quantity,
            'unit_price': package_price,
        })

    for product_id, quantity in requested.items():
        if products[product_id].quantity < quantity:
            raise ValidationError(
                {'quantity': ValidationError(
                    _('Stock insuffisant : %(available)s unité(s) disponible(s).') % {
                        'available': products[product_id].quantity
                    },
                    code='insufficient_stock',
                )}
            )

    discount = money(discount)
    tax_rate = money(tax_rate)
    if discount < 0:
        raise ValidationError({'discount': _('La remise ne peut pas être négative.')})
    if discount > margin:
        raise ValidationError(
            {'discount': ValidationError(
                _("Impossible de vendre un produit à un prix inférieur à son prix d'achat."),
                code='sale_price_below_cost',
            )}
        )
    total = money(subtotal + subtotal * tax_rate / Decimal('100') - discount)
    sale = Sale.objects.create(
        invoice_number=generate_invoice_number(),
        ticket_number=generate_ticket_number(),
        client=client,
        total=total,
        discount=discount,
        tax_rate=tax_rate,
        payment_type=payment_type,
        payment_tracking_initialized=True,
        created_by=user,
    )
    for line in normalized_lines:
        sale_line = SaleLine(
            sale=sale,
            product_id=line['product_id'],
            quantity=line['quantity'],
            packaging=line['packaging'],
            packaging_name=line['packaging_name'],
            packaging_factor=line['packaging_factor'],
            packaging_quantity=line['packaging_quantity'],
            unit_price=line['unit_price'],
        )
        sale_line._stock_user = user
        sale_line.save()
    if pay_full and total > 0:
        Payment.objects.create(sale=sale, amount=total, payment_type=payment_type, created_by=user)
    return sale


@transaction.atomic
def create_purchase(*, reference, supplier, lines, tax_rate=0, user=None):
    lines = _normalized_lines(lines, 'purchase_price')
    subtotal = sum((line['quantity'] * line['purchase_price'] for line in lines), Decimal('0'))
    tax_rate = money(tax_rate)
    total = money(subtotal + subtotal * tax_rate / Decimal('100'))
    purchase = Purchase.objects.create(
        reference=reference,
        supplier=supplier,
        total=total,
        tax_rate=tax_rate,
        payment_tracking_initialized=True,
    )
    for line in lines:
        purchase_line = PurchaseLine(
            purchase=purchase,
            product_id=line['product_id'],
            quantity=line['quantity'],
            purchase_price=line['purchase_price'],
        )
        purchase_line._stock_user = user
        purchase_line.save()
    return purchase
