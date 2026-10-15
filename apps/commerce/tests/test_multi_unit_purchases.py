from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.commerce.models import Purchase, PurchaseLine
from apps.commerce.services import create_purchase
from apps.inventory.models import (
    Brand,
    Category,
    Product,
    ProductPackaging,
    StockMovement,
    Supplier,
    Unit,
)


class MultiUnitPurchaseMixin:
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='multi-unit-purchase',
            email='purchase@example.com',
            password='StrongPass123!',
        )
        cls.category = Category.objects.create(name='Achats multi-unités')
        cls.brand = Brand.objects.create(name='Marque achats')
        cls.unit = Unit.objects.create(name='Pièce achat')
        cls.supplier = Supplier.objects.create(name='Fournisseur multi-unités')
        cls.product = Product.objects.create(
            name='Chocolat test',
            category=cls.category,
            brand=cls.brand,
            unit=cls.unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('220.00'),
            sale_price=Decimal('300.00'),
            quantity=0,
            minimum_stock=0,
        )
        cls.carton = ProductPackaging.objects.create(
            product=cls.product,
            name='Carton 24',
            kind=ProductPackaging.CARTON,
            conversion_factor=24,
            reference_purchase_price=Decimal('4000.00'),
            default_sale_price=Decimal('7200.00'),
            super_wholesale_price=Decimal('7200.00'),
            wholesale_price=Decimal('7200.00'),
            retail_price=Decimal('7200.00'),
            purchase_enabled=True,
            sale_enabled=True,
        )


class MultiUnitPurchaseTests(MultiUnitPurchaseMixin, TestCase):
    def test_mixed_purchase_uses_entered_quantity_for_total_and_base_quantity_for_stock(self):
        purchase = create_purchase(
            reference='ACH-MIX-001',
            supplier=self.supplier,
            tax_rate=0,
            user=self.user,
            lines=[
                {
                    'product': self.product,
                    'packaging': self.carton,
                    'quantity': 100,
                    'purchase_price': Decimal('4000.00'),
                },
                {
                    'product': self.product,
                    'quantity': 30,
                    'purchase_price': Decimal('220.00'),
                },
            ],
        )

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 2430)
        self.assertEqual(purchase.total, Decimal('406600.00'))
        carton_line, piece_line = purchase.lines.order_by('pk')
        self.assertEqual(carton_line.packaging_quantity, 100)
        self.assertEqual(carton_line.packaging_factor, 24)
        self.assertEqual(carton_line.quantity, 2400)
        self.assertEqual(carton_line.line_total(), Decimal('400000.00'))
        self.assertEqual(piece_line.packaging_quantity, 30)
        self.assertEqual(piece_line.packaging_factor, 1)
        self.assertEqual(piece_line.quantity, 30)
        self.assertEqual(piece_line.line_total(), Decimal('6600.00'))
        self.assertEqual(
            list(StockMovement.objects.filter(
                source_type=StockMovement.SOURCE_PURCHASE,
            ).order_by('pk').values_list('applied_delta', flat=True)),
            [2400, 30],
        )

    def test_packaging_snapshot_survives_later_factor_change(self):
        purchase = create_purchase(
            reference='ACH-SNAPSHOT-001',
            supplier=self.supplier,
            user=self.user,
            lines=[{
                'product': self.product,
                'packaging': self.carton,
                'quantity': 2,
                'purchase_price': Decimal('4000.00'),
            }],
        )
        line = purchase.lines.get()
        self.carton.conversion_factor = 12
        self.carton.save()
        line.purchase_price = Decimal('4100.00')
        line.save(update_fields=['purchase_price'])
        line.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(line.packaging_factor, 24)
        self.assertEqual(line.quantity, 48)
        self.assertEqual(self.product.quantity, 48)

    def test_switching_existing_line_from_carton_to_piece_applies_exact_delta(self):
        purchase = create_purchase(
            reference='ACH-EDIT-001',
            supplier=self.supplier,
            user=self.user,
            lines=[{
                'product': self.product,
                'packaging': self.carton,
                'quantity': 2,
                'purchase_price': Decimal('4000.00'),
            }],
        )
        line = purchase.lines.get()
        line.packaging = None
        line.packaging_quantity = 5
        line.quantity = 5
        line.purchase_price = Decimal('230.00')
        line._stock_user = self.user
        line.save(update_fields=['packaging', 'packaging_quantity', 'purchase_price'])

        line.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(line.packaging_factor, 1)
        self.assertEqual(line.quantity, 5)
        self.assertEqual(self.product.quantity, 5)

        purchase._stock_user = self.user
        purchase.delete()
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)

    def test_carton_only_product_rejects_base_unit_purchase_atomically(self):
        self.product.unit_mode = Product.UnitMode.CARTON_ONLY
        self.product.save(update_fields=['unit_mode'])
        with self.assertRaises(ValidationError):
            create_purchase(
                reference='ACH-INVALID-001',
                supplier=self.supplier,
                user=self.user,
                lines=[{
                    'product': self.product,
                    'quantity': 1,
                    'purchase_price': Decimal('220.00'),
                }],
            )
        self.assertFalse(Purchase.objects.filter(reference='ACH-INVALID-001').exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)

    def test_web_form_creates_mixed_purchase_and_renders_dynamic_unit_controls(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('purchase_create'))
        self.assertContains(page, 'name="lines-0-packaging"')
        self.assertContains(page, 'purchase-stock-quantity')
        self.assertContains(page, 'purchase-total-ttc')

        response = self.client.post(reverse('purchase_create'), {
            'reference': 'ACH-WEB-MIX-001',
            'supplier': self.supplier.pk,
            'tax_rate': '0',
            'lines-TOTAL_FORMS': '2',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': self.product.pk,
            'lines-0-packaging': self.carton.pk,
            'lines-0-quantity': '100',
            'lines-0-purchase_price': '4000',
            'lines-1-product': self.product.pk,
            'lines-1-packaging': '',
            'lines-1-quantity': '30',
            'lines-1-purchase_price': '220',
        })
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 2430)

    def test_purchase_product_search_exposes_only_server_validated_units(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('commercial_product_search'), {
            'context': 'purchase',
            'q': 'Chocolat',
        })
        self.assertEqual(response.status_code, 200)
        result = response.json()['results'][0]
        self.assertEqual(result['id'], self.product.pk)
        self.assertEqual(result['purchase_units'], [
            {
                'id': None,
                'name': self.unit.name,
                'factor': 1,
                'purchase_price': '220.00',
            },
            {
                'id': self.carton.pk,
                'name': self.carton.name,
                'factor': 24,
                'purchase_price': '4000.00',
            },
        ])


