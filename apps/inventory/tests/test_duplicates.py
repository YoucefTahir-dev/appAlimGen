from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.inventory.forms import ClientForm, SupplierForm
from apps.inventory.models import Brand, Category, Client, Supplier, Unit


class ReferenceUniquenessTests(TestCase):
    def test_reference_manager_reuses_case_and_whitespace_variant(self):
        brand = Brand.objects.resolve('  Ma   Marque  ')

        same_brand = Brand.objects.resolve('ma marque')

        self.assertEqual(same_brand.pk, brand.pk)
        self.assertEqual(brand.name, 'Ma Marque')

    def test_database_rejects_case_insensitive_reference_duplicate(self):
        Category.objects.create(name='Boissons')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name='  BOISSONS  ')

        Unit.objects.create(name='Pièce')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Unit.objects.create(name='pièce')


class PartnerUniquenessTests(TestCase):
    def test_client_form_rejects_duplicate_email_or_nif(self):
        Client.objects.create(
            name='Client existant',
            email='contact@example.com',
            tax_number='NIF-001',
        )

        duplicate_email = ClientForm(
            data={
                'name': 'Autre client',
                'email': ' CONTACT@EXAMPLE.COM ',
                'tax_number': 'NIF-002',
                'balance': '0',
            }
        )
        duplicate_nif = ClientForm(
            data={
                'name': 'Autre client',
                'email': 'other@example.com',
                'tax_number': ' nif-001 ',
                'balance': '0',
            }
        )

        self.assertFalse(duplicate_email.is_valid())
        self.assertFalse(duplicate_nif.is_valid())

    def test_supplier_form_rejects_duplicate_email_rc_or_nif(self):
        Supplier.objects.create(
            name='Fournisseur existant',
            email='supplier@example.com',
            rc_number='RC-001',
            tax_number='NIF-S-001',
        )
        base = {
            'name': 'Autre fournisseur',
            'phone': '',
            'address': '',
            'wilaya': '',
            'notes': '',
        }

        for changed_field, value in (
            ('email', ' SUPPLIER@EXAMPLE.COM '),
            ('rc_number', ' rc-001 '),
            ('tax_number', ' nif-s-001 '),
        ):
            data = {
                **base,
                'email': 'unique@example.com',
                'rc_number': 'RC-UNIQUE',
                'tax_number': 'NIF-UNIQUE',
                changed_field: value,
            }
            with self.subTest(field=changed_field):
                self.assertFalse(SupplierForm(data=data).is_valid())

    def test_multiple_blank_optional_identifiers_remain_allowed(self):
        first = Client.objects.create(name='Client A')
        second = Client.objects.create(name='Client B')
        supplier_a = Supplier.objects.create(name='Fournisseur A')
        supplier_b = Supplier.objects.create(name='Fournisseur B')

        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(supplier_a.pk, supplier_b.pk)
