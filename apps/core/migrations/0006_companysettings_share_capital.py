from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0005_authratelimitbucket'),
    ]

    operations = [
        migrations.AddField(
            model_name='companysettings',
            name='share_capital',
            field=models.DecimalField(
                verbose_name='Capital social (DZD)', max_digits=14, decimal_places=2,
                null=True, blank=True, validators=[MinValueValidator(Decimal('0.00'))],
                help_text='Facultatif. Laisser vide si le capital social n’est pas renseigné.',
            ),
        ),
    ]