class MultiUnitPurchaseApiTests(MultiUnitPurchaseMixin, APITestCase):
    def test_api_accepts_packaging_id_and_keeps_legacy_payload_compatible(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(reverse('api-purchase-list'), {
            'reference': 'ACH-API-MIX-001',
            'supplier': self.supplier.pk,
            'tax_rate': '0',
            'items': [
                {
                    'product': self.product.pk,
                    'packaging_id': self.carton.pk,
                    'quantity': 2,
                    'purchase_price': '4000.00',
                },
                {
                    'product': self.product.pk,
                    'quantity': 5,
                    'purchase_price': '220.00',
                },
            ],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 53)
        lines = response.data['lines']
        self.assertEqual(lines[0]['packaging_id'], self.carton.pk)
        self.assertEqual(lines[0]['packaging_factor'], 24)
        self.assertEqual(lines[0]['packaging_quantity'], 2)
        self.assertEqual(lines[0]['base_quantity'], 48)
        self.assertIsNone(lines[1]['packaging_id'])
        self.assertEqual(lines[1]['base_quantity'], 5)

    def test_api_rejects_packaging_from_another_product_without_stock_change(self):
        other = Product.objects.create(
            name='Autre produit achat',
            category=self.category,
            brand=self.brand,
            unit=self.unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('10.00'),
            sale_price=Decimal('20.00'),
            quantity=0,
        )
        foreign_carton = ProductPackaging.objects.create(
            product=other,
            name='Carton étranger',
            kind=ProductPackaging.CARTON,
            conversion_factor=6,
            reference_purchase_price=Decimal('60.00'),
            default_sale_price=Decimal('120.00'),
        )
        self.client.force_authenticate(self.user)
        response = self.client.post(reverse('api-purchase-list'), {
            'reference': 'ACH-API-INVALID-001',
            'supplier': self.supplier.pk,
            'items': [
                {
                    'product': self.product.pk,
                    'quantity': 5,
                    'purchase_price': '220.00',
                },
                {
                    'product': self.product.pk,
                    'packaging_id': foreign_carton.pk,
                    'quantity': 1,
                    'purchase_price': '60.00',
                },
            ],
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.product.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertEqual(other.quantity, 0)
        self.assertFalse(Purchase.objects.filter(reference='ACH-API-INVALID-001').exists())
