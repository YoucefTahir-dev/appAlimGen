import re

from django.db import migrations


def seed_invoice_sequences(apps, schema_editor):
    Sale = apps.get_model('commerce', 'Sale')
    InvoiceSequence = apps.get_model('commerce', 'InvoiceSequence')
    pattern = re.compile(r'^FAC-(\d{4})-(\d{6})$')
    max_by_year = {}

    for invoice_number, created_at in Sale.objects.values_list('invoice_number', 'created_at'):
        match = pattern.match(invoice_number or '')
        if match:
            year = int(match.group(1))
            number = int(match.group(2))
        else:
            year = created_at.year
            number = 0

        max_by_year[year] = max(max_by_year.get(year, 0), number)

    for year, last_number in max_by_year.items():
        InvoiceSequence.objects.update_or_create(
            year=year,
            defaults={'last_number': last_number},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0003_invoicesequence'),
    ]

    operations = [
        migrations.RunPython(seed_invoice_sequences, migrations.RunPython.noop),
    ]
