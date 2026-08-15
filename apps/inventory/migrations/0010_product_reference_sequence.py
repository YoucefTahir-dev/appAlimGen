import re

from django.db import migrations, models


REFERENCE_PATTERN = re.compile(r'^PRD-(\d{4})-(\d+)$')


def seed_product_sequences(apps, schema_editor):
    Product = apps.get_model('inventory', 'Product')
    ProductReferenceSequence = apps.get_model(
        'inventory', 'ProductReferenceSequence'
    )
    maximum_by_year = {}
    for reference in Product.objects.values_list('reference', flat=True).iterator():
        match = REFERENCE_PATTERN.match(reference or '')
        if not match:
            continue
        year, number = int(match.group(1)), int(match.group(2))
        maximum_by_year[year] = max(maximum_by_year.get(year, 0), number)
    for year, last_number in maximum_by_year.items():
        ProductReferenceSequence.objects.update_or_create(
            year=year,
            defaults={'last_number': last_number},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0009_reference_and_partner_uniqueness'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductReferenceSequence',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('year', models.PositiveIntegerField(unique=True)),
                ('last_number', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Séquence référence produit',
                'verbose_name_plural': 'Séquences références produits',
            },
        ),
        migrations.RunPython(seed_product_sequences, migrations.RunPython.noop),
    ]
