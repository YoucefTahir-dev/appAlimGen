from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0023_conditioningcorrection_minimum_stock_after_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='client',
            options={
                'permissions': [('adjust_client_balance', 'Peut corriger la dette antérieure client')],
                'verbose_name': 'Client', 'verbose_name_plural': 'Clients',
            },
        ),
        migrations.CreateModel(
            name='ClientBalanceAdjustment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('initial', 'Saisie initiale'), ('correction', 'Correction')], editable=False, max_length=16)),
                ('balance_before', models.DecimalField(decimal_places=2, editable=False, max_digits=12)),
                ('balance_after', models.DecimalField(decimal_places=2, editable=False, max_digits=12)),
                ('delta', models.DecimalField(decimal_places=2, editable=False, max_digits=13)),
                ('reason', models.CharField(editable=False, max_length=1000, verbose_name='Motif')),
                ('actor_id_snapshot', models.PositiveBigIntegerField(editable=False)),
                ('actor_name_snapshot', models.CharField(editable=False, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='balance_adjustments', to='inventory.client')),
                ('created_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='client_balance_adjustments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-created_at', '-pk'), 'default_permissions': ('view',),
                'verbose_name': 'Correction de dette antérieure', 'verbose_name_plural': 'Journal de dette antérieure',
                'indexes': [models.Index(fields=['client', '-created_at'], name='inv_client_debt_date_idx')],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('balance_before', models.F('balance_after')), _negated=True), name='inv_debt_adjustment_changed'),
                    models.CheckConstraint(condition=models.Q(('reason', ''), _negated=True), name='inv_debt_adjustment_reason'),
                ],
            },
        ),
    ]
