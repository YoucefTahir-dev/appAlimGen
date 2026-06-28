from django.db import migrations


def seed_base_packagings(apps, schema_editor):
    Product = apps.get_model('inventory', 'Product')
    ProductPackaging = apps.get_model('inventory', 'ProductPackaging')

    for product in Product.objects.select_related('unit').order_by('pk'):
        packaging_name = product.unit.name if product.unit_id and product.unit else 'Unité'
        ProductPackaging.objects.get_or_create(
            product=product,
            name=packaging_name,
            defaults={
                'unit_quantity': 1,
                'default_sale_price': product.sale_price,
                'barcode': product.barcode or None,
                'is_active': True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_productpackaging'),
    ]

    operations = [
        migrations.RunPython(seed_base_packagings, migrations.RunPython.noop),
    ]
