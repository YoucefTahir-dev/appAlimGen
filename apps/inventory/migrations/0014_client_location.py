from django.db import migrations, models
from django.core.validators import MinValueValidator, MaxValueValidator
import apps.inventory.location


class Migration(migrations.Migration):
    dependencies = [('inventory', '0013_customer_type_pricing')]
    operations = [
        migrations.AddField(model_name='client', name='latitude', field=models.FloatField(blank=True, null=True, verbose_name='Latitude', validators=[apps.inventory.location.validate_finite, MinValueValidator(-90), MaxValueValidator(90)])),
        migrations.AddField(model_name='client', name='longitude', field=models.FloatField(blank=True, null=True, verbose_name='Longitude', validators=[apps.inventory.location.validate_finite, MinValueValidator(-180), MaxValueValidator(180)])),
        migrations.AddField(model_name='client', name='location_accuracy', field=models.FloatField(blank=True, null=True, verbose_name='Précision GPS (m)', validators=[apps.inventory.location.validate_finite, MinValueValidator(0)])),
        migrations.AddField(model_name='client', name='formatted_address', field=models.CharField(blank=True, null=True, max_length=1000, verbose_name='Adresse détectée')),
        migrations.AddField(model_name='client', name='place_id', field=models.CharField(blank=True, null=True, max_length=255, verbose_name='Identifiant Maps')),
    ]
