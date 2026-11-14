"""Synthetic API replays must retain current write authorization and scope."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User
from apps.accounts.permissions import has_permission
from apps.api.exceptions import BusinessAPIException
from apps.api.idempotency import idempotent
from apps.api.models import IdempotencyRecord
from apps.commerce.models import Payment, Purchase, Sale
from apps.commerce.services import create_purchase, create_sale, update_purchase
from apps.core.models import AuditLog
from apps.inventory.models import (
    Client, LoadingOrder, LoadingOrderLine, OperatorStock,
    OperatorStockMovement, Product, StockMovement, Supplier,
)
from apps.inventory.services import record_stock_movement, validate_loading_order


class IdempotencyReplayCallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='replay-helper', password='unused')
        self.request = SimpleNamespace(
            user=self.user,
            headers={'Idempotency-Key': 'callback-order-key'},
            data={'amount': '10.00'},
        )

    def test_callback_checks_only_completed_equal_payload_replays(self):
        authorization = Mock()
        mutation = Mock(return_value=Response({'id': 42}, status=201))
        first = idempotent(
            self.request, 'test.callback', mutation, replay_check=authorization,
        )
        self.assertEqual(first['Idempotency-Replayed'], 'false')
        authorization.assert_not_called()
        second = idempotent(
            self.request, 'test.callback', mutation, replay_check=authorization,
        )
        self.assertEqual(second['Idempotency-Replayed'], 'true')
        mutation.assert_called_once_with()
        authorization.assert_called_once()
        self.assertTrue(authorization.call_args.args[0].completed)

        authorization.reset_mock()
        self.request.data = {'amount': '11.00'}
        with self.assertRaises(BusinessAPIException) as raised:
            idempotent(self.request, 'test.callback', mutation, replay_check=authorization)
        self.assertEqual(raised.exception.business_code, 'IDEMPOTENCY_KEY_REUSED')
        authorization.assert_not_called()
        mutation.assert_called_once_with()

    def test_incomplete_record_never_runs_replay_authorization_or_mutation(self):
        from apps.api.idempotency import _request_hash

        IdempotencyRecord.objects.create(
            user=self.user, operation='test.callback', key='callback-order-key',
            request_hash=_request_hash(self.request), completed=False,
        )
        authorization = Mock()
        mutation = Mock()
        with self.assertRaises(BusinessAPIException) as raised:
            idempotent(self.request, 'test.callback', mutation, replay_check=authorization)
        self.assertEqual(raised.exception.business_code, 'IDEMPOTENCY_REQUEST_IN_PROGRESS')
        authorization.assert_not_called()
        mutation.assert_not_called()

    def test_replay_refusal_keeps_completed_record_and_original_result_intact(self):
        mutation = Mock(return_value=Response({'id': 42}, status=201))
        idempotent(self.request, 'test.callback', mutation)
        baseline = list(IdempotencyRecord.objects.values())
        authorization = Mock(side_effect=PermissionDenied('Synthetic current refusal.'))
        with self.assertRaises(PermissionDenied):
            idempotent(self.request, 'test.callback', mutation, replay_check=authorization)
        authorization.assert_called_once()
        mutation.assert_called_once_with()
        self.assertEqual(list(IdempotencyRecord.objects.values()), baseline)


class ReplayApiFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='replay-admin', password='unused')
        cls.writer = User.objects.create_user(username='replay-writer', password='unused')
        cls.other = User.objects.create_user(username='replay-other', password='unused')
        cls.group = Group.objects.create(name='Replay explicit write rights')
        cls.writer.groups.add(cls.group)
        cls.other.groups.add(Group.objects.create(name='Replay other explicit rights'))
        cls.customer = Client.objects.create(name='Synthetic replay customer')
        cls.supplier = Supplier.objects.create(name='Synthetic replay supplier')
        cls.product = Product.objects.create(
            name='Synthetic replay product', purchase_price=10, sale_price=20, quantity=0,
        )
        record_stock_movement(
            product=cls.product, movement_type=StockMovement.ENTRY,
            quantity=100, user=cls.admin,
        )

    def authenticate(self, user=None):
        # Permissions are intentionally changed between requests: discard caches.
        user = User.objects.get(pk=(user or self.writer).pk)
        self.client.force_authenticate(user)
        return user

    def grant(self, *names):
        for name in names:
            app_label, codename = name.split('.', 1)
            self.group.permissions.add(Permission.objects.get(
                content_type__app_label=app_label, codename=codename,
            ))
        self.authenticate()

    def deny(self, name):
        app_label, codename = name.split('.', 1)
        self.writer.denied_permissions.add(Permission.objects.get(
            content_type__app_label=app_label, codename=codename,
        ))
        self.authenticate()

    def sale(self, *, creator=None):
        return create_sale(
            client=self.customer,
            lines=[{'product': self.product, 'quantity': 2, 'unit_price': 20}],
            # Ordinary replay fixtures must be inside the seller's own scope.
            # Tests of foreign targets request that creator explicitly.
            user=creator or self.writer,
        )

    def purchase(self, reference='REPLAY-PURCHASE'):
        return create_purchase(
            reference=reference, supplier=self.supplier,
            lines=[{'product': self.product, 'quantity': 2, 'purchase_price': 10}],
            user=self.admin,
        )

    def financial_state(self):
        return {
            'payments': list(Payment.objects.order_by('pk').values()),
            'product_quantity': Product.objects.get(pk=self.product.pk).quantity,
            'stock_movements': list(StockMovement.objects.order_by('pk').values()),
            'operator_stocks': list(OperatorStock.objects.order_by('pk').values()),
            'operator_movements': list(OperatorStockMovement.objects.order_by('pk').values()),
            'business_audit': list(AuditLog.objects.filter(
                action__startswith='api.',
            ).exclude(action__contains='denied').order_by('pk').values('pk', 'action')),
        }

    def payment(self, payload, key='payment-replay-key'):
        return self.client.post(
            reverse('api-payment-list'), payload, format='json', HTTP_IDEMPOTENCY_KEY=key,
        )


class ReplayAccessApiTests(ReplayApiFixtures, APITestCase):
    def test_sale_payment_replay_uses_current_write_right_not_read_right(self):
        self.grant('commerce.add_customerpayment')
        sale = self.sale()
        payload = {'sale': sale.pk, 'amount': '10.00', 'payment_type': 'cash'}
        self.assertFalse(has_permission(self.authenticate(), 'commerce.view_sale'))
        first = self.payment(payload)
        self.assertEqual(first.status_code, 201, first.data)
        baseline = self.financial_state()
        replay = self.payment(payload)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), baseline)

        self.deny('commerce.add_customerpayment')
        denied = self.payment(payload)
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertNotIn('Idempotency-Replayed', denied)
        self.assertEqual(self.financial_state(), baseline)

    def test_purchase_payment_replay_uses_current_change_right_not_read_right(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        payload = {'purchase': purchase.pk, 'amount': '10.00', 'payment_type': 'cash'}
        self.assertFalse(has_permission(self.authenticate(), 'commerce.view_purchase'))
        first = self.payment(payload)
        self.assertEqual(first.status_code, 201, first.data)
        baseline = self.financial_state()
        replay = self.payment(payload)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), baseline)

        self.deny('commerce.change_purchase')
        denied = self.payment(payload)
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertEqual(self.financial_state(), baseline)

    def test_payment_replay_rechecks_operator_scope_after_assignment_changes(self):
        self.grant('commerce.add_customerpayment', 'inventory.view_all_loadingorders')
        sale = self.sale(creator=self.other)
        payload = {'sale': sale.pk, 'amount': '10.00', 'payment_type': 'cash'}
        self.assertEqual(self.payment(payload).status_code, 201)
        LoadingOrder.objects.create(
            number='REPLAY-NEW-OPERATOR', operator=self.writer, created_by=self.admin,
        )
        # A first loading no longer controls seller confidentiality. Remove the
        # explicit global delegation to test loss of today's foreign scope.
        self.deny('inventory.view_all_loadingorders')
        baseline = self.financial_state()
        replay = self.payment(payload)
        self.assertEqual(replay.status_code, 403, replay.data)
        self.assertEqual(self.financial_state(), baseline)

    def test_full_payment_replay_does_not_revalidate_remaining_balance(self):
        self.grant('commerce.add_customerpayment')
        sale = self.sale()
        payload = {'sale': sale.pk, 'amount': str(sale.total), 'payment_type': 'cash'}
        first = self.payment(payload)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(sale.balance_due, Decimal('0.00'))
        baseline = self.financial_state()
        replay = self.payment(payload)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), baseline)

    def test_payment_legacy_record_without_resource_id_still_checks_current_rights(self):
        self.grant('commerce.add_customerpayment')
        sale = self.sale()
        payload = {'sale': sale.pk, 'amount': '10.00', 'payment_type': 'cash'}
        self.assertEqual(self.payment(payload).status_code, 201)
        IdempotencyRecord.objects.filter(operation='payment.create').update(resource_id='')
        self.deny('commerce.add_customerpayment')
        baseline = self.financial_state()
        response = self.payment(payload)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(self.financial_state(), baseline)

    def test_payment_changed_payload_is_rejected_before_contextual_replay_check(self):
        self.grant('commerce.add_customerpayment')
        sale = self.sale()
        payload = {'sale': sale.pk, 'amount': '10.00', 'payment_type': 'cash'}
        self.assertEqual(self.payment(payload).status_code, 201)
        self.deny('commerce.add_customerpayment')
        baseline = self.financial_state()
        changed = self.payment({**payload, 'amount': '11.00'})
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(changed.json()['error']['code'], 'IDEMPOTENCY_KEY_REUSED')
        self.assertEqual(self.financial_state(), baseline)

    def test_payment_unavailable_replay_target_returns_not_found_without_mutation(self):
        self.grant('commerce.add_customerpayment')
        sale = self.sale()
        payload = {'sale': sale.pk, 'amount': '10.00', 'payment_type': 'cash'}
        self.assertEqual(self.payment(payload).status_code, 201)
        # Model-level immutable payments cannot normally be deleted. Represent a
        # historical stale pointer explicitly, without bypassing ledger deletion.
        IdempotencyRecord.objects.filter(operation='payment.create').update(
            resource_id=str(Payment.objects.get().pk + 100000),
        )
        baseline = self.financial_state()
        records = list(IdempotencyRecord.objects.values())
        with patch('apps.api.serializers.PaymentSerializer.create') as mutation:
            response = self.payment(payload)
        self.assertEqual(response.status_code, 404, response.data)
        mutation.assert_not_called()
        self.assertEqual(self.financial_state(), baseline)
        self.assertEqual(list(IdempotencyRecord.objects.values()), records)

    def test_sale_update_replay_rechecks_scope_without_repeating_stock_mutations(self):
        self.grant('commerce.change_sale', 'inventory.view_all_loadingorders')
        sale = self.sale(creator=self.other)
        url = reverse('api-sale-detail', args=[sale.pk])
        payload = {'discount': '1.00'}
        first = self.client.patch(url, payload, format='json', HTTP_IDEMPOTENCY_KEY='sale-update-replay')
        self.assertEqual(first.status_code, 200, first.data)
        baseline = self.financial_state()
        replay = self.client.patch(url, payload, format='json', HTTP_IDEMPOTENCY_KEY='sale-update-replay')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), baseline)

        LoadingOrder.objects.create(
            number='REPLAY-SALE-SCOPE', operator=self.writer, created_by=self.admin,
        )
        self.deny('inventory.view_all_loadingorders')
        denied = self.client.patch(url, payload, format='json', HTTP_IDEMPOTENCY_KEY='sale-update-replay')
        self.assertEqual(denied.status_code, 404, denied.data)
        self.assertEqual(self.financial_state(), baseline)

    def test_sale_update_replay_does_not_return_a_deleted_target(self):
        self.grant('commerce.change_sale')
        sale = self.sale()
        url = reverse('api-sale-detail', args=[sale.pk])
        payload = {'discount': '1.00'}
        self.assertEqual(self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='sale-deleted-replay',
        ).status_code, 200)
        sale.delete()
        baseline = self.financial_state()
        replay = self.client.patch(url, payload, format='json', HTTP_IDEMPOTENCY_KEY='sale-deleted-replay')
        self.assertEqual(replay.status_code, 404, replay.data)
        self.assertEqual(self.financial_state(), baseline)

    def loading(self):
        order = LoadingOrder.objects.create(
            number='REPLAY-LOADING', operator=self.writer, created_by=self.admin,
        )
        LoadingOrderLine.objects.create(loading_order=order, product=self.product, quantity=5)
        return order

    def test_loading_validation_replay_ignores_stale_status_filter_but_not_assignment(self):
        self.grant('inventory.validate_loadingorder')
        order = self.loading()
        url = reverse('api-loading-order-validate', args=[order.pk]) + '?status=draft'
        first = self.client.post(url, {}, format='json', HTTP_IDEMPOTENCY_KEY='loading-validate-replay')
        self.assertEqual(first.status_code, 200, first.data)
        order.refresh_from_db()
        self.assertEqual(order.status, LoadingOrder.IN_PROGRESS)
        baseline = self.financial_state()
        replay = self.client.post(url, {}, format='json', HTTP_IDEMPOTENCY_KEY='loading-validate-replay')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), baseline)

        LoadingOrder.objects.filter(pk=order.pk).update(operator=self.other)
        denied = self.client.post(url, {}, format='json', HTTP_IDEMPOTENCY_KEY='loading-validate-replay')
        self.assertEqual(denied.status_code, 404, denied.data)
        self.assertEqual(self.financial_state(), baseline)

    def test_loading_close_replay_ignores_stale_status_filter_but_not_assignment(self):
        self.grant('inventory.close_loadingorder')
        order = self.loading()
        validate_loading_order(order, user=self.admin)
        # VALIDATED is a supported active historical state, also accepted by close.
        LoadingOrder.objects.filter(pk=order.pk).update(status=LoadingOrder.VALIDATED)
        url = reverse('api-loading-order-close', args=[order.pk]) + '?status=validated'
        first = self.client.post(url, {}, format='json', HTTP_IDEMPOTENCY_KEY='loading-close-replay')
        self.assertEqual(first.status_code, 200, first.data)
        order.refresh_from_db()
        self.assertEqual(order.status, LoadingOrder.CLOSED)
        baseline = self.financial_state()
        replay = self.client.post(url, {}, format='json', HTTP_IDEMPOTENCY_KEY='loading-close-replay')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.financial_state(), baseline)

        LoadingOrder.objects.filter(pk=order.pk).update(operator=self.other)
        denied = self.client.post(url, {}, format='json', HTTP_IDEMPOTENCY_KEY='loading-close-replay')
        self.assertEqual(denied.status_code, 404, denied.data)
        self.assertEqual(self.financial_state(), baseline)


class PurchaseUpdateIdempotencyApiTests(ReplayApiFixtures, APITestCase):
    def purchase_payload(self, purchase, quantity=3):
        return {
            'reference': purchase.reference, 'supplier': self.supplier.pk, 'tax_rate': '10.00',
            'items': [{'product': self.product.pk, 'quantity': quantity, 'purchase_price': '9.00'}],
        }

    def purchase_state(self, purchase):
        purchase.refresh_from_db()
        return {
            **self.financial_state(),
            'purchase': (purchase.reference, purchase.total, purchase.tax_rate, purchase.supplier_id),
            'lines': list(purchase.lines.order_by('pk').values()),
        }

    def test_patch_and_put_replays_keep_line_ids_stock_and_audit_unchanged(self):
        self.grant('commerce.change_purchase')
        for method in ('patch', 'put'):
            with self.subTest(method=method):
                purchase = self.purchase(reference=f'REPLAY-{method.upper()}')
                payload = self.purchase_payload(purchase)
                url = reverse('api-purchase-detail', args=[purchase.pk])
                send = getattr(self.client, method)
                key = f'purchase-{method}-replay'
                first = send(url, payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(first.status_code, 200, first.data)
                self.assertEqual(first['Idempotency-Replayed'], 'false')
                baseline = self.purchase_state(purchase)
                replay = send(url, payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(replay.status_code, 200, replay.data)
                self.assertEqual(replay['Idempotency-Replayed'], 'true')
                self.assertEqual(replay.data, first.data)
                self.assertEqual(self.purchase_state(purchase), baseline)
                self.assertEqual(purchase.lines.get().quantity, 3)
                self.assertEqual(purchase.total, Decimal('29.70'))
                self.assertEqual(IdempotencyRecord.objects.filter(
                    user=self.writer, operation=f'purchase.{purchase.pk}.update', key=key,
                    completed=True,
                ).count(), 1)

    def test_partial_patch_without_items_preserves_lines_while_incomplete_put_is_rejected(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        url = reverse('api-purchase-detail', args=[purchase.pk])
        payload = {'tax_rate': '5.00'}
        lines = list(purchase.lines.order_by('pk').values())
        movements = list(StockMovement.objects.order_by('pk').values())
        first = self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-partial-key',
        )
        self.assertEqual(first.status_code, 200, first.data)
        purchase.refresh_from_db()
        self.assertEqual(purchase.total, Decimal('21.00'))
        self.assertEqual(list(purchase.lines.order_by('pk').values()), lines)
        self.assertEqual(list(StockMovement.objects.order_by('pk').values()), movements)
        baseline = self.purchase_state(purchase)
        replay = self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-partial-key',
        )
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(self.purchase_state(purchase), baseline)

        incomplete = self.client.put(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-incomplete-put',
        )
        self.assertEqual(incomplete.status_code, 400, incomplete.data)
        self.assertFalse(IdempotencyRecord.objects.filter(key='purchase-incomplete-put').exists())
        self.assertEqual(self.purchase_state(purchase), baseline)

    def test_changed_body_same_key_is_rejected_without_stock_or_line_changes(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        payload = self.purchase_payload(purchase)
        url = reverse('api-purchase-detail', args=[purchase.pk])
        self.assertEqual(self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-changed-key',
        ).status_code, 200)
        baseline = self.purchase_state(purchase)
        changed = self.client.patch(
            url, self.purchase_payload(purchase, quantity=4), format='json',
            HTTP_IDEMPOTENCY_KEY='purchase-changed-key',
        )
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(changed.json()['error']['code'], 'IDEMPOTENCY_KEY_REUSED')
        self.assertEqual(self.purchase_state(purchase), baseline)

    def test_failed_update_rolls_back_mutation_and_key_then_same_key_can_succeed(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        payload = self.purchase_payload(purchase)
        url = reverse('api-purchase-detail', args=[purchase.pk])
        baseline = self.purchase_state(purchase)

        def fail_after_mutating(**kwargs):
            update_purchase(**kwargs)
            raise DjangoValidationError({'items': 'Synthetic failure after stock mutation.'})

        with patch('apps.api.serializers.update_purchase', side_effect=fail_after_mutating):
            failed = self.client.patch(
                url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-rollback-key',
            )
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertEqual(self.purchase_state(purchase), baseline)
        self.assertFalse(IdempotencyRecord.objects.filter(key='purchase-rollback-key').exists())
        corrected = self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-rollback-key',
        )
        self.assertEqual(corrected.status_code, 200, corrected.data)
        self.assertEqual(corrected['Idempotency-Replayed'], 'false')
        self.assertEqual(purchase.lines.get().quantity, 3)

    def test_invalid_body_does_not_reserve_key_against_corrected_payload(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        url = reverse('api-purchase-detail', args=[purchase.pk])
        failed = self.client.patch(
            url, {'items': []}, format='json', HTTP_IDEMPOTENCY_KEY='purchase-correction-key',
        )
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertFalse(IdempotencyRecord.objects.filter(key='purchase-correction-key').exists())
        corrected = self.client.patch(
            url, self.purchase_payload(purchase), format='json',
            HTTP_IDEMPOTENCY_KEY='purchase-correction-key',
        )
        self.assertEqual(corrected.status_code, 200, corrected.data)

    def test_purchase_update_without_key_preserves_legacy_client_contract(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        response = self.client.patch(
            reverse('api-purchase-detail', args=[purchase.pk]),
            self.purchase_payload(purchase), format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('Idempotency-Replayed', response)
        self.assertEqual(purchase.lines.get().quantity, 3)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_purchase_update_replay_does_not_escape_current_permissions(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        payload = self.purchase_payload(purchase)
        url = reverse('api-purchase-detail', args=[purchase.pk])
        self.assertEqual(self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-revoked-key',
        ).status_code, 200)
        baseline = self.purchase_state(purchase)
        self.deny('commerce.change_purchase')
        replay = self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-revoked-key',
        )
        self.assertEqual(replay.status_code, 403, replay.data)
        self.assertEqual(self.purchase_state(purchase), baseline)

    def test_same_key_is_independent_for_distinct_users(self):
        self.grant('commerce.change_purchase')
        purchase = self.purchase()
        payload = self.purchase_payload(purchase)
        url = reverse('api-purchase-detail', args=[purchase.pk])
        first = self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-shared-user-key',
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.authenticate(self.admin)
        other = self.client.patch(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-shared-user-key',
        )
        self.assertEqual(other.status_code, 200, other.data)
        self.assertEqual(other['Idempotency-Replayed'], 'false')
        self.assertEqual(IdempotencyRecord.objects.filter(key='purchase-shared-user-key').count(), 2)


class PostgreSQLPurchaseReplayConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Concurrent unique-key arbitration requires PostgreSQL.')
        self.user = User.objects.create_superuser(username='purchase-race-admin', password='unused')
        self.supplier = Supplier.objects.create(name='Synthetic purchase race supplier')
        self.product = Product.objects.create(
            name='Synthetic purchase race product', purchase_price=10, sale_price=20, quantity=0,
        )
        self.purchase = create_purchase(
            reference='PURCHASE-REPLAY-RACE', supplier=self.supplier,
            lines=[{'product': self.product, 'quantity': 2, 'purchase_price': 10}],
            user=self.user,
        )

    def test_concurrent_identical_patch_executes_one_update_on_distinct_connections(self):
        barrier = Barrier(2)
        initial_movements = StockMovement.objects.count()
        payload = {
            'items': [{'product': self.product.pk, 'quantity': 3, 'purchase_price': '9.00'}],
        }
        url = reverse('api-purchase-detail', args=[self.purchase.pk])

        def update(_):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=self.user.pk))
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=20)
                response = client.patch(
                    url, payload, format='json', HTTP_IDEMPOTENCY_KEY='purchase-concurrent-key',
                )
                return backend_pid, response.status_code, response.data, response.get('Idempotency-Replayed')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, range(2)))
        self.assertEqual(len({result[0] for result in results}), 2)
        self.assertEqual([result[1] for result in results], [200, 200], results)
        self.assertEqual(sorted(result[3] for result in results), ['false', 'true'])
        self.assertEqual(results[0][2], results[1][2])
        self.product.refresh_from_db()
        self.purchase.refresh_from_db()
        self.assertEqual(self.product.quantity, 3)
        self.assertEqual(self.purchase.total, Decimal('27.00'))
        self.assertEqual(self.purchase.lines.count(), 1)
        self.assertEqual(self.purchase.lines.get().quantity, 3)
        self.assertEqual(StockMovement.objects.count(), initial_movements + 2)
        self.assertEqual(AuditLog.objects.filter(action='api.purchase.update').count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(
            user=self.user, operation=f'purchase.{self.purchase.pk}.update',
            key='purchase-concurrent-key', completed=True,
        ).count(), 1)
