"""Bounded product lookup for commercial forms; never expose sale costs."""
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from apps.accounts.permissions import has_permission
from apps.inventory.models import LoadingOrder, OperatorStock, Product
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
    products = products.only(
        'pk', 'name', 'reference', 'barcode', 'quantity', 'purchase_price',
        'sale_price', 'super_wholesale_price', 'wholesale_price', 'retail_price',
    ).filter(
        Q(name__icontains=query) | Q(reference__icontains=query)
        | Q(barcode__icontains=query) | Q(brand__name__icontains=query)
    ).annotate(search_rank=Case(
        When(barcode__iexact=query, then=Value(0)),
        When(reference__iexact=query, then=Value(1)),
        When(name__istartswith=query, then=Value(2)),
        default=Value(3), output_field=IntegerField(),
    )).order_by('search_rank', 'name', 'pk')[:20]
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
        elif context == 'sale':
            item['price'] = f'{get_sale_price(product, customer):.2f}'
        results.append(item)
    return results
