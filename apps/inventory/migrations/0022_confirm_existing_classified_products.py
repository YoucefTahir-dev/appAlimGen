from django.db import migrations


def confirm_existing_classified_products(apps, schema_editor):
    Product = apps.get_model('inventory', 'Product')
    Product.objects.exclude(unit_mode='legacy').filter(
        conditioning_status='unclassified',
    ).update(conditioning_status='confirmed')


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0021_historicalstockconversion_backup_reference_and_more'),
    ]

    operations = [
        migrations.RunPython(confirm_existing_classified_products, migrations.RunPython.noop),
    ]
