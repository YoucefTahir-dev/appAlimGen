from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower, Trim


def normalized(value):
    return ' '.join(str(value or '').split())


def merge_reference_data(apps, model_name, product_field):
    model = apps.get_model('inventory', model_name)
    product = apps.get_model('inventory', 'Product')
    keepers = {}
    for item in model.objects.order_by('pk').iterator():
        cleaned = normalized(item.name)
        key = cleaned.casefold()
        keeper = keepers.get(key)
        if keeper is None:
            model.objects.filter(pk=item.pk).update(name=cleaned)
            keepers[key] = item
            continue
        product.objects.filter(**{f'{product_field}_id': item.pk}).update(
            **{f'{product_field}_id': keeper.pk}
        )
        item.delete()


def clean_and_validate_partners(apps):
    checks = (
        ('Client', ('email', 'tax_number')),
        ('Supplier', ('email', 'rc_number', 'tax_number')),
    )
    conflicts = []
    for model_name, fields in checks:
        model = apps.get_model('inventory', model_name)
        for item in model.objects.order_by('pk').iterator():
            updates = {
                'name': normalized(item.name),
                'phone': normalized(item.phone),
                'email': normalized(item.email).lower(),
                'tax_number': normalized(item.tax_number),
            }
            if model_name == 'Supplier':
                updates['rc_number'] = normalized(item.rc_number)
            model.objects.filter(pk=item.pk).update(**updates)

        for field in fields:
            seen = {}
            for item in model.objects.order_by('pk').only('pk', field).iterator():
                value = normalized(getattr(item, field))
                if not value:
                    continue
                key = value.casefold()
                if key in seen:
                    conflicts.append(
                        f'{model_name}.{field}: #{seen[key]} et #{item.pk} ({value!r})'
                    )
                else:
                    seen[key] = item.pk

    if conflicts:
        details = '; '.join(conflicts[:20])
        raise RuntimeError(
            'Doublons clients/fournisseurs ambigus détectés. '
            'Exécutez `python manage.py audit_duplicates`, corrigez-les manuellement, '
            f'puis relancez la migration. Détails : {details}'
        )


def prepare_unique_data(apps, schema_editor):
    merge_reference_data(apps, 'Category', 'category')
    merge_reference_data(apps, 'Brand', 'brand')
    merge_reference_data(apps, 'Unit', 'unit')
    clean_and_validate_partners(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0008_stockmovement_ledger_fields'),
    ]

    operations = [
        migrations.RunPython(prepare_unique_data, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_category_name_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='brand',
            constraint=models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_brand_name_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='unit',
            constraint=models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_unit_name_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='client',
            constraint=models.UniqueConstraint(
                Lower(Trim('email')),
                condition=~Q(email=''),
                name='uniq_client_email_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='client',
            constraint=models.UniqueConstraint(
                Lower(Trim('tax_number')),
                condition=~Q(tax_number=''),
                name='uniq_client_nif_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='supplier',
            constraint=models.UniqueConstraint(
                Lower(Trim('email')),
                condition=~Q(email=''),
                name='uniq_supplier_email_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='supplier',
            constraint=models.UniqueConstraint(
                Lower(Trim('rc_number')),
                condition=~Q(rc_number=''),
                name='uniq_supplier_rc_ci_trim',
            ),
        ),
        migrations.AddConstraint(
            model_name='supplier',
            constraint=models.UniqueConstraint(
                Lower(Trim('tax_number')),
                condition=~Q(tax_number=''),
                name='uniq_supplier_nif_ci_trim',
            ),
        ),
    ]
