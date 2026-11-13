"""Synthetic receipt retries, with no external service or real financial data."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections
from django.test import Client as WebClient, TestCase, TransactionTestCase
from django.urls import reverse

from apps.api.models import IdempotencyRecord
from apps.commerce import views as commerce_views
from apps.commerce.forms import PaymentForm
from apps.commerce.models import Payment, Purchase, Sale
from apps.commerce.web_idempotency import WEB_TOKEN_MAX_AGE, new_web_token
from apps.core.models import AuditLog
from apps.inventory.models import Client, LoadingOrder, Supplier


User = get_user_model()
DATE = '2026-11-12T10:30'
TOKEN_SALT = 'commerce.web-mutation.v1'


class PaymentFixtures:
    @classmethod
    def make_fixtures(cls):
        cls.user = User.objects.create_user(username='synthetic-web-payment-manager', role=User.MANAGER)
        cls.other = User.objects.create_user(username='synthetic-web-payment-other', role=User.MANAGER)
        cls.customer = Client.objects.create(name='Synthetic direct receipt customer')
        cls.receivable_customer = Client.objects.create(name='Synthetic receivable receipt customer')
        cls.supplier = Supplier.objects.create(name='Synthetic receipt supplier')
        cls.sale = Sale.objects.create(
            invoice_number='SYNTHETIC-DIRECT-RECEIPT', client=cls.customer,
            total=Decimal('100.00'), created_by=cls.other,
        )
        cls.receivable_sale = Sale.objects.create(
            invoice_number='SYNTHETIC-RECEIVABLE-RECEIPT', client=cls.receivable_customer,
            total=Decimal('100.00'), created_by=cls.other,
        )
        cls.purchase = Purchase.objects.create(
            reference='SYNTHETIC-PURCHASE-RECEIPT', supplier=cls.supplier, total=Decimal('80.00'),
        )

    def cases(self):
        return [
            ('sale', reverse('sale_payment_create', args=[self.sale.pk]), self.sale),
            ('purchase', reverse('purchase_payment_create', args=[self.purchase.pk]), self.purchase),
            ('receivable', reverse('receivable_payment_create', args=[self.receivable_customer.pk]), self.receivable_sale),
        ]

    def payload(self, kind, *, token=None, amount='25.00', date=DATE):
        data = {
            'amount': amount, 'payment_type': Payment.CASH,
            'external_reference': 'SYNTHETIC-RECEIPT-42', 'notes': 'Synthetic receipt only',
        }
        if kind == 'receivable':
            data['sale'] = str(self.receivable_sale.pk)
        if token is not None:
            data['request_token'] = token
        if date is not None:
            data['created_at'] = date
        return data

    def seed_historical(self, kind, document, *, key, amount='25.00', actor=None):
        return Payment.objects.create(
            **{'purchase' if kind == 'purchase' else 'sale': document},
            amount=Decimal(amount), payment_type=Payment.CASH,
            created_at=PaymentForm().fields['created_at'].clean(DATE),
            created_by=actor or self.user, idempotency_key=key,
            external_reference='SYNTHETIC-RECEIPT-42', notes='Synthetic receipt only',
        )

    def state(self):
        state = {
            model._meta.label: list(model.objects.order_by('pk').values())
            for model in (Payment, Sale, Purchase, IdempotencyRecord)
        }
        state['successful_audits'] = list(AuditLog.objects.filter(
            status_code__in=[302, 303],
        ).order_by('pk').values())
        return state

    def deny(self, name):
        app, codename = name.split('.', 1)
        self.user.denied_permissions.add(Permission.objects.get(
            content_type__app_label=app, codename=codename,
        ))


class WebPaymentIdempotencyTests(PaymentFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_fixtures()

    def setUp(self):
        self.client.force_login(self.user)

    def test_all_payment_forms_issue_distinct_signed_twelve_hour_tokens(self):
        tokens = []
        for kind, url, document in self.cases():
            for _ in range(2):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                token = response.context['form']['request_token'].value()
                nonce = signing.TimestampSigner(salt=TOKEN_SALT).unsign(token, max_age=WEB_TOKEN_MAX_AGE)
                self.assertRegex(nonce, r'^[0-9a-f]{32}$')
                self.assertContains(response, 'name="request_token"')
                self.assertTrue(response.context['form'].fields['created_at'].initial or kind == 'receivable')
                tokens.append(token)
        self.assertEqual(len(set(tokens)), 6)
        self.assertEqual(WEB_TOKEN_MAX_AGE, 12 * 60 * 60)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_signed_full_payment_replays_all_three_routes_before_financial_validation(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token(), amount=str(document.total))
                first = self.client.post(url, payload)
                self.assertEqual(first.status_code, 302)
                self.assertEqual(first.get('Idempotency-Replayed'), 'false')
                payment = document.payments.get()
                self.assertIsNone(payment.idempotency_key)
                self.assertEqual(document.balance_due, Decimal('0.00'))
                baseline = self.state()
                with patch.object(Payment, 'save', side_effect=AssertionError('Replay saved a payment')):
                    replay = self.client.post(url, payload)
                self.assertEqual(replay.status_code, 302)
                self.assertEqual(replay['Location'], first['Location'])
                self.assertEqual(replay.get('Idempotency-Replayed'), 'true')
                self.assertEqual(self.state(), baseline)
                record = IdempotencyRecord.objects.get(key=payload['request_token'])
                self.assertTrue(record.completed)
                self.assertEqual(record.resource_type, 'payment')
                self.assertEqual(record.resource_id, str(payment.pk))

    def test_new_legacy_requests_without_dates_are_hashed_and_replayed_when_fully_paid(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                key = f'synthetic-old-form-{kind}'
                payload = self.payload(kind, token=key, amount=str(document.total), date=None)
                first = self.client.post(url, payload)
                self.assertEqual(first.status_code, 302)
                self.assertEqual(document.payments.get().idempotency_key, key)
                baseline = self.state()
                replay = self.client.post(url, payload)
                self.assertEqual(replay.status_code, 302)
                self.assertEqual(replay.get('Idempotency-Replayed'), 'true')
                self.assertEqual(self.state(), baseline)

    def test_new_signed_requests_without_dates_keep_the_original_generated_date(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token(), date=None)
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                baseline = self.state()
                self.assertEqual(self.client.post(url, payload).get('Idempotency-Replayed'), 'true')
                self.assertEqual(self.state(), baseline)

    def test_missing_tokens_preserve_legacy_first_submission_without_a_registry_record(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                response = self.client.post(url, self.payload(kind, date=None))
                self.assertEqual(response.status_code, 302)
                payment = document.payments.get()
                self.assertEqual(payment.created_by, self.user)
                self.assertIsNone(payment.idempotency_key)
                self.assertIsNotNone(payment.created_at)
                self.assertFalse(IdempotencyRecord.objects.exists())

    def test_invalid_first_submission_does_not_consume_the_key_and_keeps_it_for_correction(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                token = new_web_token()
                payload = self.payload(kind, token=token, amount='0.00')
                baseline = self.state()
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['form']['request_token'].value(), token)
                self.assertEqual(self.state(), baseline)
                corrected = self.client.post(url, {**payload, 'amount': '25.00'})
                self.assertEqual(corrected.status_code, 302)
                self.assertEqual(document.payments.count(), 1)

    def test_error_page_upgrades_a_missing_token_without_assigning_it_to_legacy_payment_key(self):
        kind, url, document = self.cases()[0]
        response = self.client.post(url, self.payload(kind, amount='0.00'))
        self.assertEqual(response.status_code, 200)
        token = response.context['form']['request_token'].value()
        signing.TimestampSigner(salt=TOKEN_SALT).unsign(token, max_age=WEB_TOKEN_MAX_AGE)
        corrected = self.client.post(url, self.payload(kind, token=token))
        self.assertEqual(corrected.status_code, 302)
        self.assertIsNone(document.payments.get().idempotency_key)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_historical_full_payments_with_explicit_matching_dates_can_be_adopted(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                key = f'synthetic-historical-{kind}'
                payment = self.seed_historical(kind, document, key=key, amount=str(document.total))
                payload = self.payload(kind, token=key, amount=str(document.total))
                previous = list(Payment.objects.values())
                with patch.object(Payment, 'save', side_effect=AssertionError('Historical adoption saved')):
                    response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.get('Idempotency-Replayed'), 'true')
                self.assertEqual(list(Payment.objects.values()), previous)
                record = IdempotencyRecord.objects.get(key=key)
                self.assertTrue(record.completed)
                self.assertEqual(record.resource_id, str(payment.pk))
                baseline = self.state()
                self.assertEqual(self.client.post(url, payload).get('Idempotency-Replayed'), 'true')
                self.assertEqual(self.state(), baseline)

    def test_unregistered_historical_payments_without_dates_fail_closed_on_all_routes(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                key = f'synthetic-no-historical-date-{kind}'
                self.seed_historical(kind, document, key=key)
                baseline = self.state()
                response = self.client.post(url, self.payload(kind, token=key, date=None))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.state(), baseline)

    def test_historical_same_key_from_another_actor_is_not_a_success_or_a_new_payment(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                key = f'synthetic-other-actor-{kind}'
                self.seed_historical(kind, document, key=key, actor=self.other)
                baseline = self.state()
                response = self.client.post(url, self.payload(kind, token=key))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.state(), baseline)

    def test_legacy_global_key_cannot_be_reused_for_another_document_or_client(self):
        payment = self.seed_historical('sale', self.sale, key='synthetic-global-key')
        for kind, url, document in self.cases()[1:]:
            with self.subTest(kind=kind):
                baseline = self.state()
                response = self.client.post(url, self.payload(kind, token=payment.idempotency_key))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.state(), baseline)

    def test_registered_legacy_global_key_still_rejects_a_different_actor(self):
        kind, url, document = self.cases()[0]
        payload = self.payload(kind, token='synthetic-registered-legacy-actor')
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.client.force_login(self.other)
        baseline = self.state()
        self.assertEqual(self.client.post(url, payload).status_code, 409)
        self.assertEqual(self.state(), baseline)

    def test_legacy_receipt_with_explicit_date_is_safe_after_its_registry_entry_is_removed(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=f'synthetic-legacy-registry-removed-{kind}')
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                IdempotencyRecord.objects.filter(key=payload['request_token']).delete()
                payments_before = list(Payment.objects.order_by('pk').values())
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.get('Idempotency-Replayed'), 'true')
                self.assertEqual(list(Payment.objects.order_by('pk').values()), payments_before)
                self.assertEqual(document.payments.count(), 1)

    def test_historical_adoption_rejects_changed_immutable_fields_without_mutation(self):
        kind, url, document = self.cases()[0]
        key = 'synthetic-historical-fields'
        self.seed_historical(kind, document, key=key)
        original = self.payload(kind, token=key)
        changes = {
            'amount': '26.00', 'payment_type': Payment.TRANSFER,
            'created_at': '2026-11-12T10:31', 'external_reference': 'SYNTHETIC-CHANGED',
            'notes': 'Changed synthetic intent',
        }
        baseline = self.state()
        for field, value in changes.items():
            with self.subTest(field=field):
                response = self.client.post(url, {**original, field: value})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.state(), baseline)

    def test_historical_comparison_accepts_equivalent_decimal_and_trimmed_text_fields(self):
        kind, url, document = self.cases()[0]
        key = 'synthetic-historical-normalized'
        self.seed_historical(kind, document, key=key)
        payload = self.payload(kind, token=key, amount='25')
        payload['notes'] = '  Synthetic receipt only  '
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(document.payments.count(), 1)

    def test_historical_duplicate_dates_and_oversized_sale_ids_fail_closed(self):
        kind, url, document = self.cases()[2]
        key = 'synthetic-historical-ambiguous'
        self.seed_historical(kind, document, key=key)
        baseline = self.state()
        for change in [{'created_at': [DATE, DATE]}, {'sale': '9' * 5000}]:
            with self.subTest(field=next(iter(change))):
                response = self.client.post(url, {**self.payload(kind, token=key), **change})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.state(), baseline)

    def test_registered_keys_bind_every_changed_field_before_replay_access(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token())
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                baseline = self.state()
                for field, value in {
                    'amount': '26.00', 'payment_type': Payment.CHEQUE,
                    'created_at': '2026-11-12T10:31', 'notes': 'Another intent',
                    'external_reference': 'CHANGED', 'extra': ['first', 'last'],
                }.items():
                    with self.subTest(field=field):
                        response = self.client.post(url, {**payload, field: value})
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(self.state(), baseline)

    def test_legacy_registry_rejects_a_changed_body_even_when_the_old_receipt_would_match(self):
        kind, url, document = self.cases()[0]
        payload = self.payload(kind, token='synthetic-legacy-hash')
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        baseline = self.state()
        self.assertEqual(self.client.post(url, {**payload, 'amount': '25'}).status_code, 400)
        self.assertEqual(self.state(), baseline)

    def test_same_signed_token_is_independent_for_another_actor_and_for_other_operations(self):
        token = new_web_token()
        kind, url, document = self.cases()[0]
        payload = self.payload(kind, token=token)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.client.force_login(self.other)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.get('Idempotency-Replayed'), 'false')
        self.assertEqual(document.payments.count(), 2)
        self.assertEqual(set(document.payments.values_list('created_by_id', flat=True)), {self.user.pk, self.other.pk})
        for kind, url, document in self.cases()[1:]:
            self.assertEqual(self.client.post(url, self.payload(kind, token=token)).status_code, 302)
            self.assertEqual(document.payments.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(key=token).count(), 4)

    def test_same_signed_token_is_scoped_to_the_document_id(self):
        token = new_web_token()
        payload = self.payload('sale', token=token)
        for document in (self.sale, self.receivable_sale):
            response = self.client.post(reverse('sale_payment_create', args=[document.pk]), payload)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.get('Idempotency-Replayed'), 'false')
            self.assertEqual(document.payments.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(key=token).count(), 2)

    def test_malformed_signatures_and_unbounded_legacy_tokens_never_downgrade(self):
        token = new_web_token()
        broken = token[:-1] + ('A' if token[-1] != 'A' else 'B')
        malformed = [broken, 'legacy:invalid-signature', 'x' * 65, 'bad token', 'bad/token', 'é' * 32]
        for kind, url, document in self.cases():
            for bad in malformed:
                with self.subTest(kind=kind, shape=len(bad)):
                    baseline = self.state()
                    self.assertEqual(self.client.post(url, self.payload(kind, token=bad)).status_code, 400)
                    self.assertEqual(self.state(), baseline)

    def test_duplicate_token_fields_are_rejected_for_signed_and_legacy_requests(self):
        for kind, url, document in self.cases():
            for tokens in [[new_web_token(), new_web_token()], ['same-legacy', 'same-legacy'], ['', new_web_token()]]:
                with self.subTest(kind=kind, tokens_count=len(tokens)):
                    baseline = self.state()
                    self.assertEqual(self.client.post(url, self.payload(kind, token=tokens)).status_code, 400)
                    self.assertEqual(self.state(), baseline)

    def test_expired_signed_tokens_cannot_create_replay_or_revive_purged_results(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                with patch('django.core.signing.time.time', return_value=1_700_000_000):
                    token = new_web_token()
                    payload = self.payload(kind, token=token)
                    self.assertEqual(self.client.post(url, payload).status_code, 302)
                baseline = self.state()
                with patch('django.core.signing.time.time', return_value=1_700_000_000 + WEB_TOKEN_MAX_AGE + 1):
                    self.assertEqual(self.client.post(url, payload).status_code, 400)
                self.assertEqual(self.state(), baseline)
                IdempotencyRecord.objects.filter(key=token).delete()
                baseline = self.state()
                with patch('django.core.signing.time.time', return_value=1_700_000_000 + WEB_TOKEN_MAX_AGE + 1):
                    self.assertEqual(self.client.post(url, payload).status_code, 400)
                self.assertEqual(self.state(), baseline)

    def test_all_payment_routes_keep_csrf_protection(self):
        client = WebClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        for kind, url, document in self.cases():
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            payload = self.payload(kind, token=response.context['form']['request_token'].value())
            baseline = self.state()
            self.assertEqual(client.post(url, payload).status_code, 403)
            self.assertEqual(self.state(), baseline)
            payload['csrfmiddlewaretoken'] = client.cookies['csrftoken'].value
            self.assertEqual(client.post(url, payload).status_code, 302)

    def test_revoked_route_permissions_are_rechecked_before_cached_redirects(self):
        permissions = {
            'sale': 'commerce.change_sale', 'purchase': 'commerce.change_purchase',
            'receivable': 'commerce.add_customerpayment',
        }
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token())
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                self.deny(permissions[kind])
                baseline = self.state()
                self.assertEqual(self.client.post(url, payload).status_code, 403)
                self.assertEqual(self.state(), baseline)

    def test_sale_and_receivable_replays_recheck_current_operator_visibility(self):
        requests = []
        for kind, url, document in self.cases():
            if kind != 'purchase':
                payload = self.payload(kind, token=new_web_token())
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                requests.append((url, payload))
        LoadingOrder.objects.create(number='SYNTHETIC-RECEIPT-SCOPE', operator=self.user, created_by=self.other)
        self.deny('inventory.view_all_loadingorders')
        baseline = self.state()
        for url, payload in requests:
            self.assertEqual(self.client.post(url, payload).status_code, 404)
            self.assertEqual(self.state(), baseline)

    def test_missing_payment_targets_fail_without_recreating_them(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token())
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                IdempotencyRecord.objects.filter(key=payload['request_token']).update(resource_id='999999999')
                baseline = self.state()
                self.assertEqual(self.client.post(url, payload).status_code, 404)
                self.assertEqual(self.state(), baseline)

    def test_cancelled_registered_payments_are_not_recreated(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token())
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                document.payments.get().cancel(user=self.user, reason='Synthetic cancellation')
                baseline = self.state()
                self.assertEqual(self.client.post(url, payload).get('Idempotency-Replayed'), 'true')
                self.assertEqual(self.state(), baseline)

    def test_cancelled_historical_payments_are_adopted_with_an_explicit_message(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                key = f'synthetic-cancelled-{kind}'
                payment = self.seed_historical(kind, document, key=key)
                payment.cancel(user=self.user, reason='Synthetic cancellation')
                response = self.client.post(url, self.payload(kind, token=key))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.get('Idempotency-Replayed'), 'true')
                messages = ' '.join(str(message) for message in get_messages(response.wsgi_request))
                self.assertIn('annulé', messages)
                self.assertEqual(document.payments.count(), 1)

    def test_receivable_target_client_must_still_match_the_current_invoice(self):
        kind, url, document = self.cases()[2]
        payload = self.payload(kind, token=new_web_token())
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        Sale.objects.filter(pk=document.pk).update(client=self.customer)
        baseline = self.state()
        self.assertEqual(self.client.post(url, payload).status_code, 404)
        self.assertEqual(self.state(), baseline)

    def test_incomplete_registry_entries_return_conflict_without_a_payment_write(self):
        kind, url, document = self.cases()[0]
        payload = self.payload(kind, token=new_web_token())
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        IdempotencyRecord.objects.filter(key=payload['request_token']).update(
            completed=False, completed_at=None, response_status=None, response_body=None,
        )
        baseline = self.state()
        self.assertEqual(self.client.post(url, payload).status_code, 409)
        self.assertEqual(self.state(), baseline)

    def test_direct_payment_failure_after_save_rolls_back_and_the_same_key_can_retry(self):
        real_save = Payment.save

        def fail_after_save(payment, *args, **kwargs):
            real_save(payment, *args, **kwargs)
            raise ValidationError('Synthetic failure after payment insertion')

        for kind, url, document in self.cases()[:2]:
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token())
                baseline = self.state()
                with patch.object(Payment, 'save', new=fail_after_save):
                    response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['form']['request_token'].value(), payload['request_token'])
                self.assertEqual(self.state(), baseline)
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                self.assertEqual(document.payments.count(), 1)

    def test_receivable_business_failure_after_save_propagates_and_rolls_back_without_consuming_key(self):
        kind, url, document = self.cases()[2]
        payload = self.payload(kind, token=new_web_token())
        baseline = self.state()
        real_save = Payment.save

        def fail_after_save(payment, *args, **kwargs):
            real_save(payment, *args, **kwargs)
            raise ValidationError('Synthetic receivable failure after insertion')

        with patch.object(Payment, 'save', new=fail_after_save):
            with self.assertRaisesMessage(ValidationError, 'Synthetic receivable failure after insertion'):
                self.client.post(url, payload)
        self.assertEqual(self.state(), baseline)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(document.payments.count(), 1)

    def test_only_exact_legacy_key_integrity_or_unique_validation_collisions_are_adopted(self):
        kind, url, document = self.cases()[0]
        key = 'synthetic-collision-race'
        self.seed_historical(kind, document, key=key)
        payload = self.payload(kind, token=key)
        real_replay = commerce_views._legacy_payment_replay
        exceptions = [
            IntegrityError('UNIQUE constraint failed: commerce_payment.idempotency_key'),
            ValidationError({'idempotency_key': ValidationError('Synthetic duplicate', code='unique')}),
        ]
        for exc in exceptions:
            with self.subTest(exception=type(exc).__name__):
                IdempotencyRecord.objects.filter(key=key).delete()
                calls = []

                def skip_initial_lookup(*args, **kwargs):
                    calls.append(True)
                    return None if len(calls) == 1 else real_replay(*args, **kwargs)

                with patch('apps.commerce.views._legacy_payment_replay', side_effect=skip_initial_lookup):
                    with patch.object(Payment, 'save', side_effect=exc):
                        response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.get('Idempotency-Replayed'), 'true')
                self.assertEqual(len(calls), 2)
                self.assertEqual(document.payments.count(), 1)

    def test_unrelated_integrity_errors_are_not_swallowed_as_legacy_replays(self):
        kind, url, document = self.cases()[0]
        payload = self.payload(kind, token='synthetic-other-constraint')
        baseline = self.state()
        with patch.object(Payment, 'save', side_effect=IntegrityError('Synthetic unrelated reference constraint')):
            with self.assertRaisesMessage(IntegrityError, 'Synthetic unrelated reference constraint'):
                self.client.post(url, payload)
        self.assertEqual(self.state(), baseline)


class PostgreSQLWebPaymentConcurrencyTests(PaymentFixtures, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real payment replay arbitration requires PostgreSQL.')
        self.make_fixtures()

    def test_concurrent_full_payments_use_distinct_connections_and_one_receipt_per_route(self):
        for kind, url, document in self.cases():
            with self.subTest(kind=kind):
                payload = self.payload(kind, token=new_web_token(), amount=str(document.total))
                barrier = Barrier(2)
                audit_before = AuditLog.objects.count()

                def submit(_):
                    close_old_connections()
                    try:
                        client = WebClient()
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
                self.assertEqual(results[0][2], results[1][2])
                self.assertEqual(sorted(result[3] for result in results), ['false', 'true'])
                self.assertEqual(document.payments.count(), 1)
                payment = document.payments.get()
                self.assertEqual(payment.amount, document.total)
                self.assertEqual(payment.created_by, self.user)
                self.assertIsNone(payment.idempotency_key)
                record = IdempotencyRecord.objects.get(key=payload['request_token'])
                self.assertTrue(record.completed)
                self.assertEqual(record.resource_type, 'payment')
                self.assertEqual(record.resource_id, str(payment.pk))
                # These routes have no mutation-audit mapping; do not invent one
                # or claim that a pre-existing payment audit was de-duplicated.
                self.assertEqual(AuditLog.objects.count(), audit_before)
