"""Synthetic sale/loading lock regressions; no external data or processes."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
import time
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase, override_settings

from apps.accounts.models import User
from apps.commerce import models as commerce_models
from apps.commerce.models import Payment, Sale, SaleLine
from apps.commerce.services import create_sale, update_sale
from apps.inventory.models import Client, LoadingOrder, LoadingOrderLine, OperatorStock, Product
from apps.inventory.services import active_loading_for_operator, close_loading_order, record_stock_movements, validate_loading_order


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SaleStockLockOrderTests(TestCase):
    def setUp(self):
        self.client_record = Client.objects.create(name='Synthetic lock customer')
        self.a = Product.objects.create(name='Synthetic lock A', purchase_price=1, sale_price=2, quantity=20)
        self.b = Product.objects.create(name='Synthetic lock B', purchase_price=1, sale_price=2, quantity=20)

    def test_replacement_locks_the_old_and_new_product_union_before_mutations(self):
        sale = create_sale(client=self.client_record, lines=[{'product': self.a, 'quantity': 1, 'unit_price': 2}])
        original_fetch = QuerySet._fetch_all
        product_locks = []

        def observe_fetch(queryset):
            if queryset.model is Product and queryset.query.select_for_update and queryset._result_cache is None:
                product_locks.append(queryset.query.sql_with_params()[1])
            return original_fetch(queryset)

        with patch.object(QuerySet, '_fetch_all', autospec=True, side_effect=observe_fetch):
            update_sale(sale=sale, lines=[{'product': self.b, 'quantity': 1, 'unit_price': 2}])
        self.assertEqual(tuple(product_locks[0]), (self.a.pk, self.b.pk))
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.quantity, self.b.quantity), (20, 19))

    def test_sale_line_queryset_delete_keeps_stock_aware_accounting(self):
        sale = create_sale(client=self.client_record, lines=[
            {'product': self.a, 'quantity': 1, 'unit_price': 2},
            {'product': self.b, 'quantity': 2, 'unit_price': 2},
        ])
        count, details = SaleLine.objects.filter(sale=sale).delete()
        self.assertEqual(count, 2)
        self.assertEqual(details['commerce.SaleLine'], 2)
        self.assertFalse(sale.lines.exists())
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.quantity, self.b.quantity), (20, 20))
        self.assertEqual(SaleLine.objects.none().delete(), (0, {}))

    def test_concurrent_line_parent_change_is_refused_before_stock_mutation(self):
        sale = create_sale(client=self.client_record, lines=[{'product': self.a, 'quantity': 1, 'unit_price': 2}])
        other = create_sale(client=self.client_record, lines=[{'product': self.b, 'quantity': 1, 'unit_price': 2}])
        line = sale.lines.get()
        original_lock = commerce_models._lock_sales_for_stock

        def move_before_parent_lock(sale_ids):
            # Simulate an already-committed concurrent reparenting between the
            # initial identity read and parent acquisition. The operation must
            # recheck the locked line, not mutate a third, unlocked sale.
            SaleLine.objects.filter(pk=line.pk).update(sale_id=other.pk)
            return original_lock(sale_ids)

        for operation in ('save', 'delete', 'queryset_delete'):
            with self.subTest(operation=operation), patch(
                'apps.commerce.models._lock_sales_for_stock', side_effect=move_before_parent_lock,
            ):
                with self.assertRaises(ValidationError):
                    if operation == 'save':
                        line.quantity = 2
                        line.packaging_quantity = 2
                        line.save()
                    elif operation == 'delete':
                        line.delete()
                    else:
                        SaleLine.objects.filter(pk=line.pk).delete()
            line.refresh_from_db()
            self.a.refresh_from_db()
            self.b.refresh_from_db()
            self.assertEqual((line.sale_id, line.quantity, self.a.quantity, self.b.quantity), (sale.pk, 1, 19, 19))

    def test_multi_sale_delete_prelocks_all_parents_and_products_in_pk_order(self):
        first = create_sale(client=self.client_record, lines=[{'product': self.a, 'quantity': 1, 'unit_price': 2}])
        second = create_sale(client=self.client_record, lines=[{'product': self.b, 'quantity': 2, 'unit_price': 2}])
        original_fetch = QuerySet._fetch_all
        parent_orders, product_batches, sale_batches = [], [], []

        def observe_fetch(queryset):
            observe = queryset.query.select_for_update and queryset._result_cache is None
            if observe and queryset.model in {Sale, LoadingOrder}:
                parent_orders.append(queryset.query.order_by)
            result = original_fetch(queryset)
            if observe and queryset.model is Product:
                product_batches.append(tuple(product.pk for product in queryset._result_cache))
            if observe and queryset.model is Sale:
                sale_batches.append(tuple(sale.pk for sale in queryset._result_cache))
            return result

        with patch.object(QuerySet, '_fetch_all', autospec=True, side_effect=observe_fetch):
            _count, details = Sale.objects.filter(pk__in=[second.pk, first.pk]).delete()
        self.assertEqual(sale_batches[0], (first.pk, second.pk))
        self.assertTrue(parent_orders)
        self.assertTrue(all(order == ('pk',) for order in parent_orders), parent_orders)
        self.assertEqual(product_batches[0], (self.a.pk, self.b.pk))
        self.assertEqual(details['commerce.Sale'], 2)
        self.assertFalse(Sale.objects.exists())
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.quantity, self.b.quantity), (20, 20))


@skipUnless(connection.vendor == 'postgresql', 'Real lifecycle lock races require PostgreSQL.')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PostgreSQLSaleLoadingLockOrderTests(TransactionTestCase):
    def setUp(self):
        self.operator = User.objects.create_user(username='lock-order-operator', password='Synthetic-lock-only-2040!')
        self.admin = User.objects.create_superuser(username='lock-order-admin', password='Synthetic-lock-only-2040!')
        self.customer = Client.objects.create(name='Synthetic lock customer')
        self.product = Product.objects.create(name='Synthetic lock product', purchase_price=1, sale_price=2, quantity=20)
        self.order = LoadingOrder.objects.create(number='SYNTHETIC-LOCK-ORDER', operator=self.operator, created_by=self.admin)
        LoadingOrderLine.objects.create(loading_order=self.order, product=self.product, quantity=5)
        validate_loading_order(self.order, user=self.admin)

    @staticmethod
    def wait_on_loading_lock(pid):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT wait_event_type, query FROM pg_stat_activity WHERE pid = %s', [pid])
                row = cursor.fetchone()
            if row and row[0] == 'Lock' and 'inventory_loadingorder' in row[1].lower():
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def sqlstate(error):
        return getattr(error.__cause__, 'sqlstate', None) or getattr(error.__cause__, 'pgcode', None) or type(error.__cause__).__name__

    def run_close_first(self, operation):
        sale = None
        if operation != 'create':
            sale = create_sale(client=self.customer, lines=[{
                'product': self.product, 'quantity': 1, 'unit_price': 2,
            }], user=self.operator)
        close_paused, release_close = Event(), Event()
        identities = Queue()

        def pause_before_depot_product(changes):
            close_paused.set()
            if not release_close.wait(timeout=30):
                raise AssertionError('Lifecycle race coordination timed out')
            return record_stock_movements(changes)

        def worker(kind):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    identities.put((kind, cursor.fetchone()[0]))
                try:
                    if kind == 'close':
                        close_loading_order(self.order, user=self.admin)
                    elif operation == 'create':
                        create_sale(client=self.customer, lines=[{
                            'product': self.product, 'quantity': 1, 'unit_price': 2,
                        }], user=self.operator)
                    elif operation == 'update':
                        update_sale(sale=sale, lines=[{
                            'product': self.product, 'quantity': 2, 'unit_price': 2,
                        }], user=self.operator)
                    elif operation == 'direct_save':
                        line = SaleLine.objects.get(sale=sale)
                        line.quantity = 2
                        line.packaging_quantity = 2
                        line.save()
                    elif operation == 'direct_delete':
                        SaleLine.objects.get(sale=sale).delete()
                    else:
                        SaleLine.objects.filter(sale=sale).delete()
                except ValidationError:
                    return kind, 'business_denied'
                except DatabaseError as error:
                    return kind, self.sqlstate(error)
                return kind, 'ok'
            finally:
                connections.close_all()

        with patch('apps.inventory.services.record_stock_movements', pause_before_depot_product):
            with ThreadPoolExecutor(max_workers=2) as executor:
                close_future = executor.submit(worker, 'close')
                try:
                    self.assertTrue(close_paused.wait(timeout=30))
                    operation_future = executor.submit(worker, 'operation')
                    pids = dict(identities.get(timeout=30) for _ in range(2))
                    self.assertNotEqual(pids['close'], pids['operation'])
                    self.assertTrue(self.wait_on_loading_lock(pids['operation']))
                finally:
                    release_close.set()
                results = dict([close_future.result(timeout=30), operation_future.result(timeout=30)])
        self.assertEqual(results, {
            'close': 'ok', 'operation': 'ok' if operation == 'create' else 'business_denied',
        })
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        stock = OperatorStock.objects.get(operator=self.operator, product=self.product)
        self.assertEqual((self.order.status, self.product.quantity, stock.quantity), (LoadingOrder.CLOSED, 19, 0))
        self.assertEqual(Sale.objects.count(), 1)
        saved_sale = Sale.objects.get()
        self.assertEqual(saved_sale.loading_order_id, None if operation == 'create' else self.order.pk)
        self.assertEqual(saved_sale.lines.get().quantity, 1)
        self.assertFalse(Payment.objects.exists())

    def test_close_then_create_preserves_existing_depot_fallback_without_deadlock(self):
        self.run_close_first('create')

    def test_close_then_service_update_is_business_refusal_without_deadlock(self):
        self.run_close_first('update')

    def test_close_then_direct_line_save_is_business_refusal_without_deadlock(self):
        self.run_close_first('direct_save')

    def test_close_then_direct_line_delete_is_business_refusal_without_deadlock(self):
        self.run_close_first('direct_delete')

    def test_close_then_line_queryset_delete_is_business_refusal_without_deadlock(self):
        self.run_close_first('queryset_delete')

    def test_create_then_close_serializes_and_returns_only_unsold_operator_stock(self):
        sale_paused, release_sale = Event(), Event()
        identities = Queue()

        def pause_after_order_lock(*args, **kwargs):
            order = active_loading_for_operator(*args, **kwargs)
            sale_paused.set()
            if not release_sale.wait(timeout=30):
                raise AssertionError('Lifecycle race coordination timed out')
            return order

        def worker(kind):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    identities.put((kind, cursor.fetchone()[0]))
                if kind == 'create':
                    create_sale(client=self.customer, lines=[{
                        'product': self.product, 'quantity': 1, 'unit_price': 2,
                    }], user=self.operator)
                else:
                    close_loading_order(self.order, user=self.admin)
                return 'ok'
            finally:
                connections.close_all()

        with patch('apps.commerce.services.active_loading_for_operator', pause_after_order_lock):
            with ThreadPoolExecutor(max_workers=2) as executor:
                sale_future = executor.submit(worker, 'create')
                try:
                    self.assertTrue(sale_paused.wait(timeout=30))
                    close_future = executor.submit(worker, 'close')
                    pids = dict(identities.get(timeout=30) for _ in range(2))
                    self.assertNotEqual(pids['create'], pids['close'])
                    self.assertTrue(self.wait_on_loading_lock(pids['close']))
                finally:
                    release_sale.set()
                self.assertEqual((sale_future.result(timeout=30), close_future.result(timeout=30)), ('ok', 'ok'))
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual((self.order.status, self.product.quantity), (LoadingOrder.CLOSED, 19))
        self.assertEqual(OperatorStock.objects.get(operator=self.operator, product=self.product).quantity, 0)
        self.assertEqual(Sale.objects.get().loading_order_id, self.order.pk)

    def test_caller_joins_do_not_lock_client_during_document_or_line_queryset_delete(self):
        for kind in ('sale', 'line'):
            with self.subTest(kind=kind):
                sale = create_sale(client=self.customer, lines=[{
                    'product': self.product, 'quantity': 1, 'unit_price': 2,
                }])
                client_locked, release_client = Event(), Event()
                identities = Queue()

                def hold_client():
                    close_old_connections()
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            identities.put(cursor.fetchone()[0])
                        with transaction.atomic():
                            Client.objects.select_for_update().get(pk=self.customer.pk)
                            client_locked.set()
                            if not release_client.wait(timeout=30):
                                raise AssertionError('Client lock coordination timed out')
                    finally:
                        connections.close_all()

                def delete_joined_queryset():
                    close_old_connections()
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            identities.put(cursor.fetchone()[0])
                        if kind == 'sale':
                            queryset = Sale.objects.filter(pk=sale.pk, client__name=self.customer.name).select_related('client')
                        else:
                            queryset = SaleLine.objects.filter(
                                sale=sale, sale__client__name=self.customer.name,
                            ).select_related('sale__client').select_for_update()
                        return queryset.delete()
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    holder = executor.submit(hold_client)
                    try:
                        self.assertTrue(client_locked.wait(timeout=30))
                        deletion = executor.submit(delete_joined_queryset)
                        first_pid, second_pid = identities.get(timeout=30), identities.get(timeout=30)
                        self.assertNotEqual(first_pid, second_pid)
                        # The deletion must complete while Client remains
                        # exclusively held. Caller joins are read-only here.
                        count, _details = deletion.result(timeout=15)
                        self.assertGreaterEqual(count, 1)
                    finally:
                        release_client.set()
                    holder.result(timeout=30)
                self.assertFalse(SaleLine.objects.filter(sale_id=sale.pk).exists())
