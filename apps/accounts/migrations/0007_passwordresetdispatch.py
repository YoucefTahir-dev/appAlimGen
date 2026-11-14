import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0006_unique_normalized_user_email')]
    operations = [migrations.CreateModel(
        name='PasswordResetDispatch',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('encrypted_email', models.TextField(blank=True)),
            ('state', models.CharField(default='pending', max_length=16)),
            ('attempts', models.PositiveSmallIntegerField(default=0)),
            ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('available_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('expires_at', models.DateTimeField()),
            ('lease', models.UUIDField(editable=False, null=True)),
            ('lease_until', models.DateTimeField(null=True)),
        ],
        options={'default_permissions': (), 'indexes': [
            models.Index(fields=['state', 'available_at'], name='reset_dispatch_ready_idx'),
        ]},
    )]
