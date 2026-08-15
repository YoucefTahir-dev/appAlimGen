from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_dynamic_roles_and_user_profile'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='denied_permissions',
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    'Ces refus priment sur les permissions du rôle et les '
                    'permissions directes.'
                ),
                related_name='denied_user_set',
                related_query_name='denied_user',
                to='auth.permission',
                verbose_name='Permissions individuelles refusées',
            ),
        ),
    ]
