import csv
import io

from django.core.management import call_command
from django.test import TestCase

from apps.inventory.models import Product, ProductPackaging, Unit


class ProductUnitAuditCommandTests(TestCase):
    def setUp(self):
        unit = Unit.objects.create(name='Pièce')
        self.product = Product.objects.create(
            name='Produit audité',
            unit=unit,
            purchase_price='10.00',
            sale_price='12.00',
            super_wholesale_price='12.00',
            wholesale_price='13.00',
            retail_price='14.00',
            quantity=48,
            minimum_stock=2,
        )
        ProductPackaging.objects.create(
            product=self.product,
            name='Carton de 24',
            kind=ProductPackaging.CARTON,
            conversion_factor=24,
            default_sale_price='300.00',
            reference_purchase_price='240.00',
            super_wholesale_price='260.00',
            wholesale_price='280.00',
            retail_price='300.00',
        )

    def test_csv_export_is_read_only_and_contains_blank_decision_fields(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        call_command('export_product_unit_audit', stdout=stdout, stderr=stderr)

        rows = list(csv.DictReader(io.StringIO(stdout.getvalue())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['product_id'], str(self.product.pk))
        self.assertEqual(rows[0]['stock_quantity'], '48')
        self.assertIn('Carton de 24', rows[0]['packagings'])
        self.assertEqual(rows[0]['classification'], '')
        self.assertEqual(rows[0]['confirmed_carton_factor'], '')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 48)
        self.assertIn('SHA-256', stderr.getvalue())

    def test_json_export_is_available_without_writing_database(self):
        stdout = io.StringIO()

        call_command('export_product_unit_audit', format='json', stdout=stdout)

        self.assertIn('"name": "Produit audité"', stdout.getvalue())
        self.assertEqual(Product.objects.get(pk=self.product.pk).unit_mode, 'legacy')
