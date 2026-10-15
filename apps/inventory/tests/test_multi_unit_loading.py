from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.api.authentication import MobileTokenSerializer
from apps.commerce.models import Sale
from apps.commerce.services import create_sale
from apps.inventory.models import (
    Client, LoadingOrder, LoadingOrderLine, OperatorStock,
    OperatorStockMovement, Product, ProductPackaging, StockMovement,
)
from apps.inventory.services import close_loading_order, validate_loading_order


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class MultiUnitLoadingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='multi-load-admin', password='StrongPass123!')
        cls.operator = User.objects.create_user(username='multi-load-operator', password='StrongPass123!')
        cls.customer = Client.objects.create(name='Client multi chargement')
        cls.product = Product.objects.create(
            name='Boîte multi chargement', purchase_price=10, sale_price=20,
            quantity=245, unit_mode=Product.UnitMode.CARTON_AND_PIECE,
        )
        cls.carton = ProductPackaging.objects.create(
            product=cls.product, name='Carton 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price=480,
            purchase_enabled=True, sale_enabled=True,
        )

    def setUp(self):
        self.order = LoadingOrder.objects.create(
            number=f'CHG-MULTI-{uuid4().hex[:8]}', operator=self.operator, created_by=self.admin,
        )

    def add_mixed_lines(self):
        carton_line = LoadingOrderLine.objects.create(
            loading_order=self.order, product=self.product, packaging=self.carton,
            packaging_quantity=10, quantity=10,
        )
        piece_line = LoadingOrderLine.objects.create(
            loading_order=self.order, product=self.product,
            packaging_quantity=5, quantity=5,
        )
        return carton_line, piece_line

    def test_mixed_carton_and_piece_lines_transfer_one_canonical_quantity(self):
        carton_line, piece_line = self.add_mixed_lines()
        self.assertEqual((carton_line.quantity, piece_line.quantity), (240, 5))
        validate_loading_order(self.order, user=self.admin)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 245)
        self.assertEqual(
            OperatorStockMovement.objects.filter(
                loading_order=self.order, movement_type=OperatorStockMovement.LOAD,
            ).count(),
            1,
        )
        self.assertEqual(
            StockMovement.objects.filter(
                source_type=StockMovement.SOURCE_LOADING, source_reference=self.order.number,
            ).count(),
            1,
        )

    def test_aggregated_stock_boundary_is_atomic(self):
        self.product.quantity = 244
        self.product.save(update_fields=['quantity'])
        self.add_mixed_lines()
        with self.assertRaises(ValidationError):
            validate_loading_order(self.order, user=self.admin)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual((self.order.status, self.product.quantity), (LoadingOrder.DRAFT, 244))
        self.assertFalse(OperatorStock.objects.filter(operator=self.operator).exists())

    def test_close_returns_exact_canonical_remainder_across_duplicate_lines(self):
        self.add_mixed_lines()
        validate_loading_order(self.order, user=self.admin)
        create_sale(
            client=self.customer,
            lines=[{'product': self.product, 'quantity': 51, 'unit_price': 20}],
            user=self.operator,
        )
        close_loading_order(self.order, user=self.admin)
        self.product.refresh_from_db()
        lines = list(self.order.lines.order_by('pk'))
        self.assertEqual(self.product.quantity, 194)
        self.assertEqual(sum(line.returned_quantity for line in lines), 194)
        self.assertEqual(sum(line.sold_quantity for line in lines), 51)
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 0)

    def test_snapshot_survives_packaging_changes(self):
        line = LoadingOrderLine.objects.create(
            loading_order=self.order, product=self.product, packaging=self.carton,
            packaging_quantity=2, quantity=2,
        )
        self.carton.name = 'Carton modifié'
        self.carton.conversion_factor = 30
        self.carton.save()
        line.refresh_from_db()
        line.packaging_quantity = 3
        line.save(update_fields=['packaging_quantity'])
        self.assertEqual((line.packaging_name, line.packaging_factor, line.quantity), ('Carton 24', 24, 72))

    def test_modes_and_foreign_or_inactive_packagings_are_rejected(self):
        other = Product.objects.create(
            name='Autre produit', purchase_price=1, sale_price=2, quantity=20,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
        )
        foreign = ProductPackaging.objects.create(
            product=other, name='Carton autre', kind=ProductPackaging.CARTON,
            conversion_factor=10, default_sale_price=20,
        )
        with self.assertRaises(ValidationError):
            LoadingOrderLine.objects.create(
                loading_order=self.order, product=self.product, packaging=foreign,
                packaging_quantity=1, quantity=1,
            )
        self.product.unit_mode = Product.UnitMode.CARTON_ONLY
        self.product.save(update_fields=['unit_mode'])
        with self.assertRaises(ValidationError):
            LoadingOrderLine.objects.create(
                loading_order=self.order, product=self.product,
                packaging_quantity=1, quantity=1,
            )
        self.carton.is_active = False
        self.carton.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            LoadingOrderLine.objects.create(
                loading_order=self.order, product=self.product, packaging=self.carton,
                packaging_quantity=1, quantity=1,
            )

    def test_api_is_backward_compatible_and_exposes_additive_fields(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(self.admin).access_token}')
        legacy = api.post(reverse('api-loading-order-list'), {
            'operator': self.operator.pk,
            'lines': [{'product': self.product.pk, 'quantity': 5}],
        }, format='json')
        self.assertEqual(legacy.status_code, 201, legacy.data)
        legacy_line = legacy.json()['data']['lines'][0]
        self.assertEqual((legacy_line['quantity'], legacy_line['entered_quantity'], legacy_line['base_quantity']), (5, 5, 5))
        LoadingOrder.objects.filter(pk=legacy.json()['data']['id']).delete()

        packaged = api.post(reverse('api-loading-order-list'), {
            'operator': self.operator.pk,
            'lines': [
                {'product': self.product.pk, 'packaging_id': self.carton.pk, 'quantity': 10},
                {'product': self.product.pk, 'quantity': 5},
            ],
        }, format='json')
        self.assertEqual(packaged.status_code, 201, packaged.data)
        lines = packaged.json()['data']['lines']
        self.assertEqual(
            [(line['entered_quantity'], line['base_quantity'], line['packaging_factor']) for line in lines],
            [(10, 240, 24), (5, 5, 1)],
        )

    def test_second_validation_and_second_close_do_not_duplicate_movements(self):
        self.add_mixed_lines()
        validate_loading_order(self.order, user=self.admin)
        with self.assertRaises(ValidationError):
            validate_loading_order(self.order, user=self.admin)
        close_loading_order(self.order, user=self.admin)
        with self.assertRaises(ValidationError):
            close_loading_order(self.order, user=self.admin)
        self.assertEqual(
            OperatorStockMovement.objects.filter(loading_order=self.order).count(),
            2,
        )
