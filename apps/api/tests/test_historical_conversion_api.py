from django.urls import reverse
from django.contrib.auth.models import Permission
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.inventory.models import (
    Category,
    HistoricalStockConversion,
    Product,
    Unit,
)


class HistoricalConversionApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='conversion-api-admin',
            email='conversion-api@example.test',
            password='StrongPass123!',
        )
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(
            name='Stock historique mobile',
            category=Category.objects.create(name='Historique API'),
            unit=Unit.objects.create(name='Carton historique API'),
            unit_mode=Product.UnitMode.LEGACY,
            quantity=7,
            minimum_stock=2,
            purchase_price='10.00',
            sale_price='15.00',
        )

    def test_preview_then_apply_conversion(self):
        preview = self.client.get(
            reverse('api-product-historical-conversion-preview', args=[self.product.pk]),
            {'factor': 24},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)
        self.assertTrue(preview.data['eligible'])
        self.assertEqual(preview.data['depot_pieces_after'], 168)

        response = self.client.post(
            reverse('api-product-historical-conversion', args=[self.product.pk]),
            {
                'factor': 24,
                'backup_reference': 'backup-test-2026-10-17',
                'confirm': True,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 168)
        self.assertEqual(self.product.minimum_stock, 48)
        self.assertEqual(self.product.unit_mode, Product.UnitMode.CARTON_AND_PIECE)
        self.assertTrue(HistoricalStockConversion.objects.filter(product=self.product).exists())

    def test_conversion_requires_explicit_confirmation(self):
        response = self.client.post(
            reverse('api-product-historical-conversion', args=[self.product.pk]),
            {
                'factor': 24,
                'backup_reference': 'backup-test-2026-10-17',
                'confirm': False,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

    def test_correction_preview_and_apply(self):
        conversion_response = self.client.post(
            reverse('api-product-historical-conversion', args=[self.product.pk]),
            {
                'factor': 24,
                'backup_reference': 'backup-test-2026-10-17',
                'confirm': True,
            },
            format='json',
        )
        self.assertEqual(conversion_response.status_code, status.HTTP_201_CREATED)

        preview = self.client.get(
            reverse('api-product-conditioning-correction-preview', args=[self.product.pk]),
            {'new_factor': 30},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)
        self.assertFalse(preview.data['requires_physical_inventory'])
        self.assertEqual(preview.data['depot_target'], 210)

        corrected = self.client.post(
            reverse('api-product-conditioning-correction', args=[self.product.pk]),
            {'new_factor': 30, 'reason': 'Facteur réel confirmé', 'confirm': True},
            format='json',
        )
        self.assertEqual(corrected.status_code, status.HTTP_200_OK, corrected.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 210)
        self.assertEqual(self.product.minimum_stock, 60)
        self.assertEqual(self.product.carton_packaging.conversion_factor, 30)

    def test_conversion_requires_dedicated_permission(self):
        operator = User.objects.create_user(
            username='conversion-api-operator',
            email='conversion-operator@example.test',
            password='StrongPass123!',
        )
        operator.user_permissions.add(Permission.objects.get(codename='view_product'))
        self.client.force_authenticate(operator)

        preview = self.client.get(
            reverse('api-product-historical-conversion-preview', args=[self.product.pk]),
            {'factor': 24},
        )
        apply = self.client.post(
            reverse('api-product-historical-conversion', args=[self.product.pk]),
            {
                'factor': 24,
                'backup_reference': 'forbidden',
                'confirm': True,
            },
            format='json',
        )

        self.assertEqual(preview.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(apply.status_code, status.HTTP_403_FORBIDDEN)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
