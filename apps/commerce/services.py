from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import LoadingOrder, OperatorStock, Product, ProductPackaging
from apps.inventory.pricing import get_sale_price, sale_base_unit_allowed, sale_packaging_allowed
from apps.inventory.services import active_loading_for_operator
from apps.core.models import CompanySettings

from .models import InvoiceSequence, Payment, Purchase, PurchaseLine, Sale, SaleLine, TicketSequence


MONEY_QUANTUM = Decimal('0.01')
INVOICE_SNAPSHOT_VERSION = 1
DEFAULT_COMPANY_NAME_FR = 'El Amine lil Mawad El Ghidhaiya wa Ghayr El Ghidhaiya'
DEFAULT_COMPANY_NAME_AR = 'الأمين للمواد الغذائية و غير الغذائية'


def money(value):
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def validate_tax_rate(value):
    if not Decimal(value).is_finite() or not 0 <= value <= 100:
        raise ValidationError(_('Le taux de TVA doit être compris entre 0 et 100.'))
    return value


def _invoice_snapshot_values(
    *, client, subtotal, tax_amount, issued_at=None,
    integrity_status=Sale.SNAPSHOT_COMPLETE, snapshot_version=INVOICE_SNAPSHOT_VERSION,
):
    company = CompanySettings.objects.order_by('pk').first()
    return {
        'issued_at': issued_at or timezone.now(),
        'snapshot_version': snapshot_version,
        'snapshot_integrity_status': integrity_status,
        'currency_snapshot': 'DZD',
        'subtotal_ht_snapshot': money(subtotal),
        'tax_amount_snapshot': money(tax_amount),
        'company_name_snapshot': company.company_name if company else DEFAULT_COMPANY_NAME_FR,
        'company_name_ar_snapshot': DEFAULT_COMPANY_NAME_AR,
        'company_address_snapshot': company.address if company else '',
        'company_phone_snapshot': company.phone if company else '',
        'company_email_snapshot': company.email if company else '',
        'company_rc_number_snapshot': company.rc_number if company else '',
        'company_tax_number_snapshot': company.tax_number if company else '',
        'company_nis_snapshot': company.nis if company else '',
        'company_article_number_snapshot': company.article_number if company else '',
        'company_logo_snapshot': company.logo.name if company and company.logo else '',
        'client_name_snapshot': client.name,
        'client_phone_snapshot': client.phone,
        'client_address_snapshot': client.address,
        'client_tax_number_snapshot': client.tax_number,
        'client_nis_snapshot': client.nis,
        'client_article_number_snapshot': client.article_number,
        'client_trade_register_number_snapshot': client.trade_register_number,
        'client_customer_type_snapshot': client.customer_type,
    }


