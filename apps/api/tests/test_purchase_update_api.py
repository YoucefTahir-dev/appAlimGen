from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.commerce.models import Purchase
from apps.inventory.models import Category, Product, ProductPackaging, Supplier, Unit


class PurchaseUpdateApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='purchase-update-admin',
            email='purchase-update@example.test',
            password='StrongPass123!',
        )
        self.client.force_authenticate(self.user)
        category = Category.objects.create(name='Achats modifiables')
        unit = Unit.objects.create(name='Pièce achat modifiable')
        self.supplier = Supplier.objects.create(name='Fournisseur achat modifiable')
        self.product = Product.objects.create(
            name='Produit achat modifiable', category=category, unit=unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('10.00'), sale_price=Decimal('15.00'), quantity=0,
        )
        self.carton = ProductPackaging.objects.create(
            product=self.product, name='Carton', kind=ProductPackaging.CARTON,
            conversion_factor=12, reference_purchase_price=Decimal('100.00'),
            default_sale_price=Decimal('180.00'),
            purchase_enabled=True, sale_enabled=True,
        )

    def _create_purchase(self):
        response = self.client.post(
            reverse('api-purchase-list'),
            {
                'reference': 'ACH-MOD-001',
                'supplier': self.supplier.pk,
                'tax_rate': '0.00',
                'items': [{
                    'product': self.product.pk,
                    'packaging_id': self.carton.pk,
                    'quantity': 2,
                    'purchase_price': '100.00',
                }],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return Purchase.objects.get(pk=response.data['id'])

    def test_patch_purchase_replaces_lines_and_adjusts_stock_once(self):
        purchase = self._create_purchase()
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 24)

        response = self.client.patch(
            reverse('api-purchase-detail', args=[purchase.pk]),
            {
                'reference': 'ACH-MOD-001',
                'supplier': self.supplier.pk,
                'tax_rate': '10.00',
                'items': [{
                    'product': self.product.pk,
                    'packaging_id': self.carton.pk,
                    'quantity': 3,
                    'purchase_price': '90.00',
                }],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.product.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(self.product.quantity, 36)
        self.assertEqual(purchase.lines.get().quantity, 36)
        self.assertEqual(purchase.lines.get().packaging_quantity, 3)
        self.assertEqual(purchase.total, Decimal('297.00'))

    def test_invalid_patch_rolls_back_purchase_and_stock(self):
        purchase = self._create_purchase()
        response = self.client.patch(
            reverse('api-purchase-detail', args=[purchase.pk]),
            {'items': []},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(self.product.quantity, 24)
        self.assertEqual(purchase.lines.count(), 1)
