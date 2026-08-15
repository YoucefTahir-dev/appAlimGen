from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.inventory.models import Brand, Category, Client, Product, Supplier, Unit


def normalized(value):
    return ' '.join((value or '').split()).casefold()


def grouped_duplicates(queryset, field):
    groups = defaultdict(list)
    for instance in queryset.order_by('pk').only('pk', field):
        value = getattr(instance, field)
        key = normalized(value)
        if key:
            groups[key].append(instance)
    return {key: values for key, values in groups.items() if len(values) > 1}


class Command(BaseCommand):
    help = 'Audite les doublons et peut fusionner sans perte les catégories, marques et unités.'

    def add_arguments(self, parser):
        parser.add_argument('--fix-reference-data', action='store_true')

    def _report_groups(self, label, groups, field):
        self.stdout.write(f'{label}: {len(groups)} groupe(s) de doublons')
        for values in groups.values():
            details = ', '.join(f'#{item.pk} {getattr(item, field)!r}' for item in values)
            self.stdout.write(f'  - {details}')

    @transaction.atomic
    def _merge_reference_groups(self, model, product_field, groups):
        merged = 0
        for values in groups.values():
            keeper, *duplicates = values
            cleaned_name = ' '.join(keeper.name.split())
            if keeper.name != cleaned_name:
                model.objects.filter(pk=keeper.pk).update(name=cleaned_name)
            duplicate_ids = [item.pk for item in duplicates]
            Product.objects.filter(**{f'{product_field}_id__in': duplicate_ids}).update(
                **{f'{product_field}_id': keeper.pk}
            )
            model.objects.filter(pk__in=duplicate_ids).delete()
            merged += len(duplicate_ids)
        return merged

    def handle(self, *args, **options):
        reference_sets = (
            ('Catégories', Category, 'category'),
            ('Marques', Brand, 'brand'),
            ('Unités', Unit, 'unit'),
        )
        total_merged = 0
        for label, model, product_field in reference_sets:
            groups = grouped_duplicates(model.objects.all(), 'name')
            self._report_groups(label, groups, 'name')
            if options['fix_reference_data']:
                total_merged += self._merge_reference_groups(model, product_field, groups)

        client_checks = (
            ('Clients par NIF', 'tax_number'),
            ('Clients par téléphone', 'phone'),
            ('Clients par email', 'email'),
        )
        for label, field in client_checks:
            self._report_groups(label, grouped_duplicates(Client.objects.all(), field), field)

        supplier_checks = (
            ('Fournisseurs par RC', 'rc_number'),
            ('Fournisseurs par NIF', 'tax_number'),
            ('Fournisseurs par téléphone', 'phone'),
            ('Fournisseurs par email', 'email'),
        )
        for label, field in supplier_checks:
            self._report_groups(label, grouped_duplicates(Supplier.objects.all(), field), field)

        if options['fix_reference_data']:
            self.stdout.write(self.style.SUCCESS(f'Référentiels fusionnés : {total_merged} ligne(s).'))
        else:
            self.stdout.write(
                self.style.WARNING(
                    'Simulation uniquement. Les clients et fournisseurs ne sont jamais fusionnés automatiquement.'
                )
            )
