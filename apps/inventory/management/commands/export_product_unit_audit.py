import csv
import hashlib
import io
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum

from apps.commerce.models import PurchaseLine, SaleLine
from apps.inventory.models import (
    LoadingOrder,
    LoadingOrderLine,
    OperatorStock,
    Product,
    StockMovement,
)


FIELDS = (
    'product_id',
    'reference',
    'name',
    'current_unit',
    'unit_mode',
    'stock_quantity',
    'operator_stock_quantity',
    'open_loading_quantity',
    'minimum_stock',
    'purchase_price',
    'super_wholesale_price',
    'wholesale_price',
    'retail_price',
    'packagings',
    'purchase_line_count',
    'sale_line_count',
    'loading_line_count',
    'stock_movement_count',
    'classification',
    'confirmed_carton_factor',
    'validation_notes',
)


def _counts(queryset):
    return {
        row['product_id']: row['count']
        for row in queryset.values('product_id').annotate(count=Count('pk'))
    }


def build_rows():
    purchase_counts = _counts(PurchaseLine.objects.all())
    sale_counts = _counts(SaleLine.objects.all())
    loading_counts = _counts(LoadingOrderLine.objects.all())
    movement_counts = _counts(StockMovement.objects.all())
    operator_quantities = {
        row['product_id']: row['total'] or 0
        for row in OperatorStock.objects.values('product_id').annotate(total=Sum('quantity'))
    }
    open_loading_quantities = {
        row['product_id']: row['total'] or 0
        for row in LoadingOrderLine.objects.filter(
            loading_order__status__in=(LoadingOrder.DRAFT, LoadingOrder.IN_PROGRESS),
        ).values('product_id').annotate(total=Sum('quantity'))
    }

    rows = []
    products = Product.objects.select_related('unit').prefetch_related('packagings').order_by('pk')
    for product in products:
        packaging_snapshot = [
            {
                'id': item.pk,
                'name': item.name,
                'kind': item.kind,
                'factor': item.conversion_factor,
                'active': item.is_active,
                'purchase_enabled': item.purchase_enabled,
                'sale_enabled': item.sale_enabled,
                'reference_purchase_price': str(item.reference_purchase_price),
                'super_wholesale_price': str(item.super_wholesale_price),
                'wholesale_price': str(item.wholesale_price),
                'retail_price': str(item.retail_price),
            }
            for item in product.packagings.all()
        ]
        rows.append({
            'product_id': product.pk,
            'reference': product.reference,
            'name': product.name,
            'current_unit': product.unit.name if product.unit_id else '',
            'unit_mode': product.unit_mode,
            'stock_quantity': product.quantity,
            'operator_stock_quantity': operator_quantities.get(product.pk, 0),
            'open_loading_quantity': open_loading_quantities.get(product.pk, 0),
            'minimum_stock': product.minimum_stock,
            'purchase_price': str(product.purchase_price),
            'super_wholesale_price': str(product.super_wholesale_price),
            'wholesale_price': str(product.wholesale_price),
            'retail_price': str(product.retail_price),
            'packagings': json.dumps(packaging_snapshot, ensure_ascii=False, separators=(',', ':')),
            'purchase_line_count': purchase_counts.get(product.pk, 0),
            'sale_line_count': sale_counts.get(product.pk, 0),
            'loading_line_count': loading_counts.get(product.pk, 0),
            'stock_movement_count': movement_counts.get(product.pk, 0),
            'classification': '',
            'confirmed_carton_factor': '',
            'validation_notes': '',
        })
    return rows


class Command(BaseCommand):
    help = (
        'Exporte, sans modifier la base, les données nécessaires à la validation '
        'métier produit par produit avant toute reprise multi-unités.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Fichier de destination CSV ou JSON.')
        parser.add_argument('--format', choices=('csv', 'json'), default='csv')

    def handle(self, *args, **options):
        rows = build_rows()
        if options['format'] == 'json':
            content = json.dumps(rows, ensure_ascii=False, indent=2) + '\n'
        else:
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            content = stream.getvalue()

        checksum = hashlib.sha256(content.encode('utf-8')).hexdigest()
        output = options.get('output')
        if output:
            path = Path(output).expanduser().resolve()
            if not path.parent.exists():
                raise CommandError(f'Le dossier de destination n’existe pas : {path.parent}')
            path.write_text(content, encoding='utf-8', newline='')
            self.stdout.write(f'Export lecture seule : {path}')
        else:
            self.stdout.write(content, ending='')
        self.stderr.write(f'Produits exportés : {len(rows)} ; SHA-256 : {checksum}')
