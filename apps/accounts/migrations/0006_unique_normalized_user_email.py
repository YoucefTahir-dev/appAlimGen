# Prepared by P0-02. Run audit_user_emails --fail-on-conflict before applying.

import apps.accounts.models
from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower, Trim


def assert_no_normalized_duplicates(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    conflicts = (
        User.objects.using(schema_editor.connection.alias)
        .exclude(email='')
        .annotate(normalized_email=Lower(Trim('email')))
        .values('normalized_email')
        .annotate(total=Count('pk'))
        .filter(total__gt=1)
        .count()
    )
    if conflicts:
        raise RuntimeError(
            'Migration 0006 bloquée : exécutez audit_user_emails et '
            'résolvez manuellement les conflits avant de réessayer.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_user_auth_token_version'),
    ]

    operations = [
        migrations.AlterModelManagers(
            name='user',
            managers=[
                ('objects', apps.accounts.models.UserManager()),
            ],
        ),
        migrations.RunPython(
            assert_no_normalized_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(
                Lower(Trim('email')),
                condition=~models.Q(email=''),
                name='uniq_user_email_ci_trim',
            ),
        ),
    ]
