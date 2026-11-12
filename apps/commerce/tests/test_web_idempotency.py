"""Synthetic Web form retries; no real accounts, archive, or external service."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections
from django.http import HttpResponseRedirect, QueryDict
from django.test import Client as WebClient, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from apps.api.models import IdempotencyRecord
from apps.commerce.forms import PurchaseForm, SaleForm
from apps.commerce.models import (
    InvoiceSequence, Payment, Purchase, PurchaseLine, Sale, SaleLine,
    TicketSequence,
)
from apps.commerce.services import create_purchase, create_sale
from apps.commerce.web_idempotency import WEB_TOKEN_MAX_AGE, new_web_token, web_idempotent
from apps.core.models import AuditLog
from apps.inventory.models import (
    Brand, Category, Client, LoadingOrder, Product, StockMovement, Supplier, Unit,
)


User = get_user_model()
TOKEN_SALT = 'commerce.web-mutation.v1'


class WebMutationFixtures:
    @classmethod
    def make_fixtures(cls):
        cls.user = User.objects.create_user(username='synthetic-web-manager', role=User.MANAGER)
        cls.other = User.objects.create_user(username='synthetic-web-other', role=User.MANAGER)
        category = Category.objects.create(name='Synthetic Web retry category')
        brand = Brand.objects.create(name='Synthetic Web retry brand')
        unit = Unit.objects.create(name='Synthetic Web retry unit')
        cls.product = Product.objects.create(
            name='Synthetic Web retry product', category=category, brand=brand,
            unit=unit, quantity=20, purchase_price='10.00', sale_price='20.00',
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
        )
        cls.customer = Client.objects.create(name='Synthetic Web retry customer')
        cls.supplier = Supplier.objects.create(name='Synthetic Web retry supplier')

    def sale_payload(self, *, token=None, sale=None, quantity=2, action=SaleForm.PAY_FULL):
        payload = {
            'client': str(self.customer.pk), 'discount': '0.00', 'tax_rate': '0.00',
            'payment_type': Sale.CASH, 'settlement_action': action,
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '1' if sale else '0',
            'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk), 'lines-0-packaging': '',
            'lines-0-quantity': str(quantity), 'lines-0-unit_price': '20.00',
        }
        if sale:
            payload['lines-0-id'] = str(sale.lines.get().pk)
        if token is not None:
            payload['request_token'] = token
        return payload

    def purchase_payload(self, *, token=None, purchase=None, quantity=3, reference='SYNTHETIC-WEB-PO'):
        payload = {
            'reference': purchase.reference if purchase else reference,
            'supplier': str(self.supplier.pk), 'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '1' if purchase else '0',
            'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk), 'lines-0-packaging': '',
            'lines-0-quantity': str(quantity), 'lines-0-purchase_price': '10.00',
        }
        if purchase:
            payload['lines-0-id'] = str(purchase.lines.get().pk)
        if token is not None:
            payload['request_token'] = token
        return payload

    def seed_sale(self, *, user=None):
        return create_sale(
            client=self.customer, lines=[{
                'product': self.product, 'quantity': 2, 'unit_price': Decimal('20.00'),
            }], user=user or self.user, pay_full=False,
        )

    def seed_purchase(self, *, reference='SYNTHETIC-WEB-SEED'):
        return create_purchase(
            reference=reference, supplier=self.supplier,
            lines=[{'product': self.product, 'quantity': 2, 'purchase_price': Decimal('10.00')}],
            user=self.user, tax_rate=Decimal('0.00'),
        )

    def business_state(self):
        """Include records and successful route audits, but not refusal security logs."""
        models = (
            Product, Sale, SaleLine, Purchase, PurchaseLine, Payment,
            StockMovement, InvoiceSequence, TicketSequence, IdempotencyRecord,
        )
        state = {model._meta.label: list(model.objects.order_by('pk').values()) for model in models}
        state['successful_sale_audits'] = list(AuditLog.objects.filter(
            action__in=['Création vente', 'Modification vente'], status_code__in=[302, 303],
        ).order_by('pk').values())
        return state

    def deny(self, permission_name):
        app_label, codename = permission_name.split('.', 1)
        self.user.denied_permissions.add(Permission.objects.get(
            content_type__app_label=app_label, codename=codename,
        ))


class WebIdempotencyTests(WebMutationFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_fixtures()

    def setUp(self):
        self.client.force_login(self.user)

    def assert_redirect_replay(self, first, replay, *, operation, resource):
        self.assertEqual(first.status_code, 302)
        self.assertEqual(replay.status_code, first.status_code)
        self.assertEqual(replay['Location'], first['Location'])
        self.assertEqual(first.get('Idempotency-Replayed'), 'false')
        self.assertEqual(replay.get('Idempotency-Replayed'), 'true')
        record = IdempotencyRecord.objects.get(user=self.user, operation=operation)
        self.assertTrue(record.completed)
        self.assertEqual(record.response_status, 302)
        self.assertEqual(record.resource_type, resource._meta.model_name)
        self.assertEqual(record.resource_id, str(resource.pk))

    def test_get_mints_distinct_valid_signed_tokens_for_all_four_forms(self):
        sale, purchase = self.seed_sale(), self.seed_purchase()
        urls = [
            reverse('sale_create'), reverse('sale_update', args=[sale.pk]),
            reverse('purchase_create'), reverse('purchase_update', args=[purchase.pk]),
        ]
        tokens = []
        for url in urls:
            for _ in range(2):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                token = response.context['form']['request_token'].value()
                self.assertTrue(token)
                nonce = signing.TimestampSigner(salt=TOKEN_SALT).unsign(token, max_age=WEB_TOKEN_MAX_AGE)
                self.assertRegex(nonce, r'^[0-9a-f]{32}$')
                self.assertContains(response, 'name="request_token"')
                tokens.append(token)
        self.assertEqual(len(set(tokens)), len(tokens))
        self.assertEqual(WEB_TOKEN_MAX_AGE, 12 * 60 * 60)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_sale_create_retry_preserves_one_invoice_stock_payment_and_audit(self):
        payload = self.sale_payload(token=new_web_token())
        url = reverse('sale_create')
        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        sale = Sale.objects.get()
        baseline = self.business_state()
        with patch('apps.commerce.views.SaleForm.is_valid', side_effect=AssertionError('revalidated replay')):
            replay = self.client.post(url, payload)
        self.assert_redirect_replay(first, replay, operation='web.sale.create', resource=sale)
        self.assertEqual(self.business_state(), baseline)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 18)
        self.assertEqual(sale.total, Decimal('40.00'))
        self.assertEqual(sale.payments.get().amount, Decimal('40.00'))
        self.assertEqual(StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='Création vente', status_code=302).count(), 1)

    def test_sale_update_retry_does_not_add_partial_payment_or_stock_twice(self):
        sale = self.seed_sale()
        payload = self.sale_payload(token=new_web_token(), sale=sale, quantity=3, action=SaleForm.PAY_PARTIAL)
        payload['amount_received'] = '10.00'
        url = reverse('sale_update', args=[sale.pk])
        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        baseline = self.business_state()
        replay = self.client.post(url, payload)
        self.assert_redirect_replay(first, replay, operation=f'web.sale.{sale.pk}.update', resource=sale)
        self.assertEqual(self.business_state(), baseline)
        sale.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(sale.total, Decimal('60.00'))
        self.assertEqual(self.product.quantity, 17)
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.payments.get().amount, Decimal('10.00'))
        self.assertEqual(AuditLog.objects.filter(action='Modification vente', status_code=302).count(), 1)

    def test_purchase_create_retry_precedes_unique_reference_validation(self):
        payload = self.purchase_payload(token=new_web_token())
        url = reverse('purchase_create')
        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        purchase = Purchase.objects.get()
        baseline = self.business_state()
        with patch('apps.commerce.views.PurchaseForm.is_valid', side_effect=AssertionError('revalidated replay')):
            replay = self.client.post(url, payload)
        self.assert_redirect_replay(first, replay, operation='web.purchase.create', resource=purchase)
        self.assertEqual(self.business_state(), baseline)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 23)
        self.assertEqual(purchase.total, Decimal('30.00'))
        self.assertEqual(StockMovement.objects.filter(source_type=StockMovement.SOURCE_PURCHASE).count(), 1)

    def test_purchase_update_retry_leaves_lines_and_stock_movements_unchanged(self):
        purchase = self.seed_purchase()
        payload = self.purchase_payload(token=new_web_token(), purchase=purchase, quantity=5)
        url = reverse('purchase_update', args=[purchase.pk])
        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        baseline = self.business_state()
        replay = self.client.post(url, payload)
        self.assert_redirect_replay(first, replay, operation=f'web.purchase.{purchase.pk}.update', resource=purchase)
        self.assertEqual(self.business_state(), baseline)
        purchase.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(purchase.total, Decimal('50.00'))
        self.assertEqual(purchase.lines.get().quantity, 5)
        self.assertEqual(self.product.quantity, 25)

    def test_missing_or_blank_token_preserves_legacy_create_and_update_contracts(self):
        for token in [None, '']:
            with self.subTest(token=token):
                response = self.client.post(reverse('sale_create'), self.sale_payload(token=token))
                self.assertEqual(response.status_code, 302)
                sale = Sale.objects.latest('pk')
                response = self.client.post(
                    reverse('sale_update', args=[sale.pk]),
                    self.sale_payload(token=token, sale=sale, quantity=3, action=SaleForm.NO_PAYMENT),
                )
                self.assertEqual(response.status_code, 302)
                response = self.client.post(reverse('purchase_create'), self.purchase_payload(
                    token=token, reference=f'SYNTHETIC-LEGACY-{sale.pk}',
                ))
                self.assertEqual(response.status_code, 302)
                purchase = Purchase.objects.latest('pk')
                response = self.client.post(
                    reverse('purchase_update', args=[purchase.pk]),
                    self.purchase_payload(token=token, purchase=purchase, quantity=4),
                )
                self.assertEqual(response.status_code, 302)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_invalid_signed_form_retains_token_and_can_be_corrected(self):
        token = new_web_token()
        payload = self.sale_payload(token=token, quantity=0)
        baseline = self.business_state()
        response = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['request_token'].value(), token)
        self.assertEqual(self.business_state(), baseline)
        payload['lines-0-quantity'] = '2'
        corrected = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(corrected.status_code, 302)
        self.assertEqual(IdempotencyRecord.objects.filter(completed=True).count(), 1)

    def test_invalid_legacy_form_renders_a_new_signed_token_for_correction(self):
        for token in [None, '']:
            with self.subTest(token=token):
                response = self.client.post(reverse('sale_create'), self.sale_payload(token=token, quantity=0))
                self.assertEqual(response.status_code, 200)
                rendered = response.context['form']['request_token'].value()
                self.assertTrue(rendered)
                signing.TimestampSigner(salt=TOKEN_SALT).unsign(rendered, max_age=WEB_TOKEN_MAX_AGE)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_legacy_form_upgrade_does_not_mutate_the_original_post_data(self):
        for form_class in [SaleForm, PurchaseForm]:
            for data in [QueryDict('client=1'), QueryDict('request_token=&client=1')]:
                with self.subTest(form=form_class.__name__, explicit_blank='request_token' in data):
                    original = list(data.lists())
                    form = form_class(data=data)
                    self.assertEqual(list(data.lists()), original)
                    self.assertTrue(form['request_token'].value())
                    self.assertIsNot(form.data, data)

    def test_same_token_with_changed_body_is_rejected_without_business_changes(self):
        payload = self.sale_payload(token=new_web_token())
        self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 302)
        baseline = self.business_state()
        changed = {**payload, 'lines-0-quantity': '3'}
        response = self.client.post(reverse('sale_create'), changed)
        self.assertEqual(response.status_code, 400)
        self.assertNotEqual(response.get('Idempotency-Replayed'), 'true')
        self.assertEqual(self.business_state(), baseline)

    def test_hash_ignores_csrf_rotation_and_post_field_order(self):
        payload = self.sale_payload(token=new_web_token())
        payload['csrfmiddlewaretoken'] = 'a' * 64
        first = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(first.status_code, 302)
        baseline = self.business_state()
        reordered = dict(reversed(list(payload.items())))
        reordered['csrfmiddlewaretoken'] = 'b' * 64
        replay = self.client.post(reverse('sale_create'), reordered)
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(replay.get('Idempotency-Replayed'), 'true')
        self.assertEqual(self.business_state(), baseline)

    def test_hash_binds_every_value_in_a_multivalue_post_field(self):
        payload = self.sale_payload(token=new_web_token())
        payload['unrecognized_repeated_field'] = ['first', 'last']
        self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 302)
        baseline = self.business_state()
        changed = {**payload, 'unrecognized_repeated_field': ['different-first', 'last']}
        response = self.client.post(reverse('sale_create'), changed)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.business_state(), baseline)

    def test_hash_binds_multivalue_order_not_only_the_set_of_values(self):
        payload = self.sale_payload(token=new_web_token())
        payload['unrecognized_repeated_field'] = ['first', 'last']
        self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 302)
        baseline = self.business_state()
        changed = {**payload, 'unrecognized_repeated_field': ['last', 'first']}
        self.assertEqual(self.client.post(reverse('sale_create'), changed).status_code, 400)
        self.assertEqual(self.business_state(), baseline)

    def test_same_signed_token_is_independent_for_another_actor(self):
        payload = self.sale_payload(token=new_web_token())
        first = self.client.post(reverse('sale_create'), payload)
        self.client.force_login(self.other)
        other = self.client.post(reverse('sale_create'), payload)
        self.assertEqual([first.status_code, other.status_code], [302, 302])
        self.assertEqual(other.get('Idempotency-Replayed'), 'false')
        self.assertEqual(Sale.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.filter(operation='web.sale.create').count(), 2)
        self.assertEqual(set(Sale.objects.values_list('created_by_id', flat=True)), {self.user.pk, self.other.pk})

    def test_same_signed_token_is_independent_for_sale_and_purchase_operations(self):
        token = new_web_token()
        sale = self.client.post(reverse('sale_create'), self.sale_payload(token=token))
        purchase = self.client.post(reverse('purchase_create'), self.purchase_payload(token=token))
        self.assertEqual([sale.status_code, purchase.status_code], [302, 302])
        self.assertEqual(purchase.get('Idempotency-Replayed'), 'false')
        self.assertEqual(set(IdempotencyRecord.objects.values_list('operation', flat=True)), {
            'web.sale.create', 'web.purchase.create',
        })

    def test_web_registry_namespace_does_not_collide_with_existing_api_records(self):
        token = new_web_token()
        existing = IdempotencyRecord.objects.create(
            user=self.user, operation='sale.create', key=token,
            request_hash='a' * 64, completed=True, response_status=201,
            response_body={'id': 'synthetic-api-only'},
        )
        original = IdempotencyRecord.objects.filter(pk=existing.pk).values().get()
        response = self.client.post(reverse('sale_create'), self.sale_payload(token=token))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.get('Idempotency-Replayed'), 'false')
        self.assertEqual(IdempotencyRecord.objects.filter(pk=existing.pk).values().get(), original)
        self.assertEqual(IdempotencyRecord.objects.count(), 2)

    def test_update_token_is_scoped_to_the_document_operation(self):
        first, second = self.seed_sale(), self.seed_sale()
        token = new_web_token()
        for sale in [first, second]:
            response = self.client.post(
                reverse('sale_update', args=[sale.pk]),
                self.sale_payload(token=token, sale=sale, quantity=3, action=SaleForm.NO_PAYMENT),
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.get('Idempotency-Replayed'), 'false')
        self.assertEqual(set(IdempotencyRecord.objects.values_list('operation', flat=True)), {
            f'web.sale.{first.pk}.update', f'web.sale.{second.pk}.update',
        })

    def test_revoked_create_permission_cannot_replay_existing_result(self):
        payload = self.sale_payload(token=new_web_token())
        self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 302)
        baseline = self.business_state()
        self.deny('commerce.add_sale')
        response = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.business_state(), baseline)

    def test_revoked_purchase_update_permission_cannot_replay_existing_result(self):
        purchase = self.seed_purchase()
        url = reverse('purchase_update', args=[purchase.pk])
        payload = self.purchase_payload(token=new_web_token(), purchase=purchase)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        baseline = self.business_state()
        self.deny('commerce.change_purchase')
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.assertEqual(self.business_state(), baseline)

    def test_sale_replay_rechecks_current_operator_visibility(self):
        sale = self.seed_sale(user=self.other)
        url = reverse('sale_update', args=[sale.pk])
        payload = self.sale_payload(token=new_web_token(), sale=sale, action=SaleForm.NO_PAYMENT)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        LoadingOrder.objects.create(number='SYNTHETIC-SCOPE-CHANGE', operator=self.user, created_by=self.other)
        self.deny('inventory.view_all_loadingorders')
        baseline = self.business_state()
        self.assertEqual(self.client.post(url, payload).status_code, 404)
        self.assertEqual(self.business_state(), baseline)

    def test_deleted_sale_create_target_cannot_be_replayed(self):
        payload = self.sale_payload(token=new_web_token(), action=SaleForm.NO_PAYMENT)
        url = reverse('sale_create')
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        Sale.objects.get().delete()
        baseline = self.business_state()
        self.assertEqual(self.client.post(url, payload).status_code, 404)
        self.assertEqual(self.business_state(), baseline)

    def test_deleted_purchase_update_target_cannot_be_replayed(self):
        purchase = self.seed_purchase()
        url = reverse('purchase_update', args=[purchase.pk])
        payload = self.purchase_payload(token=new_web_token(), purchase=purchase)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        purchase.delete()
        baseline = self.business_state()
        self.assertEqual(self.client.post(url, payload).status_code, 404)
        self.assertEqual(self.business_state(), baseline)

    def test_invalid_signature_is_rejected_before_any_business_work(self):
        token = new_web_token()
        token = token[:-1] + ('A' if token[-1] != 'A' else 'B')
        baseline = self.business_state()
        response = self.client.post(reverse('sale_create'), self.sale_payload(token=token))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.business_state(), baseline)

    def test_expired_token_cannot_create_or_replay_and_is_not_replaced(self):
        with patch('django.core.signing.time.time', return_value=1_700_000_000):
            token = new_web_token()
        payload = self.sale_payload(token=token)
        baseline = self.business_state()
        with patch('django.core.signing.time.time', return_value=1_700_000_000 + WEB_TOKEN_MAX_AGE + 1):
            response = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.business_state(), baseline)
        self.assertNotContains(response, 'name="request_token"', status_code=400)

    def test_expired_token_is_rejected_even_when_registry_contains_a_completed_result(self):
        with patch('django.core.signing.time.time', return_value=1_700_000_000):
            token = new_web_token()
            payload = self.sale_payload(token=token)
            self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 302)
        baseline = self.business_state()
        with patch('django.core.signing.time.time', return_value=1_700_000_000 + WEB_TOKEN_MAX_AGE + 1):
            replay = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(self.business_state(), baseline)

    def test_expired_token_remains_rejected_after_its_registry_record_was_purged(self):
        with patch('django.core.signing.time.time', return_value=1_700_000_000):
            token = new_web_token()
            payload = self.sale_payload(token=token)
            self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 302)
        IdempotencyRecord.objects.filter(operation='web.sale.create').delete()
        baseline = self.business_state()
        with patch('django.core.signing.time.time', return_value=1_700_000_000 + 24 * 60 * 60 + 1):
            response = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.business_state(), baseline)

    def test_duplicate_token_fields_are_rejected_even_if_values_match(self):
        token = new_web_token()
        for values in [[token, token], ['', token]]:
            with self.subTest(values_count=len(values)):
                payload = self.sale_payload(token=values)
                baseline = self.business_state()
                self.assertEqual(self.client.post(reverse('sale_create'), payload).status_code, 400)
                self.assertEqual(self.business_state(), baseline)

    def test_incomplete_registry_record_returns_conflict_without_running_view(self):
        payload = self.sale_payload(token=new_web_token())
        url = reverse('sale_create')
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        IdempotencyRecord.objects.filter(operation='web.sale.create').update(
            completed=False, completed_at=None, response_status=None, response_body=None,
        )
        baseline = self.business_state()
        with patch('apps.commerce.views.create_sale', side_effect=AssertionError('incomplete replay mutated')):
            response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.business_state(), baseline)

    def test_failure_after_sale_stock_and_automatic_payment_rolls_back_and_key_can_retry(self):
        payload = self.sale_payload(token=new_web_token())
        baseline = self.business_state()

        def fail_after_write(*args, **kwargs):
            sale = create_sale(*args, **kwargs)
            self.assertTrue(sale.payments.exists())
            self.assertTrue(sale.lines.exists())
            raise ValidationError('Synthetic failure after stock and automatic payment')

        with patch('apps.commerce.views.create_sale', side_effect=fail_after_write):
            response = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['request_token'].value(), payload['request_token'])
        self.assertEqual(self.business_state(), baseline)
        corrected = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(corrected.status_code, 302)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(completed=True).count(), 1)

    def test_failure_after_purchase_stock_write_rolls_back_and_key_can_retry(self):
        payload = self.purchase_payload(token=new_web_token())
        baseline = self.business_state()

        def fail_after_write(*args, **kwargs):
            purchase = create_purchase(*args, **kwargs)
            self.assertTrue(purchase.lines.exists())
            raise ValidationError('Synthetic failure after purchase stock')

        with patch('apps.commerce.views.create_purchase', side_effect=fail_after_write):
            response = self.client.post(reverse('purchase_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.business_state(), baseline)
        self.assertEqual(self.client.post(reverse('purchase_create'), payload).status_code, 302)
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(completed=True).count(), 1)

    def test_business_integrity_error_rolls_back_and_is_not_swallowed_as_a_replay(self):
        token = new_web_token()
        request = RequestFactory().post('/synthetic-only/', self.sale_payload(token=token))
        request.user = self.user
        baseline = self.business_state()

        @web_idempotent('web.synthetic.create', 'sale', lambda *args, **kwargs: None)
        def failing_view(request):
            sale = create_sale(
                client=self.customer,
                lines=[{'product': self.product, 'quantity': 2, 'unit_price': Decimal('20.00')}],
                user=self.user, pay_full=True,
            )
            self.assertTrue(sale.payments.exists())
            raise IntegrityError('Synthetic business constraint failure')

        with self.assertRaisesMessage(IntegrityError, 'Synthetic business constraint failure'):
            failing_view(request)
        self.assertEqual(self.business_state(), baseline)

    def test_changed_body_is_rejected_before_the_replay_access_callback(self):
        token = new_web_token()
        factory = RequestFactory()
        calls = []

        @web_idempotent('web.synthetic.create', 'sale', lambda *args, **kwargs: calls.append('access'))
        def view(request):
            calls.append('mutation')
            response = HttpResponseRedirect('/synthetic-list/')
            response._web_idempotency_resource_id = '1'
            return response

        request = factory.post('/synthetic-only/', {'request_token': token, 'amount': '10'})
        request.user = self.user
        self.assertEqual(view(request).status_code, 302)
        changed = factory.post('/synthetic-only/', {'request_token': token, 'amount': '11'})
        changed.user = self.user
        self.assertEqual(view(changed).status_code, 400)
        self.assertEqual(calls, ['mutation'])

    def test_signed_token_does_not_replace_the_csrf_check(self):
        client = WebClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.get(reverse('sale_create'))
        self.assertEqual(response.status_code, 200)
        token = response.context['form']['request_token'].value()
        payload = self.sale_payload(token=token)
        self.assertEqual(client.post(reverse('sale_create'), payload).status_code, 403)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())
        payload['csrfmiddlewaretoken'] = client.cookies['csrftoken'].value
        self.assertEqual(client.post(reverse('sale_create'), payload).status_code, 302)

    def test_urlencoded_multivalue_payload_replays_without_losing_values(self):
        payload = self.sale_payload(token=new_web_token())
        pairs = list(payload.items()) + [('extra', 'first'), ('extra', 'last')]
        encoded = urlencode(pairs)
        first = self.client.post(reverse('sale_create'), encoded, content_type='application/x-www-form-urlencoded')
        self.assertEqual(first.status_code, 302)
        baseline = self.business_state()
        replay = self.client.post(reverse('sale_create'), encoded, content_type='application/x-www-form-urlencoded')
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(replay.get('Idempotency-Replayed'), 'true')
        self.assertEqual(self.business_state(), baseline)


class PostgreSQLWebReplayConcurrencyTests(WebMutationFixtures, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real concurrent Web key arbitration requires PostgreSQL.')
        self.make_fixtures()

    def test_concurrent_sale_creation_uses_distinct_connections_and_one_invoice_stock_payment_and_audit(self):
        payload = self.sale_payload(token=new_web_token())
        url = reverse('sale_create')
        barrier = Barrier(2)

        def submit(_):
            close_old_connections()
            try:
                client = WebClient()
                # Finish session writes before the barrier: race only the business POSTs.
                client.force_login(User.objects.get(pk=self.user.pk))
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=20)
                response = client.post(url, payload)
                return backend_pid, response.status_code, response.get('Location'), response.get('Idempotency-Replayed')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, range(2)))
        self.assertEqual(len({result[0] for result in results}), 2)
        self.assertEqual([result[1] for result in results], [302, 302], results)
        self.assertEqual(sorted(result[3] for result in results), ['false', 'true'])
        self.assertEqual(results[0][2], results[1][2])
        self.assertEqual(Sale.objects.count(), 1)
        sale = Sale.objects.get()
        self.assertEqual(sale.lines.count(), 1)
        self.assertEqual(sale.lines.get().quantity, 2)
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.payments.get().amount, Decimal('40.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 18)
        self.assertEqual(StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='Création vente', status_code=302).count(), 1)
        record = IdempotencyRecord.objects.get(user=self.user, operation='web.sale.create')
        self.assertTrue(record.completed)
        self.assertEqual(record.resource_type, 'sale')
        self.assertEqual(record.resource_id, str(sale.pk))
