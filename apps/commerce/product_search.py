"""Bounded product lookup for commercial forms; never expose sale costs."""
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from apps.accounts.permissions import has_permission
from apps.inventory.models import LoadingOrder, OperatorStock, Product
from apps.inventory.pricing import get_sale_price


def commercial_products(user, context):
    if context not in ('sale', 'purchase') or not any(
        has_permission(user, f'commerce.{action}_{context}') for action in ('add', 'change')
    ):
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
    return [dict(
        id=product.pk, name=product.name, reference=product.reference,
        stock=(product.operator_quantity if getattr(product, 'operator_loading_active', False) else product.quantity),
        **({'purchase_price': f'{product.purchase_price:.2f}'} if context == 'purchase'
           else {'price': f'{get_sale_price(product, customer):.2f}'}),
    ) for product in products]
