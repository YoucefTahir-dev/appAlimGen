from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0018_sale_company_share_capital_snapshot'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='sale',
            options={
                'permissions': [('override_sale_price', 'Peut déroger au tarif de vente')],
                'verbose_name': 'Vente',
                'verbose_name_plural': 'Ventes',
            },
        ),
    ]
