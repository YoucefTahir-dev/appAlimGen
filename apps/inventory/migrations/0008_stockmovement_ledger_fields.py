from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('inventory', '0007_alter_product_photo'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='stockmovement',
            options={
                'ordering': ['-created_at', '-pk'],
                'verbose_name': 'Mouvement stock',
                'verbose_name_plural': 'Mouvements stock',
            },
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='applied_delta',
            field=models.IntegerField(
                blank=True,
                editable=False,
                help_text='Valeur nulle uniquement pour les mouvements historiques non rapprochés.',
                null=True,
                verbose_name='Variation appliquée',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='balance_after',
            field=models.PositiveIntegerField(
                blank=True,
                editable=False,
                null=True,
                verbose_name='Stock après mouvement',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='balance_before',
            field=models.PositiveIntegerField(
                blank=True,
                editable=False,
                null=True,
                verbose_name='Stock avant mouvement',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='stock_movements',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='reversal_of',
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='reversal',
                to='inventory.stockmovement',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='source_reference',
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=100,
                verbose_name='Référence origine',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('legacy', 'Historique antérieur'),
                    ('manual', 'Saisie manuelle'),
                    ('product', 'Fiche produit'),
                    ('import', 'Import produits'),
                    ('purchase', 'Achat'),
                    ('sale', 'Vente'),
                    ('reversal', 'Annulation'),
                ],
                default='legacy',
                editable=False,
                max_length=16,
                verbose_name='Origine',
            ),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('legacy', 'Historique antérieur'),
                    ('manual', 'Saisie manuelle'),
                    ('product', 'Fiche produit'),
                    ('import', 'Import produits'),
                    ('purchase', 'Achat'),
                    ('sale', 'Vente'),
                    ('reversal', 'Annulation'),
                ],
                default='manual',
                editable=False,
                max_length=16,
                verbose_name='Origine',
            ),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='product',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='movements',
                to='inventory.product',
            ),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(
                fields=['product', '-created_at'],
                name='inv_mov_prod_created_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(
                fields=['source_type', 'source_reference'],
                name='inv_mov_source_ref_idx',
            ),
        ),
    ]