def refresh_invoice_snapshot(sale):
    """Refresh a deliberately edited invoice without touching stock or payments."""
    subtotal = sum((line.line_total() for line in sale.lines.all()), Decimal('0.00'))
    tax_amount = money(sale.total - money(subtotal) + sale.discount)
    values = _invoice_snapshot_values(
        client=sale.client,
        subtotal=subtotal,
        tax_amount=tax_amount,
        issued_at=sale.issued_at or sale.created_at,
        integrity_status=sale.snapshot_integrity_status,
        snapshot_version=sale.snapshot_version,
    )
    for field, value in values.items():
        setattr(sale, field, value)
    sale.save(update_fields=tuple(values))
    return sale


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
def create_sale(
    *, client, lines, discount=0, tax_rate=0, payment_type=Sale.CASH,
    user=None, pay_full=False, initial_payment_amount=None, show_legal_information=True,
):
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

    active_loading = active_loading_for_operator(user, lock=True) if getattr(user, 'is_authenticated', False) else None
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
        if packaging and not sale_packaging_allowed(product, packaging):
            raise ValidationError({'packaging': _('Conditionnement de vente invalide ou inactif.')})
        if not packaging and not sale_base_unit_allowed(product):
            raise ValidationError({'packaging': _('Ce produit doit être vendu en carton.')})
        packaging_quantity = int(raw_line['quantity'])
        if packaging_quantity <= 0:
            raise ValidationError({'quantity': _('La quantité doit être strictement positive.')})
        factor = packaging.conversion_factor if packaging else 1
        tariff_price = get_sale_price(product, client, packaging)
        package_price = raw_line.get('unit_price')
        if package_price is None:
            package_price = tariff_price
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

    operator_stocks = {}
    if active_loading:
        operator_stocks = {
            stock.product_id: stock
            for stock in OperatorStock.objects.select_for_update().filter(
                operator=user, product_id__in=requested,
            ).order_by('product_id')
        }
    for product_id, quantity in requested.items():
        available = operator_stocks.get(product_id).quantity if product_id in operator_stocks else (
            0 if active_loading else products[product_id].quantity
        )
        if available < quantity:
            raise ValidationError(
                {'quantity': ValidationError(
                    _('%(label)s insuffisant : %(available)s unité(s) disponible(s).') % {
                        'label': _('Stock opérateur') if active_loading else _('Stock'),
                        'available': available,
                    },
                    code='insufficient_stock',
                )}
            )

    discount = money(discount)
    tax_rate = validate_tax_rate(money(tax_rate))
    if discount < 0:
        raise ValidationError({'discount': _('La remise ne peut pas être négative.')})
    if discount > subtotal + subtotal * tax_rate / Decimal('100'):
        raise ValidationError({'discount': _('La remise ne peut pas dépasser le total de la vente.')})
    if discount > margin:
        raise ValidationError(
            {'discount': ValidationError(
                _("Impossible de vendre un produit à un prix inférieur à son prix d'achat."),
                code='sale_price_below_cost',
            )}
        )
    total = money(subtotal + subtotal * tax_rate / Decimal('100') - discount)
    tax_amount = money(total - money(subtotal) + discount)
    snapshot_values = _invoice_snapshot_values(
        client=client, subtotal=subtotal, tax_amount=tax_amount,
    )
    sale = Sale.objects.create(
        invoice_number=generate_invoice_number(),
        ticket_number=generate_ticket_number(),
        client=client,
        total=total,
        discount=discount,
        tax_rate=tax_rate,
        payment_type=payment_type,
        payment_tracking_initialized=True,
        show_legal_information=show_legal_information,
        created_by=user,
        loading_order=active_loading,
        **snapshot_values,
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
    payment_amount = total if pay_full else (
        money(initial_payment_amount) if initial_payment_amount is not None else Decimal('0.00')
    )
    if payment_amount < 0 or payment_amount > total:
        raise ValidationError({'amount_received': _('Le montant reçu doit être compris entre 0 et le total.')})
    if initial_payment_amount is not None and not pay_full and not Decimal('0.00') < payment_amount < total:
        raise ValidationError({'amount_received': _('Un paiement partiel doit être strictement positif et inférieur au total.')})
    if payment_amount > 0:
        Payment.objects.create(sale=sale, amount=payment_amount, payment_type=payment_type, created_by=user)
    return sale


@transaction.atomic
def update_sale(
    *, sale, client=None, lines=None, discount=None, tax_rate=None,
    payment_type=None, user=None, pay_full=False, initial_payment_amount=None,
    show_legal_information=None,
):
    """Update a sale while leaving every stock mutation to the domain models.

    API clients send the complete replacement line set.  Existing lines are
    restored before the replacements are saved; the surrounding transaction
    makes the operation all-or-nothing when stock or payment validation fails.
    """
    # Lock the sale without joining its nullable loading order. PostgreSQL
    # rejects FOR UPDATE on the nullable side of the LEFT OUTER JOIN generated
    # by select_related('loading_order'). Keep the loading-order lock explicit:
    # an operator sale must not race with closing that order.
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale.loading_order_id:
        sale.loading_order = LoadingOrder.objects.select_for_update().get(
            pk=sale.loading_order_id,
        )
    client = client or sale.client
    discount = money(sale.discount if discount is None else discount)
    tax_rate = validate_tax_rate(money(sale.tax_rate if tax_rate is None else tax_rate))
    payment_type = payment_type or sale.payment_type

    if lines is None:
        current_lines = list(sale.lines.select_related('product', 'packaging'))
        subtotal = sum((line.line_total() for line in current_lines), Decimal('0'))
        margin = sum(
            (
                line.packaging_quantity
                * (line.unit_price - line.unit_cost * line.packaging_factor)
                for line in current_lines
            ),
            Decimal('0'),
        )
        normalized_lines = None
    else:
        if not lines:
            raise ValidationError({'lines': _('Une vente doit contenir au moins un produit.')})
        product_ids = sorted({
            line['product'].pk if isinstance(line['product'], Product) else int(line['product'])
            for line in lines
        })
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

        subtotal = Decimal('0')
        margin = Decimal('0')
        normalized_lines = []
        requested = {}
        for raw_line in lines:
            product_id = raw_line['product'].pk if isinstance(raw_line['product'], Product) else int(raw_line['product'])
            product = products[product_id]
            packaging_value = raw_line.get('packaging')
            packaging_id = packaging_value.pk if isinstance(packaging_value, ProductPackaging) else packaging_value
            packaging = packagings.get(int(packaging_id)) if packaging_id else None
            if packaging and not sale_packaging_allowed(product, packaging):
                raise ValidationError({'packaging': _('Conditionnement de vente invalide ou inactif.')})
            if not packaging and not sale_base_unit_allowed(product):
                raise ValidationError({'packaging': _('Ce produit doit être vendu en carton.')})
            packaging_quantity = int(raw_line['quantity'])
            if packaging_quantity <= 0:
                raise ValidationError({'quantity': _('La quantité doit être strictement positive.')})
            factor = packaging.conversion_factor if packaging else 1
            package_price = raw_line.get('unit_price')
            if package_price is None:
                package_price = get_sale_price(product, client, packaging)
            package_price = money(package_price)
            minimum_price = product.purchase_price * factor
            if package_price < minimum_price:
                raise ValidationError({
                    'unit_price': ValidationError(
                        _("Impossible de vendre un produit à un prix inférieur à son prix d'achat."),
                        code='sale_price_below_cost',
                    )
                })
            subtotal += packaging_quantity * package_price
            margin += packaging_quantity * (package_price - minimum_price)
            normalized_lines.append({
                'product': product,
                'packaging': packaging,
                'packaging_quantity': packaging_quantity,
                'quantity': packaging_quantity * factor,
                'unit_price': package_price,
            })
            requested[product.pk] = requested.get(product.pk, 0) + packaging_quantity * factor

        old_quantities = {}
        for old_line in sale.lines.all():
            old_quantities[old_line.product_id] = (
                old_quantities.get(old_line.product_id, 0) + old_line.quantity
            )
        operator_stocks = {}
        if sale.loading_order_id:
            operator_stocks = {
                stock.product_id: stock
                for stock in OperatorStock.objects.select_for_update().filter(
                    operator_id=sale.loading_order.operator_id,
                    product_id__in=requested,
                ).order_by('product_id')
            }
        for product_id, quantity in requested.items():
            current = (
                operator_stocks.get(product_id).quantity
                if product_id in operator_stocks
                else (0 if sale.loading_order_id else products[product_id].quantity)
            )
            available = current + old_quantities.get(product_id, 0)
            if available < quantity:
                raise ValidationError({
                    'quantity': ValidationError(
                        _('%(label)s insuffisant : %(available)s unité(s) disponible(s).') % {
                            'label': _('Stock opérateur') if sale.loading_order_id else _('Stock'),
                            'available': available,
                        },
                        code='insufficient_stock',
                    )
                })

    if discount < 0:
        raise ValidationError({'discount': _('La remise ne peut pas être négative.')})
    if discount > subtotal + subtotal * tax_rate / Decimal('100'):
        raise ValidationError({'discount': _('La remise ne peut pas dépasser le total de la vente.')})
    if discount > margin:
        raise ValidationError({
            'discount': ValidationError(
                _("Impossible de vendre un produit à un prix inférieur à son prix d'achat."),
                code='sale_price_below_cost',
            )
        })
    total = money(subtotal + subtotal * tax_rate / Decimal('100') - discount)
    tax_amount = money(total - money(subtotal) + discount)

    sale.client = client
    sale.discount = discount
    sale.tax_rate = tax_rate
    sale.payment_type = payment_type
    if show_legal_information is not None:
        sale.show_legal_information = show_legal_information
    sale.total = total
    sale.payment_tracking_initialized = True
    for field, value in _invoice_snapshot_values(
        client=client,
        subtotal=subtotal,
        tax_amount=tax_amount,
        issued_at=sale.issued_at or sale.created_at,
        integrity_status=sale.snapshot_integrity_status,
        snapshot_version=sale.snapshot_version,
    ).items():
        setattr(sale, field, value)
    sale.save()

    if normalized_lines is not None:
        for old_line in list(sale.lines.select_for_update()):
            old_line._stock_user = user
            old_line.delete()
        for line in normalized_lines:
            replacement = SaleLine(sale=sale, **line)
            replacement._stock_user = user
            replacement.save()

    list(Payment.objects.select_for_update().filter(sale=sale).order_by('pk'))
    remaining = sale.balance_due
    payment_amount = remaining if pay_full else (
        money(initial_payment_amount) if initial_payment_amount is not None else Decimal('0.00')
    )
    if payment_amount < 0 or payment_amount > remaining:
        raise ValidationError({'amount_received': _('Le montant reçu dépasse le reste à payer.')})
    if initial_payment_amount is not None and not pay_full and not Decimal('0.00') < payment_amount < remaining:
        raise ValidationError({'amount_received': _('Un paiement partiel doit être strictement positif et inférieur au reste à payer.')})
    if payment_amount > 0:
        Payment.objects.create(
            sale=sale,
            amount=payment_amount,
            payment_type=payment_type,
            created_by=user,
        )
    return sale


@transaction.atomic
def create_purchase(*, reference, supplier, lines, tax_rate=0, user=None):
    if not lines:
        raise ValidationError({'lines': _('Une vente ou un achat doit contenir au moins un produit.')})
    product_ids = sorted({
        line['product'].pk if isinstance(line['product'], Product) else int(line['product'])
        for line in lines
    })
    products = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(
            pk__in=product_ids,
        ).order_by('pk')
    }
    if len(products) != len(product_ids):
        raise ValidationError({'lines': _('Un produit est introuvable.')})
    packaging_ids = sorted({
        line['packaging'].pk if isinstance(line.get('packaging'), ProductPackaging) else int(line['packaging'])
        for line in lines if line.get('packaging')
    })
    packagings = {
        packaging.pk: packaging
        for packaging in ProductPackaging.objects.select_for_update().filter(
            pk__in=packaging_ids,
        ).order_by('pk')
    }
    if len(packagings) != len(packaging_ids):
        raise ValidationError({'packaging': _('Conditionnement d’achat invalide ou inactif.')})

    normalized_lines = []
    subtotal = Decimal('0')
    for raw_line in lines:
        product_id = raw_line['product'].pk if isinstance(raw_line['product'], Product) else int(raw_line['product'])
        product = products[product_id]
        entered_quantity = int(raw_line['quantity'])
        if entered_quantity <= 0:
            raise ValidationError({'quantity': _('La quantité doit être strictement positive.')})
        purchase_price = money(raw_line['purchase_price'])
        if purchase_price < 0:
            raise ValidationError({'purchase_price': _('Le prix ne peut pas être négatif.')})
        packaging_value = raw_line.get('packaging')
        packaging_id = packaging_value.pk if isinstance(packaging_value, ProductPackaging) else packaging_value
        packaging = packagings.get(int(packaging_id)) if packaging_id else None
        if packaging and (
            packaging.product_id != product.pk
            or not packaging.is_active
            or not packaging.purchase_enabled
        ):
            raise ValidationError({'packaging': _('Conditionnement d’achat invalide ou inactif.')})
        if packaging and product.unit_mode in {Product.UnitMode.LEGACY, Product.UnitMode.PIECE_ONLY}:
            raise ValidationError({'packaging': _('Ce produit ne peut être acheté qu’en unité de base.')})
        if product.unit_mode == Product.UnitMode.CARTON_ONLY and (
            not packaging or packaging.kind != ProductPackaging.CARTON
        ):
            raise ValidationError({'packaging': _('Ce produit doit être acheté en carton.')})
        factor = packaging.conversion_factor if packaging else 1
        subtotal += entered_quantity * purchase_price
        normalized_lines.append({
            'product_id': product.pk,
            'quantity': entered_quantity * factor,
            'packaging': packaging,
            'packaging_quantity': entered_quantity,
            'purchase_price': purchase_price,
        })
    tax_rate = validate_tax_rate(money(tax_rate))
    total = money(subtotal + subtotal * tax_rate / Decimal('100'))
    purchase = Purchase.objects.create(
        reference=reference,
        supplier=supplier,
        total=total,
        tax_rate=tax_rate,
        payment_tracking_initialized=True,
    )
    for line in normalized_lines:
        purchase_line = PurchaseLine(
            purchase=purchase,
            product_id=line['product_id'],
            quantity=line['quantity'],
            packaging=line['packaging'],
            packaging_quantity=line['packaging_quantity'],
            purchase_price=line['purchase_price'],
        )
        purchase_line._stock_user = user
        purchase_line.save()
    return purchase
