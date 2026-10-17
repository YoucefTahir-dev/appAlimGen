import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0016_alter_payment_options_payment_cancellation_reason_and_more'),
        ('inventory', '0020_historicalstockconversion_physical_inventory_required'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OpeningBalancePayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference', models.CharField(blank=True, editable=False, max_length=100, unique=True, verbose_name='Référence paiement')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='Montant')),
                ('payment_type', models.CharField(choices=[('cash', 'Espèces'), ('cheque', 'Chèque'), ('transfer', 'Virement')], max_length=20, verbose_name='Type paiement')),
                ('external_reference', models.CharField(blank=True, max_length=100, verbose_name='Référence externe')),
                ('notes', models.TextField(blank=True, verbose_name='Observation')),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name='Date')),
                ('cancelled_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Annulé le')),
                ('cancellation_reason', models.TextField(blank=True, editable=False, verbose_name='Motif d’annulation')),
                ('cancelled_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_opening_balance_payments', to=settings.AUTH_USER_MODEL)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='opening_balance_payments', to='inventory.client')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='recorded_opening_balance_payments', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-created_at', '-pk')},
        ),
        migrations.AddConstraint(
            model_name='openingbalancepayment',
            constraint=models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='commerce_opening_payment_amount_positive'),
        ),
        migrations.AddIndex(
            model_name='openingbalancepayment',
            index=models.Index(fields=['client', '-created_at'], name='commerce_open_client_date_idx'),
        ),
    ]
