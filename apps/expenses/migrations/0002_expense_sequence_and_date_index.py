import re

from django.db import migrations, models


NUMBER_PATTERN = re.compile(r'^CHG-(\d{4})-(\d+)$')


def seed_expense_sequences(apps, schema_editor):
    Expense = apps.get_model('expenses', 'Expense')
    ExpenseSequence = apps.get_model('expenses', 'ExpenseSequence')
    maximum_by_year = {}
    for number in Expense.objects.values_list('number', flat=True).iterator():
        match = NUMBER_PATTERN.match(number or '')
        if not match:
            continue
        year, sequence = int(match.group(1)), int(match.group(2))
        maximum_by_year[year] = max(maximum_by_year.get(year, 0), sequence)
    for year, last_number in maximum_by_year.items():
        ExpenseSequence.objects.update_or_create(
            year=year,
            defaults={'last_number': last_number},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExpenseSequence',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('year', models.PositiveIntegerField(unique=True)),
                ('last_number', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Séquence charge',
                'verbose_name_plural': 'Séquences charges',
            },
        ),
        migrations.RunPython(seed_expense_sequences, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(
                fields=['date', '-id'],
                name='expense_date_id_idx',
            ),
        ),
    ]
