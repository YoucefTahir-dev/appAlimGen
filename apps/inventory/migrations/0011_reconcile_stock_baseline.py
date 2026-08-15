from django.db import migrations


BASELINE_REFERENCE = 'baseline:migration-0011'


def create_stock_baselines(apps, schema_editor):
    Product = apps.get_model('inventory', 'Product')
    StockMovement = apps.get_model('inventory', 'StockMovement')

    existing_product_ids = set(
        StockMovement.objects.filter(
            source_type='legacy',
            source_reference=BASELINE_REFERENCE,
        ).values_list('product_id', flat=True)
    )
    baselines = []
    for product in Product.objects.exclude(pk__in=existing_product_ids).iterator(chunk_size=500):
        baselines.append(
            StockMovement(
                product_id=product.pk,
                movement_type='adjustment',
                quantity=product.quantity,
                reason='Rapprochement initial du stock existant',
                applied_delta=0,
                balance_before=product.quantity,
                balance_after=product.quantity,
                source_type='legacy',
                source_reference=BASELINE_REFERENCE,
            )
        )
    StockMovement.objects.bulk_create(baselines, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0010_product_reference_sequence'),
    ]

    operations = [
        migrations.RunPython(create_stock_baselines, migrations.RunPython.noop),
    ]
