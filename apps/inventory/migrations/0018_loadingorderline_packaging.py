from django.db import migrations, models
import django.db.models.deletion


def backfill_loading_line_snapshots(apps, schema_editor):
    LoadingOrderLine = apps.get_model('inventory', 'LoadingOrderLine')
    LoadingOrderLine.objects.filter(packaging_quantity__isnull=True).update(
        packaging_quantity=models.F('quantity'),
        packaging_factor=1,
        packaging_name='Unité de base',
    )


class Migration(migrations.Migration):
    dependencies = [('inventory', '0017_product_multi_unit_pricing')]

    operations = [
        migrations.RemoveConstraint(
            model_name='loadingorderline', name='uniq_product_per_loading',
        ),
        migrations.AddField(
            model_name='loadingorderline', name='packaging',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='loading_lines', to='inventory.productpackaging',
                verbose_name='Conditionnement',
            ),
        ),
        migrations.AddField(
            model_name='loadingorderline', name='packaging_factor',
            field=models.PositiveIntegerField(default=1, editable=False, verbose_name='Facteur historique'),
        ),
        migrations.AddField(
            model_name='loadingorderline', name='packaging_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='Conditionnement historique'),
        ),
        migrations.AddField(
            model_name='loadingorderline', name='packaging_quantity',
            field=models.PositiveIntegerField(
                blank=True, help_text='Quantité saisie dans le conditionnement sélectionné.',
                null=True, verbose_name='Quantité chargée saisie',
            ),
        ),
        migrations.RunPython(backfill_loading_line_snapshots, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='loadingorderline',
            constraint=models.CheckConstraint(
                condition=models.Q(('packaging_factor__gte', 1)),
                name='loading_line_packaging_factor_gte_1',
            ),
        ),
        migrations.AddConstraint(
            model_name='loadingorderline',
            constraint=models.CheckConstraint(
                condition=models.Q(('packaging_quantity__isnull', True), ('packaging_quantity__gte', 1), _connector='OR'),
                name='loading_line_packaging_quantity_positive',
            ),
        ),
    ]
