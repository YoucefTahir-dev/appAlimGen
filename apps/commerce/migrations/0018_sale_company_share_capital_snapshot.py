from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0017_openingbalancepayment'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='company_share_capital_snapshot',
            field=models.DecimalField(
                max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
                validators=[MinValueValidator(Decimal('0.00'))],
            ),
        ),
    ]
