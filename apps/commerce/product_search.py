"""Bounded product lookup for commercial forms; never expose sale costs."""
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from apps.accounts.permissions import has_permission
from apps.inventory.models import LoadingOrder, OperatorStock, Product, ProductPackaging
from apps.inventory.pricing import get_sale_price


def commercial_products(user, context):
    permissions = {
        'sale': ('commerce.add_sale', 'commerce.change_sale'),
        'purchase': ('commerce.add_purchase', 'commerce.change_purchase'),
        'loading_order': ('inventory.add_loadingorder', 'inventory.change_loadingorder'),
    }
    if context not in permissions or not any(has_permission(user, permission) for permission in permissions[context]):
        raise PermissionDenied
    products = Product.objects.all()
    if context == 'sale':
        active_loading = LoadingOrder.objects.filter(
            operator=user, status__in=(LoadingOrder.VALIDATED, LoadingOrder.IN_PROGRESS),
        )
        operator_quantity = OperatorStock.objects.filter(
            operator=user, product_id=OuterRef('pk'),
        ).values('quantity')[:1]
        products = products.annotate(
            operator_loading_active=Exists(active_loading),
            operator_quantity=Coalesce(Subquery(operator_quantity, output_field=IntegerField()), Value(0)),
        ).filter(Q(operator_loading_active=False) | Q(operator_quantity__gt=0))
    elif context == 'loading_order':
        products = products.filter(quantity__gt=0)
    return products


def search_products(*, user, query, context, customer=None):
    products = commercial_products(user, context)
    if len(query) < 2:
        return []
    products = products.select_related('unit').only(
        'pk', 'name', 'reference', 'barcode', 'quantity', 'purchase_price',
        'sale_price', 'super_wholesale_price', 'wholesale_price', 'retail_price',
        'unit_mode', 'unit__name',
    ).filter(
        Q(name__icontains=query) | Q(reference__icontains=query)
        | Q(barcode__icontains=query) | Q(brand__name__icontains=query)
    ).annotate(search_rank=Case(
        When(barcode__iexact=query, then=Value(0)),
        When(reference__iexact=query, then=Value(1)),
        When(name__istartswith=query, then=Value(2)),
        default=Value(3), output_field=IntegerField(),
    )).order_by('search_rank', 'name', 'pk')
    if context in {'purchase', 'loading_order'}:
        packaging_queryset = ProductPackaging.objects.filter(is_active=True)
        if context == 'purchase':
            packaging_queryset = packaging_queryset.filter(purchase_enabled=True)
            to_attr = 'purchase_packagings'
        else:
            packaging_queryset = packaging_queryset.filter(
                Q(purchase_enabled=True) | Q(sale_enabled=True)
            )
            to_attr = 'loading_packagings'
        products = products.prefetch_related(Prefetch(
            'packagings',
            queryset=packaging_queryset.order_by('conversion_factor', 'name'),
            to_attr=to_attr,
        ))
    products = products[:20]
    results = []
    for product in products:
        item = {
            'id': product.pk,
            'name': product.name,
            'reference': product.reference,
            'stock': product.operator_quantity if getattr(product, 'operator_loading_active', False) else product.quantity,
        }
        if context == 'purchase':
            item['purchase_price'] = f'{product.purchase_price:.2f}'
            purchase_units = []
            if product.unit_mode != Product.UnitMode.CARTON_ONLY:
                purchase_units.append({
                    'id': None,
                    'name': product.unit.name if product.unit_id else str(_('Unité de base')),
                    'factor': 1,
                    'purchase_price': f'{product.purchase_price:.2f}',
                })
            if product.unit_mode in {Product.UnitMode.CARTON_ONLY, Product.UnitMode.CARTON_AND_PIECE}:
                for packaging in getattr(product, 'purchase_packagings', []):
                    if product.unit_mode == Product.UnitMode.CARTON_ONLY and packaging.kind != ProductPackaging.CARTON:
                        continue
                    suggested_price = packaging.reference_purchase_price
                    if suggested_price is None:
                        suggested_price = product.purchase_price * packaging.conversion_factor
                    purchase_units.append({
                        'id': packaging.pk,
                        'name': packaging.name,
                        'factor': packaging.conversion_factor,
                        'purchase_price': f'{suggested_price:.2f}',
                    })
            item['purchase_units'] = purchase_units
        elif context == 'sale':
            item['price'] = f'{get_sale_price(product, customer):.2f}'
        elif context == 'loading_order':
            loading_units = []
            if product.unit_mode != Product.UnitMode.CARTON_ONLY:
                loading_units.append({
                    'id': None,
                    'name': product.unit.name if product.unit_id else str(_('Unité de base')),
                    'factor': 1,
                })
            if product.unit_mode in {Product.UnitMode.CARTON_ONLY, Product.UnitMode.CARTON_AND_PIECE}:
                for packaging in getattr(product, 'loading_packagings', []):
                    if product.unit_mode == Product.UnitMode.CARTON_ONLY and packaging.kind != ProductPackaging.CARTON:
                        continue
                    loading_units.append({
                        'id': packaging.pk,
                        'name': packaging.name,
                        'factor': packaging.conversion_factor,
                    })
            item['loading_units'] = loading_units
        results.append(item)
    return results
