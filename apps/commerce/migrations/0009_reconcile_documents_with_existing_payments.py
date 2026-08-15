from django.db import migrations


def mark_documents_with_payments_as_tracked(apps, schema_editor):
    """Existing valid payments are sufficient evidence that tracking was active."""
    Payment = apps.get_model('commerce', 'Payment')
    Purchase = apps.get_model('commerce', 'Purchase')
    Sale = apps.get_model('commerce', 'Sale')

    sale_ids = Payment.objects.filter(sale_id__isnull=False).values('sale_id')
    purchase_ids = Payment.objects.filter(purchase_id__isnull=False).values('purchase_id')
    Sale.objects.filter(pk__in=sale_ids).update(payment_tracking_initialized=True)
    Purchase.objects.filter(pk__in=purchase_ids).update(payment_tracking_initialized=True)


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0008_commerce_payment_indexes'),
    ]

    operations = [
        migrations.RunPython(
            mark_documents_with_payments_as_tracked,
            migrations.RunPython.noop,
        ),
    ]
