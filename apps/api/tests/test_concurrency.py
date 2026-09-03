from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.commerce.models import Sale
from apps.commerce.services import create_sale
from apps.inventory.models import Client, Product, StockMovement
from apps.inventory.services import record_stock_movement


@skipUnless(connection.vendor == 'postgresql', 'La concurrence avec verrous de lignes exige PostgreSQL.')
class ConcurrentSaleTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='concurrent-seller', password='unused')
        self.customer = Client.objects.create(name='Client concurrence')
        self.product = Product.objects.create(
            name='Produit concurrence', purchase_price=Decimal('10'), sale_price=Decimal('15'), quantity=0,
        )
        record_stock_movement(
            product=self.product, movement_type=StockMovement.ENTRY, quantity=1,
            reason='Stock concurrence', user=self.user,
            source_type=StockMovement.SOURCE_PRODUCT, source_reference=self.product.reference,
        )

    def _sell_one(self, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            sale = create_sale(
                client=Client.objects.get(pk=self.customer.pk),
                lines=[{'product': self.product.pk, 'quantity': 1, 'unit_price': Decimal('15')}],
                user=get_user_model().objects.get(pk=self.user.pk),
            )
            return ('created', sale.pk)
        except ValidationError:
            return ('rejected', None)
        finally:
            close_old_connections()

    def test_two_simultaneous_sales_cannot_consume_the_same_unit(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self._sell_one(barrier), range(2)))

        self.assertEqual(sorted(result[0] for result in results), ['created', 'rejected'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).count(), 1,
        )
