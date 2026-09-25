from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User
from apps.api.authentication import MobileTokenSerializer
from apps.commerce.models import Payment, Sale
from apps.commerce.services import create_sale
from apps.inventory.models import (
    Client, LoadingOrder, LoadingOrderLine, OperatorStock, OperatorStockMovement,
    Product, StockMovement,
)
from apps.inventory.services import close_loading_order, record_stock_movement, validate_loading_order


class LoadingOrderFlowTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='loading-admin', password='StrongPass123!')
        cls.operator = User.objects.create_user(username='loading-operator', password='StrongPass123!')
        cls.client_record = Client.objects.create(name='Client tournée')
        cls.product = Product.objects.create(name='Produit tournée', purchase_price=10, sale_price=20, quantity=0)
        record_stock_movement(product=cls.product, movement_type=StockMovement.ENTRY, quantity=20, user=cls.admin)

    def setUp(self):
        self.order = LoadingOrder.objects.create(number=f'CHG-TEST-{uuid4().hex[:8]}', operator=self.operator, created_by=self.admin)
        LoadingOrderLine.objects.create(loading_order=self.order, product=self.product, quantity=10)

    def authenticate(self, user=None):
        user = user or self.admin
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(user).access_token}'
        )

    def test_api_validate_action_uses_post_and_transfers_stock(self):
        self.authenticate()
        response = self.client.post(
            reverse('api-loading-order-validate', args=[self.order.pk]),
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()['data']['status'], LoadingOrder.IN_PROGRESS)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, LoadingOrder.IN_PROGRESS)
        self.assertEqual(self.product.quantity, 10)
        self.assertEqual(
            OperatorStock.objects.get(operator=self.operator, product=self.product).quantity,
            10,
        )

    def test_api_cancel_action_uses_post_without_changing_stock(self):
        self.authenticate()
        response = self.client.post(
            reverse('api-loading-order-cancel', args=[self.order.pk]),
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()['data']['status'], LoadingOrder.CANCELLED)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, LoadingOrder.CANCELLED)
        self.assertEqual(self.product.quantity, 20)
        self.assertFalse(OperatorStock.objects.filter(operator=self.operator).exists())

    def test_api_close_action_returns_unsold_stock(self):
        self.authenticate()
        validated = self.client.post(
            reverse('api-loading-order-validate', args=[self.order.pk]),
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(validated.status_code, 200, validated.data)

        response = self.client.post(
            reverse('api-loading-order-close', args=[self.order.pk]),
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()['data']['status'], LoadingOrder.CLOSED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 20)
        self.assertEqual(
            OperatorStock.objects.get(operator=self.operator, product=self.product).quantity,
            0,
        )

    def test_api_actions_reject_invalid_transition_and_missing_permission(self):
        self.authenticate()
        cancelled = self.client.post(
            reverse('api-loading-order-cancel', args=[self.order.pk]),
            {},
            format='json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        invalid = self.client.post(
            reverse('api-loading-order-validate', args=[self.order.pk]),
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)

        other_order = LoadingOrder.objects.create(
            number=f'CHG-NO-PERM-{uuid4().hex[:8]}',
            operator=self.operator,
            created_by=self.admin,
        )
        LoadingOrderLine.objects.create(
            loading_order=other_order,
            product=self.product,
            quantity=1,
        )
        user_without_permission = User.objects.create_user(
            username=f'loading-no-perm-{uuid4().hex[:8]}',
            password='StrongPass123!',
        )
        self.authenticate(user_without_permission)
        forbidden = self.client.post(
            reverse('api-loading-order-validate', args=[other_order.pk]),
            {},
            format='json',
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.data)

        self.client.credentials()
        unauthenticated = self.client.post(
            reverse('api-loading-order-cancel', args=[other_order.pk]),
            {},
            format='json',
        )
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.data)

    def test_validation_sale_and_close_isolate_depot_and_operator_stock(self):
        validate_loading_order(self.order, user=self.admin)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 10)

        sale = create_sale(
            client=self.client_record,
            lines=[{'product': self.product, 'quantity': 3, 'unit_price': 20}],
            user=self.operator,
        )
        self.assertEqual(sale.loading_order_id, self.order.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10, 'Une vente opérateur ne doit pas redécrémenter le dépôt.')
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 7)

        close_loading_order(self.order, user=self.admin)
        self.product.refresh_from_db()
        self.order.refresh_from_db()
        line = self.order.lines.get()
        self.assertEqual(self.product.quantity, 17)
        self.assertEqual(line.returned_quantity, 7)
        self.assertEqual(line.sold_quantity, 3)
        self.assertEqual(self.order.status, LoadingOrder.CLOSED)
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 0)
        self.assertEqual(OperatorStockMovement.objects.filter(loading_order=self.order).count(), 3)

    def test_operator_cannot_sell_more_than_loaded_stock(self):
        validate_loading_order(self.order, user=self.admin)
        with self.assertRaisesMessage(Exception, 'Stock opérateur insuffisant'):
            create_sale(
                client=self.client_record,
                lines=[{'product': self.product, 'quantity': 11, 'unit_price': 20}],
                user=self.operator,
            )
        self.assertFalse(Sale.objects.exists())

    def test_operator_sale_update_uses_only_loaded_stock_and_rolls_back_on_overflow(self):
        validate_loading_order(self.order, user=self.admin)
        self.operator.user_permissions.add(Permission.objects.get(
            codename='change_sale', content_type__app_label='commerce',
        ))
        sale = create_sale(
            client=self.client_record,
            lines=[{'product': self.product, 'quantity': 3, 'unit_price': 20}],
            user=self.operator,
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(self.operator).access_token}'
        )

        accepted = self.client.patch(
            reverse('api-sale-detail', args=[sale.pk]),
            {'items': [{'product': self.product.pk, 'quantity': 8, 'unit_price': '20.00'}]},
            format='json', HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(
            OperatorStock.objects.get(operator=self.operator, product=self.product).quantity,
            2,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10, 'La modification ne doit pas toucher le depot.')

        rejected = self.client.patch(
            reverse('api-sale-detail', args=[sale.pk]),
            {'items': [{'product': self.product.pk, 'quantity': 11, 'unit_price': '20.00'}]},
            format='json', HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertEqual(rejected.json()['error']['code'], 'INSUFFICIENT_STOCK')
        sale.refresh_from_db()
        self.assertEqual(sale.lines.get().quantity, 8)
        self.assertEqual(
            OperatorStock.objects.get(operator=self.operator, product=self.product).quantity,
            2,
        )

    def test_api_current_only_exposes_authenticated_operator(self):
        validate_loading_order(self.order, user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(self.operator).access_token}')
        response = self.client.get(reverse('api-loading-order-current'))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()['data']['id'], self.order.pk)
        hidden = Product.objects.create(name='Produit non chargé', purchase_price=5, sale_price=10, quantity=0)
        record_stock_movement(product=hidden, movement_type=StockMovement.ENTRY, quantity=50, user=self.admin)
        products = self.client.get(reverse('api-product-list'))
        self.assertEqual(products.status_code, 200, products.data)
        rows = products.json()['data']['results']
        self.assertEqual([row['id'] for row in rows], [self.product.pk])
        self.assertEqual(rows[0]['quantity'], 10)

    def test_web_loading_list_is_isolated_for_operator(self):
        other = User.objects.create_user(username='other-operator', password='StrongPass123!')
        LoadingOrder.objects.create(number='CHG-OTHER-1', operator=other, created_by=self.admin)
        self.client.force_login(self.operator)
        response = self.client.get(reverse('loading_order_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.number)
        self.assertNotContains(response, 'CHG-OTHER-1')

    def test_operator_cannot_pay_another_operators_sale(self):
        foreign_sale = create_sale(
            client=self.client_record,
            lines=[{'product': self.product, 'quantity': 1}],
            user=self.admin,
        )
        self.operator.user_permissions.add(Permission.objects.get(codename='change_sale', content_type__app_label='commerce'))
        validate_loading_order(self.order, user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(self.operator).access_token}')
        response = self.client.post(
            reverse('api-payment-list'),
            {'sale': foreign_sale.pk, 'amount': '1.00', 'payment_type': 'cash'},
            format='json', HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Payment.objects.filter(sale=foreign_sale).exists())


class IdempotencyTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='idem-admin', password='StrongPass123!')
        cls.customer = Client.objects.create(name='Client idem')
        cls.product = Product.objects.create(name='Produit idem', purchase_price=10, sale_price=20, quantity=0)
        record_stock_movement(product=cls.product, movement_type=StockMovement.ENTRY, quantity=20, user=cls.admin)

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(self.admin).access_token}')

    def test_sale_replay_returns_same_resource_without_second_stock_exit(self):
        payload = {'client': self.customer.pk, 'items': [{'product': self.product.pk, 'quantity': 2}]}
        key = str(uuid4())
        first = self.client.post(reverse('api-sale-list'), payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
        second = self.client.post(reverse('api-sale-list'), payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.json()['data']['id'], second.json()['data']['id'])
        self.assertEqual(second['Idempotency-Replayed'], 'true')
        self.assertEqual(Sale.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 18)

    def test_same_key_with_different_payload_is_rejected(self):
        key = str(uuid4())
        base = {'client': self.customer.pk, 'items': [{'product': self.product.pk, 'quantity': 1}]}
        self.assertEqual(self.client.post(reverse('api-sale-list'), base, format='json', HTTP_IDEMPOTENCY_KEY=key).status_code, 201)
        changed = {'client': self.customer.pk, 'items': [{'product': self.product.pk, 'quantity': 2}]}
        response = self.client.post(reverse('api-sale-list'), changed, format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'IDEMPOTENCY_KEY_REUSED')

    def test_payment_replay_prevents_duplicate_payment(self):
        sale = create_sale(client=self.customer, lines=[{'product': self.product, 'quantity': 1}], user=self.admin)
        payload = {'sale': sale.pk, 'amount': '5.00', 'payment_type': 'cash'}
        key = str(uuid4())
        for _ in range(2):
            response = self.client.post(reverse('api-payment-list'), payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 1)


class PostgreSQLLoadingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Verrouillage concurrent validé dans la CI PostgreSQL.')
        self.admin = User.objects.create_superuser(username='concurrent-admin', password='StrongPass123!')
        self.operator = User.objects.create_user(username='concurrent-operator', password='StrongPass123!')
        self.product = Product.objects.create(name='Concurrent product', purchase_price=10, sale_price=20, quantity=0)
        record_stock_movement(product=self.product, movement_type=StockMovement.ENTRY, quantity=10, user=self.admin)
        self.order = LoadingOrder.objects.create(number='CHG-CONCURRENT-1', operator=self.operator, created_by=self.admin)
        LoadingOrderLine.objects.create(loading_order=self.order, product=self.product, quantity=8)

    def _validate(self):
        close_old_connections()
        try:
            validate_loading_order(self.order.pk, user=User.objects.get(pk=self.admin.pk))
            return 'ok'
        except Exception:
            return 'rejected'
        finally:
            close_old_connections()

    def test_only_one_concurrent_validation_changes_stock(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._validate(), range(2)))
        self.assertEqual(results.count('ok'), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 2)
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 8)


class PostgreSQLIdempotencyConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Idempotence concurrente validée dans la CI PostgreSQL.')
        self.admin = User.objects.create_superuser(username='idem-concurrent-admin', password='StrongPass123!')
        self.customer = Client.objects.create(name='Concurrent idem client')
        self.product = Product.objects.create(name='Concurrent idem product', purchase_price=10, sale_price=20, quantity=0)
        record_stock_movement(product=self.product, movement_type=StockMovement.ENTRY, quantity=10, user=self.admin)
        self.token = str(MobileTokenSerializer.get_token(self.admin).access_token)
        self.key = str(uuid4())

    def _post_sale(self):
        close_old_connections()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        try:
            response = client.post(
                reverse('api-sale-list'),
                {'client': self.customer.pk, 'items': [{'product': self.product.pk, 'quantity': 2}]},
                format='json', HTTP_IDEMPOTENCY_KEY=self.key,
            )
            return response.status_code, response.json()
        finally:
            close_old_connections()

    def test_two_simultaneous_identical_posts_create_one_sale(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._post_sale(), range(2)))
        self.assertEqual([status for status, _ in results], [201, 201])
        self.assertEqual(results[0][1]['data']['id'], results[1][1]['data']['id'])
        self.assertEqual(Sale.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)
