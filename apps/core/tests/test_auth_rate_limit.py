"""Synthetic rate-limit tests; no network, credentials or ERP data are required."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, OperationalError, close_old_connections, connection, connections
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.core.auth_rate_limit import consume_limit, prune_expired_buckets
from apps.core.models import AuthRateLimitBucket


@override_settings(SECRET_KEY='sec05-test-only-key-never-a-live-credential')
class AuthRateLimitServiceTests(TestCase):
    now = datetime(2026, 11, 12, 12, 0, tzinfo=datetime_timezone.utc)

    def consume(self, **kwargs):
        parameters = {
            'scope': 'unit.login',
            'dimension': 'account',
            'identifier': 'synthetic-account@example.invalid',
            'limit': 2,
            'window_seconds': 60,
        }
        parameters.update(kwargs)
        return consume_limit(**parameters)

    def test_quota_is_exact_and_blocking_does_not_change_counter_or_expiry(self):
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now):
            first = self.consume()
            second = self.consume()
            before = AuthRateLimitBucket.objects.values('hits', 'expires_at').get()
            rejected = [self.consume() for _ in range(4)]

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertTrue(all(not admission.allowed for admission in rejected))
        self.assertEqual(before['hits'], 2)
        self.assertEqual(
            AuthRateLimitBucket.objects.values('hits', 'expires_at').get(), before,
        )
        self.assertEqual(rejected[0].retry_after, 60)

    def test_later_accepted_attempt_does_not_extend_fixed_window(self):
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now):
            self.consume()
        expiry = AuthRateLimitBucket.objects.get().expires_at
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now + timedelta(seconds=20)):
            self.assertTrue(self.consume().allowed)

        self.assertEqual(AuthRateLimitBucket.objects.get().expires_at, expiry)

    def test_retry_after_rounds_up_fractional_seconds(self):
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now):
            self.consume(limit=1)
        with patch(
            'apps.core.auth_rate_limit.timezone.now',
            return_value=self.now + timedelta(seconds=58, microseconds=500000),
        ):
            rejected = self.consume(limit=1)

        self.assertFalse(rejected.allowed)
        self.assertEqual(rejected.retry_after, 2)

    def test_exact_expiry_resets_counter_and_starts_a_new_window(self):
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now):
            self.consume(limit=1)
        next_window = self.now + timedelta(seconds=60)
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=next_window):
            admitted = self.consume(limit=1)
            rejected = self.consume(limit=1)

        self.assertTrue(admitted.allowed)
        self.assertFalse(rejected.allowed)
        bucket = AuthRateLimitBucket.objects.get()
        self.assertEqual(bucket.hits, 1)
        self.assertEqual(bucket.expires_at, next_window + timedelta(seconds=60))

    def test_keys_are_stable_but_scope_dimension_and_identity_are_separated(self):
        self.consume()
        original_key = AuthRateLimitBucket.objects.get().pk
        self.consume()
        self.assertEqual(AuthRateLimitBucket.objects.count(), 1)
        self.assertEqual(AuthRateLimitBucket.objects.get().pk, original_key)

        self.consume(scope='unit.refresh')
        self.consume(dimension='ip')
        self.consume(identifier='another-synthetic-account@example.invalid')
        self.assertEqual(AuthRateLimitBucket.objects.count(), 4)

    def test_bucket_identifier_is_keyed_and_does_not_store_raw_identity(self):
        identifier = 'private-synthetic-refresh-token-with-no-valid-signature'
        self.consume(identifier=identifier)
        bucket = AuthRateLimitBucket.objects.get()
        key = bucket.pk
        self.assertRegex(key, r'^[0-9a-f]{64}$')
        self.assertNotEqual(key, hashlib.sha256(identifier.encode()).hexdigest())
        self.assertNotIn(identifier, str(bucket))
        self.assertNotIn(identifier, repr(AuthRateLimitBucket.objects.values().get()))

        with override_settings(SECRET_KEY='sec05-another-synthetic-key-not-a-live-secret'):
            self.consume(identifier=identifier)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 2)

    def test_key_framing_prevents_component_separator_collisions(self):
        self.consume(scope='unit:a', dimension='b', identifier='c')
        self.consume(scope='unit', dimension='a:b', identifier='c')
        self.consume(scope='unit', dimension='a', identifier='b:c')
        self.assertEqual(AuthRateLimitBucket.objects.count(), 3)

    def test_invalid_limits_do_not_disable_protection(self):
        for parameters in (
            {'limit': 0}, {'limit': -1}, {'window_seconds': 0}, {'window_seconds': -1},
        ):
            with self.subTest(parameters=parameters):
                with self.assertRaises((ValueError, ImproperlyConfigured)):
                    self.consume(**parameters)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 0)

    def test_database_read_failure_is_propagated_instead_of_admitting(self):
        with patch.object(QuerySet, 'get_or_create', side_effect=OperationalError('synthetic failure')):
            with self.assertRaises(DatabaseError):
                self.consume()
        self.assertEqual(AuthRateLimitBucket.objects.count(), 0)

    def test_database_update_failure_rolls_back_new_bucket(self):
        with patch.object(QuerySet, 'update', side_effect=OperationalError('synthetic failure')):
            with self.assertRaises(DatabaseError):
                self.consume()
        self.assertEqual(AuthRateLimitBucket.objects.count(), 0)

    def test_pruning_is_bounded_and_never_deletes_active_buckets(self):
        expired = self.now - timedelta(seconds=1)
        active = self.now + timedelta(seconds=60)
        for index in range(3):
            AuthRateLimitBucket.objects.create(key=f'{index:064x}', hits=1, expires_at=expired)
        active_bucket = AuthRateLimitBucket.objects.create(key='f' * 64, hits=1, expires_at=active)

        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now):
            self.assertEqual(prune_expired_buckets(limit=2), 2)
            self.assertEqual(AuthRateLimitBucket.objects.filter(expires_at=expired).count(), 1)
            self.assertTrue(AuthRateLimitBucket.objects.filter(pk=active_bucket.pk).exists())
            self.assertEqual(prune_expired_buckets(limit=2), 1)
            self.assertEqual(prune_expired_buckets(limit=2), 0)
        self.assertEqual(AuthRateLimitBucket.objects.get().pk, active_bucket.pk)

    def test_pruning_rechecks_expiry_when_deleting_selected_keys(self):
        bucket = AuthRateLimitBucket.objects.create(
            key='a' * 64, hits=1, expires_at=self.now - timedelta(seconds=1),
        )
        new_expiry = self.now + timedelta(seconds=60)
        original_delete = QuerySet.delete

        def refresh_then_delete(queryset):
            # Simulate a selected row becoming active before the final DELETE.
            AuthRateLimitBucket.objects.filter(pk=bucket.pk).update(expires_at=new_expiry)
            return original_delete(queryset)

        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now), \
                patch.object(QuerySet, 'delete', autospec=True, side_effect=refresh_then_delete):
            self.assertEqual(prune_expired_buckets(limit=1), 0)

        self.assertEqual(AuthRateLimitBucket.objects.get(pk=bucket.pk).expires_at, new_expiry)

    def test_pruning_database_failure_is_not_silently_ignored(self):
        AuthRateLimitBucket.objects.create(
            key='a' * 64, hits=1, expires_at=self.now - timedelta(seconds=1),
        )
        with patch('apps.core.auth_rate_limit.timezone.now', return_value=self.now), \
                patch.object(QuerySet, 'delete', side_effect=OperationalError('synthetic failure')):
            with self.assertRaises(DatabaseError):
                prune_expired_buckets(limit=1)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 1)


@skipUnless(connection.vendor == 'postgresql', 'Les verrous concurrents exigent PostgreSQL.')
@override_settings(SECRET_KEY='sec05-postgresql-test-only-not-a-live-credential')
class PostgreSQLAuthRateLimitConcurrencyTests(TransactionTestCase):
    """Separate DB connections, not a claim of a real Gunicorn/process test."""

    workers = 12

    @staticmethod
    def consume(**kwargs):
        parameters = {
            'scope': 'concurrency.login',
            'dimension': 'account',
            'identifier': 'synthetic-shared-account',
            'limit': 4,
            'window_seconds': 60,
        }
        parameters.update(kwargs)
        return consume_limit(**parameters)

    def run_concurrently(self, operation):
        barrier = Barrier(self.workers)

        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=30)
                return operation(index)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(worker, index) for index in range(self.workers)]
            return [future.result(timeout=60) for future in futures]

    def test_simultaneous_first_creation_admits_exact_quota(self):
        results = self.run_concurrently(lambda _: self.consume().allowed)
        self.assertEqual(sum(results), 4)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 1)
        self.assertEqual(AuthRateLimitBucket.objects.get().hits, 4)

    def test_simultaneous_attempts_near_quota_do_not_overshoot(self):
        for _ in range(3):
            self.assertTrue(self.consume().allowed)
        results = self.run_concurrently(lambda _: self.consume().allowed)
        self.assertEqual(sum(results), 1)
        self.assertEqual(AuthRateLimitBucket.objects.get().hits, 4)

    def test_simultaneous_expiry_resets_only_once(self):
        self.consume()
        AuthRateLimitBucket.objects.update(
            hits=4, expires_at=timezone.now() - timedelta(seconds=1),
        )
        results = self.run_concurrently(lambda _: self.consume().allowed)
        self.assertEqual(sum(results), 4)
        bucket = AuthRateLimitBucket.objects.get()
        self.assertEqual(bucket.hits, 4)
        self.assertGreater(bucket.expires_at, timezone.now())

    def test_shared_account_with_different_ip_buckets_has_global_quota(self):
        def attempt(index):
            ip_result = self.consume(dimension='ip', identifier=f'synthetic-ip-{index % 4}', limit=3)
            if not ip_result.allowed:
                return False
            return self.consume(limit=5).allowed

        results = self.run_concurrently(attempt)
        self.assertEqual(sum(results), 5)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 5)
        self.assertEqual(sorted(AuthRateLimitBucket.objects.values_list('hits', flat=True)), [3, 3, 3, 3, 5])

    def test_shared_ip_with_different_accounts_preserves_ip_quota(self):
        def attempt(index):
            ip_result = self.consume(dimension='ip', identifier='shared-synthetic-ip', limit=4)
            if not ip_result.allowed:
                return False
            return self.consume(identifier=f'synthetic-account-{index}', limit=2).allowed

        results = self.run_concurrently(attempt)
        self.assertEqual(sum(results), 4)
        # Exhausted-IP requests must not create attacker-controlled account rows.
        self.assertEqual(AuthRateLimitBucket.objects.count(), 5)
        self.assertEqual(sorted(AuthRateLimitBucket.objects.values_list('hits', flat=True)), [1, 1, 1, 1, 4])
