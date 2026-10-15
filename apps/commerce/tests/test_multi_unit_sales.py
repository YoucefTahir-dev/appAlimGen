from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.models import Sale
from apps.commerce.services import create_sale, update_sale
from apps.inventory.models import (
    Category,
    Client,
    LoadingOrder,
    LoadingOrderLine,
    OperatorStock,
    Product,
    ProductPackaging,
    StockMovement,
    Unit,
)
from apps.inventory.services import record_stock_movement, validate_loading_order


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class MultiUnitSaleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='multi-unit-sale', password='StrongPass123!', role='manager',
        )
        cls.unit = Unit.objects.create(name='Pièce')
        cls.category = Category.objects.create(name='Ventes multi-unités')
        cls.customer = Client.objects.create(
            name='Client détail', customer_type=Client.CustomerType.RETAIL,
        )

    def setUp(self):
        self.product = Product.objects.create(
            name='Chocolat vente mixte', category=self.category, unit=self.unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('100.00'), sale_price=Decimal('220.00'),
            super_wholesale_price=Decimal('205.00'),
            wholesale_price=Decimal('210.00'), retail_price=Decimal('220.00'),
            quantity=0,
        )
        record_stock_movement(
            product=self.product, movement_type=StockMovement.ENTRY, quantity=100,
            reason='Stock ventes multi-unités', user=self.user,
        )
        self.carton = ProductPackaging.objects.create(
            product=self.product, name='Carton de 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, reference_purchase_price=Decimal('4000.00'),
            default_sale_price=Decimal('4800.00'),
            super_wholesale_price=Decimal('4500.00'),
            wholesale_price=Decimal('4650.00'), retail_price=Decimal('4800.00'),
            purchase_enabled=True, sale_enabled=True,
        )

    def mixed_lines(self, carton_quantity=2, piece_quantity=5):
        return [
            {'product': self.product, 'packaging': self.carton, 'quantity': carton_quantity},
            {'product': self.product, 'quantity': piece_quantity},
        ]

    def test_mixed_sale_uses_entered_prices_and_deducts_53_base_units(self):
        sale = create_sale(client=self.customer, lines=self.mixed_lines(), user=self.user)

        self.assertEqual(sale.total, Decimal('10700.00'))
        carton_line, piece_line = sale.lines.order_by('pk')
        self.assertEqual(
            (carton_line.packaging_quantity, carton_line.packaging_factor, carton_line.quantity),
            (2, 24, 48),
        )
        self.assertEqual(carton_line.unit_price, Decimal('4800.00'))
        self.assertEqual(piece_line.unit_price, Decimal('220.00'))
        self.assertEqual(piece_line.quantity, 5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 47)

    def test_stock_52_is_rejected_and_stock_53_is_accepted_atomically(self):
        low_stock = Product.objects.create(
            name='Stock 52', category=self.category, unit=self.unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('100.00'), sale_price=Decimal('220.00'), quantity=0,
        )
        record_stock_movement(
            product=low_stock, movement_type=StockMovement.ENTRY, quantity=52, user=self.user,
        )
        low_carton = ProductPackaging.objects.create(
            product=low_stock, name='Carton 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price=Decimal('4800.00'), retail_price=Decimal('4800.00'),
        )
        with self.assertRaises(ValidationError):
            create_sale(client=self.customer, user=self.user, lines=[
                {'product': low_stock, 'packaging': low_carton, 'quantity': 2},
                {'product': low_stock, 'quantity': 5},
            ])
        low_stock.refresh_from_db()
        self.assertEqual(low_stock.quantity, 52)
        self.assertFalse(Sale.objects.filter(lines__product=low_stock).exists())

        record_stock_movement(
            product=self.product, movement_type=StockMovement.EXIT, quantity=47,
            reason='Ramener le stock à 53', user=self.user,
        )
        accepted = create_sale(client=self.customer, lines=self.mixed_lines(), user=self.user)
        self.assertEqual(accepted.total, Decimal('10700.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)

    def test_customer_tiers_use_independent_carton_and_piece_prices(self):
        expectations = (
            (Client.CustomerType.SUPER_WHOLESALE, Decimal('4500.00'), Decimal('205.00')),
            (Client.CustomerType.WHOLESALE, Decimal('4650.00'), Decimal('210.00')),
            (Client.CustomerType.RETAIL, Decimal('4800.00'), Decimal('220.00')),
        )
        for index, (customer_type, carton_price, piece_price) in enumerate(expectations):
            customer = Client.objects.create(name=f'Client {index}', customer_type=customer_type)
            sale = create_sale(
                client=customer, user=self.user,
                lines=[
                    {'product': self.product, 'packaging': self.carton, 'quantity': 1},
                    {'product': self.product, 'quantity': 1},
                ],
            )
            self.assertEqual(
                list(sale.lines.order_by('pk').values_list('unit_price', flat=True)),
                [carton_price, piece_price],
            )

    def test_tax_discount_and_full_payment_use_mixed_line_total(self):
        sale = create_sale(
            client=self.customer, lines=self.mixed_lines(), user=self.user,
            tax_rate=Decimal('19.00'), discount=Decimal('100.00'), pay_full=True,
        )

        self.assertEqual(sale.total, Decimal('12633.00'))
        self.assertEqual(sale.payments.get().amount, Decimal('12633.00'))

    def test_invalid_second_line_rolls_back_the_complete_sale(self):
        blocked_packaging = ProductPackaging.objects.create(
            product=self.product, name='Lot vente désactivée',
            conversion_factor=2, default_sale_price=Decimal('440.00'),
            sale_enabled=False,
        )

        with self.assertRaises(ValidationError):
            create_sale(client=self.customer, user=self.user, lines=[
                {'product': self.product, 'packaging': self.carton, 'quantity': 1},
                {'product': self.product, 'packaging': blocked_packaging, 'quantity': 1},
            ])

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 100)
        self.assertFalse(Sale.objects.exists())

    def test_sale_modes_and_sale_enabled_are_enforced(self):
        self.carton.sale_enabled = False
        self.carton.save()
        with self.assertRaises(ValidationError):
            create_sale(
                client=self.customer, user=self.user,
                lines=[{'product': self.product, 'packaging': self.carton, 'quantity': 1}],
            )

        self.carton.sale_enabled = True
        self.carton.save()
        self.product.unit_mode = Product.UnitMode.CARTON_ONLY
        self.product.save(update_fields=['unit_mode'])
        with self.assertRaises(ValidationError):
            create_sale(
                client=self.customer, user=self.user,
                lines=[{'product': self.product, 'quantity': 1}],
            )

        self.product.unit_mode = Product.UnitMode.PIECE_ONLY
        self.product.save(update_fields=['unit_mode'])
        with self.assertRaises(ValidationError):
            create_sale(
                client=self.customer, user=self.user,
                lines=[{'product': self.product, 'packaging': self.carton, 'quantity': 1}],
            )

    def test_update_and_delete_apply_exact_canonical_deltas(self):
        sale = create_sale(
            client=self.customer, user=self.user,
            lines=[
                {'product': self.product, 'packaging': self.carton, 'quantity': 1},
                {'product': self.product, 'quantity': 2},
            ],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 74)

        update_sale(
            sale=sale, client=self.customer, user=self.user,
            lines=[
                {'product': self.product, 'packaging': self.carton, 'quantity': 1},
                {'product': self.product, 'quantity': 5},
            ],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 71)

        sale._stock_user = self.user
        sale.delete()
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 100)

    def test_line_snapshots_survive_catalog_changes(self):
        sale = create_sale(
            client=self.customer, user=self.user,
            lines=[{'product': self.product, 'packaging': self.carton, 'quantity': 1}],
        )
        line = sale.lines.get()
        original = (
            line.product_name_snapshot, line.packaging_name,
            line.packaging_factor, line.unit_name_snapshot, line.unit_price,
        )
        self.product.name = 'Nom futur'
        self.product.save(update_fields=['name'])
        self.carton.name = 'Carton futur'
        self.carton.conversion_factor = 30
        self.carton.retail_price = Decimal('6000.00')
        self.carton.default_sale_price = Decimal('6000.00')
        self.carton.save()
        line.refresh_from_db()
        self.assertEqual(
            (
                line.product_name_snapshot, line.packaging_name,
                line.packaging_factor, line.unit_name_snapshot, line.unit_price,
            ),
            original,
        )

    def test_operator_sale_deducts_converted_quantity_from_operator_stock(self):
        operator = get_user_model().objects.create_user(
            username='multi-unit-operator', password='StrongPass123!', role='seller',
        )
        loading = LoadingOrder.objects.create(
            number='CHG-MULTI-SALE', operator=operator, created_by=self.user,
        )
        LoadingOrderLine.objects.create(
            loading_order=loading, product=self.product, quantity=60,
        )
        validate_loading_order(loading, user=self.user)

        sale = create_sale(client=self.customer, lines=self.mixed_lines(), user=operator)

        self.assertEqual(sale.loading_order_id, loading.pk)
        self.assertEqual(
            OperatorStock.objects.get(operator=operator, product=self.product).quantity,
            7,
        )

    def test_api_is_additive_and_legacy_payload_remains_compatible(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.user)
        response = api_client.post(reverse('api-sale-list'), {
            'client': self.customer.pk,
            'items': [
                {'product_id': self.product.pk, 'packaging_id': self.carton.pk, 'quantity': 1},
                {'product_id': self.product.pk, 'quantity': 1},
            ],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        payload = response.data.get('data', response.data)
        carton_line, piece_line = payload['lines']
        self.assertEqual(carton_line['packaging_id'], self.carton.pk)
        self.assertEqual(carton_line['entered_quantity'], 1)
        self.assertEqual(carton_line['base_quantity'], 24)
        self.assertIsNone(piece_line['packaging_id'])
        self.assertEqual(piece_line['base_quantity'], 1)


class PostgreSQLMultiUnitSaleConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Verrouillage concurrent validé dans la CI PostgreSQL.')
        self.user = get_user_model().objects.create_user(
            username='sale-concurrent', password='StrongPass123!', role='manager',
        )
        self.customer = Client.objects.create(name='Client concurrent')
        self.product = Product.objects.create(
            name='Produit vente concurrente',
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('100.00'), sale_price=Decimal('220.00'), quantity=0,
        )
        record_stock_movement(
            product=self.product, movement_type=StockMovement.ENTRY, quantity=53, user=self.user,
        )
        self.carton = ProductPackaging.objects.create(
            product=self.product, name='Carton 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price=Decimal('4800.00'), retail_price=Decimal('4800.00'),
        )

    def _sell_all(self):
        close_old_connections()
        try:
            create_sale(
                client=Client.objects.get(pk=self.customer.pk),
                user=get_user_model().objects.get(pk=self.user.pk),
                lines=[
                    {
                        'product': Product.objects.get(pk=self.product.pk),
                        'packaging': ProductPackaging.objects.get(pk=self.carton.pk),
                        'quantity': 2,
                    },
                    {'product': Product.objects.get(pk=self.product.pk), 'quantity': 5},
                ],
            )
            return 'ok'
        except ValidationError:
            return 'rejected'
        finally:
            close_old_connections()

    def test_only_one_concurrent_mixed_sale_consumes_stock(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._sell_all(), range(2)))
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('rejected'), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertEqual(Sale.objects.count(), 1)
