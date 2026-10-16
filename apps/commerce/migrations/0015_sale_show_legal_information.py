from django.db import migrations, models
import django.utils.translation


class Migration(migrations.Migration):
    dependencies = [('commerce', '0014_purchaseline_packaging_purchaseline_packaging_factor_and_more')]
    operations = [
        migrations.AddField(
            model_name='sale',
            name='show_legal_information',
            field=models.BooleanField(
                default=True,
                help_text=django.utils.translation.gettext_lazy(
                    'Les factures officielles conservent toujours les mentions légales obligatoires.'
                ),
                verbose_name=django.utils.translation.gettext_lazy(
                    'Afficher les informations légales sur les tickets et bons commerciaux'
                ),
            ),
        ),
    ]
