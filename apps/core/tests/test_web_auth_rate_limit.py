"""Synthetic Web/admin admissions; real concurrency is PostgreSQL-only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.core.cache import cache
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog, AuthRateLimitBucket
from apps.core.security import ADMIN_LOGIN_FAILURE_EVENT, LOGIN_FAILURE_EVENT
from apps.core.web_auth_rate_limit import (
    WebAuthenticationUnavailable, admit_web_authentication,
)


_SETTINGS = {
    'PASSWORD_HASHERS': ['django.contrib.auth.hashers.MD5PasswordHasher'],
    'SECRET_KEY': 'web-auth-test-only-key-not-a-live-secret-abcdefghijklmnopqrstuvwxyz',
    'SECURE_SSL_REDIRECT': False,
    'TRUST_X_FORWARDED_FOR': False,
    'LOGIN_FAILURE_LIMIT': 2,
    'LOGIN_FAILURE_IP_LIMIT': 20,
    'LOGIN_FAILURE_WINDOW_SECONDS': 900,
    'PASSWORD_RESET_LIMIT': 2,
    'PASSWORD_RESET_IP_LIMIT': 10,
    'PASSWORD_RESET_WINDOW_SECONDS': 3600,
}


@override_settings(**_SETTINGS)
class WebAuthenticationAdmissionTests(TestCase):
    password = 'Synthetic-web-auth-password-2040!'

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='web-limited-user', password=cls.password,
        )
        cls.admin = get_user_model().objects.create_superuser(
            username='web-limited-admin', password=cls.password,
        )

    def submit(self, *, username=None, password='wrong', ip='192.0.2.10', admin=False, client=None, **headers):
        return (client or self.client).post(
            reverse('admin:login' if admin else 'login'),
            {'username': username or self.user.username, 'password': password},
            REMOTE_ADDR=ip, **headers,
        )

    def test_web_and_admin_account_budgets_are_global_across_ips(self):
        for admin in (False, True):
            with self.subTest(admin=admin):
                for ip in ('192.0.2.11', '192.0.2.12'):
                    self.assertEqual(self.submit(admin=admin, ip=ip).status_code, 200)
                with patch('django.contrib.auth.forms.authenticate') as authenticate:
                    blocked = self.submit(admin=admin, ip='192.0.2.13', password=self.password)
                self.assertEqual(blocked.status_code, 429)
                self.assertGreater(int(blocked['Retry-After']), 0)
                authenticate.assert_not_called()
                self.assertNotIn(SESSION_KEY, self.client.session)

    @override_settings(LOGIN_FAILURE_IP_LIMIT=2, LOGIN_FAILURE_LIMIT=100)
    def test_rotating_names_cannot_bypass_ip_admission_or_create_account_rows_after_denial(self):
        for username in ('unknown-a', 'unknown-b'):
            self.assertEqual(self.submit(username=username).status_code, 200)
        count = AuthRateLimitBucket.objects.count()
        with patch('django.contrib.auth.forms.authenticate') as authenticate:
            denied = self.submit(username='unknown-c')
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(AuthRateLimitBucket.objects.count(), count)
        authenticate.assert_not_called()

    def test_successful_logins_also_consume_the_preserved_admission_ceiling(self):
        for _ in range(2):
            self.assertEqual(self.submit(password=self.password, client=Client()).status_code, 302)
        with patch('django.contrib.auth.forms.authenticate') as authenticate:
            denied = self.submit(password=self.password, client=Client())
        self.assertEqual(denied.status_code, 429)
        authenticate.assert_not_called()

    def test_web_and_django_admin_use_independent_namespaces(self):
        for _ in range(2):
            self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.submit(admin=True).status_code, 200)

    def test_gets_do_not_consume_budgets(self):
        for name in ('login', 'admin:login', 'password_reset'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertFalse(AuthRateLimitBucket.objects.exists())

    def test_missing_or_invalid_ip_uses_shared_unknown_bucket_not_fail_open(self):
        with override_settings(LOGIN_FAILURE_IP_LIMIT=1):
            self.assertEqual(self.submit(username='unknown-a', ip='').status_code, 200)
            self.assertEqual(self.submit(username='unknown-b', ip='not-an-ip').status_code, 429)

    @override_settings(TRUST_X_FORWARDED_FOR=False, LOGIN_FAILURE_IP_LIMIT=1)
    def test_untrusted_forwarded_values_cannot_change_the_budget(self):
        self.assertEqual(self.submit(HTTP_X_FORWARDED_FOR='198.51.100.1').status_code, 200)
        self.assertEqual(self.submit(HTTP_X_FORWARDED_FOR='198.51.100.2').status_code, 429)

    @override_settings(TRUST_X_FORWARDED_FOR=True, LOGIN_FAILURE_IP_LIMIT=1)
    def test_only_proxy_appended_rightmost_ip_is_trusted(self):
        self.assertEqual(self.submit(HTTP_X_FORWARDED_FOR='198.51.100.1, 203.0.113.9').status_code, 200)
        self.assertEqual(self.submit(HTTP_X_FORWARDED_FOR='198.51.100.2, 203.0.113.9').status_code, 429)

    def test_audit_rows_and_cache_are_not_an_admission_store(self):
        for event in (LOGIN_FAILURE_EVENT, ADMIN_LOGIN_FAILURE_EVENT):
            for _ in range(5):
                AuditLog.objects.create(
                    action=f'{event} identifier={self.user.username}', ip_address='192.0.2.10',
                )
        request = RequestFactory().post('/', {'username': self.user.username}, REMOTE_ADDR='192.0.2.10')
        with patch.object(cache, 'get', side_effect=AssertionError('No cache fallback')), \
                patch.object(cache, 'set', side_effect=AssertionError('No cache fallback')):
            self.assertTrue(admit_web_authentication(request, 'login').allowed)
        cache.clear()
        self.assertTrue(admit_web_authentication(request, 'login').allowed)
        self.assertFalse(admit_web_authentication(request, 'login').allowed)

    def test_database_or_pruning_failure_is_503_before_any_password_check(self):
        for admin in (False, True):
            for target in ('consume_limit', 'prune_expired_buckets'):
                with self.subTest(admin=admin, target=target), \
                        patch(f'apps.core.web_auth_rate_limit.{target}', side_effect=DatabaseError('private-provider-detail')), \
                        patch('django.contrib.auth.forms.authenticate') as authenticate:
                    response = self.submit(password=self.password, admin=admin)
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn('private-provider-detail', response.content.decode())
                    self.assertNotIn(self.password, response.content.decode())
                    self.assertNotIn('Retry-After', response)
                    authenticate.assert_not_called()

    def test_invalid_or_disabled_configuration_fails_closed_without_admission(self):
        for name, value in (
            ('LOGIN_FAILURE_LIMIT', 0), ('LOGIN_FAILURE_IP_LIMIT', -1),
            ('LOGIN_FAILURE_WINDOW_SECONDS', '900'), ('LOGIN_FAILURE_LIMIT', True),
            ('PASSWORD_RESET_WINDOW_SECONDS', 31_536_001),
        ):
            with self.subTest(name=name, value=value), override_settings(**{name: value}):
                operation = 'password_reset_request' if name.startswith('PASSWORD_RESET') else 'login'
                request = RequestFactory().post('/', {'username': self.user.username, 'email': 'synthetic@example.invalid'})
                with patch('apps.core.web_auth_rate_limit.consume_limit') as consume:
                    with self.assertRaises(WebAuthenticationUnavailable):
                        admit_web_authentication(request, operation)
                consume.assert_not_called()

    def test_ip_denial_happens_before_parsing_untrusted_post_identity(self):
        class UnparsedRequest:
            method = 'POST'
            META = {'REMOTE_ADDR': '192.0.2.10'}

            @property
            def POST(self):
                raise AssertionError('IP denied requests must not parse POST')

        self.submit()
        with override_settings(LOGIN_FAILURE_IP_LIMIT=1):
            self.assertFalse(admit_web_authentication(UnparsedRequest(), 'login').allowed)

    def test_username_normalization_matches_form_and_does_not_truncate_to_audit_length(self):
        for username in ('web-limited-user', '  web-limited-user  '):
            self.assertEqual(self.submit(username=username).status_code, 200)
        self.assertEqual(self.submit(username='ｗｅｂ-limited-user').status_code, 429)
        request = RequestFactory().post('/', {'username': 'a' * 90 + 'x'})
        other = RequestFactory().post('/', {'username': 'a' * 90 + 'y'})
        self.assertTrue(admit_web_authentication(request, 'login').allowed)
        self.assertTrue(admit_web_authentication(other, 'login').allowed)

    def test_hashed_storage_contains_no_username_ip_or_password(self):
        self.submit(password=self.password)
        buckets = list(AuthRateLimitBucket.objects.values('key', 'hits', 'expires_at'))
        self.assertEqual(len(buckets), 2)
        for bucket in buckets:
            self.assertEqual(len(bucket['key']), 64)
            self.assertNotIn(self.user.username, str(bucket))
            self.assertNotIn('192.0.2.10', str(bucket))
            self.assertNotIn(self.password, str(bucket))

    def test_expiry_renews_admission_and_rejections_do_not_extend_it(self):
        self.submit()
        self.submit()
        before = list(AuthRateLimitBucket.objects.order_by('key').values('key', 'expires_at'))
        blocked = self.submit()
        self.assertEqual(blocked.status_code, 429)
        # The IP quota still admits this attempt; the refused account budget is
        # not extended. Neither budget's fixed-window expiry is extended.
        self.assertEqual(list(AuthRateLimitBucket.objects.order_by('key').values('key', 'expires_at')), before)
        AuthRateLimitBucket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.submit().status_code, 200)

    def test_valid_csrf_then_quota_refusal_remains_429_without_authentication(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(reverse('login')).status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.submit()
        self.submit()
        with patch('django.contrib.auth.forms.authenticate') as authenticate:
            denied = client.post(reverse('login'), {
                'username': self.user.username, 'password': self.password,
                'csrfmiddlewaretoken': token,
            })
        self.assertEqual(denied.status_code, 429)
        authenticate.assert_not_called()

    def test_csrf_rejection_stays_403_and_never_reserves_admission(self):
        for name in ('login', 'admin:login', 'password_reset'):
            with self.subTest(name=name), \
                    patch('apps.core.web_auth_rate_limit.consume_limit') as consume, \
                    patch('django.contrib.auth.forms.authenticate') as authenticate:
                response = Client(enforce_csrf_checks=True).post(reverse(name), {
                    'username': self.admin.username, 'password': self.password,
                    'email': 'synthetic@example.invalid',
                })
                self.assertEqual(response.status_code, 403)
                authenticate.assert_not_called()
                consume.assert_not_called()


@skipUnless(connection.vendor == 'postgresql', 'Real admission concurrency requires PostgreSQL.')
@override_settings(**{**_SETTINGS, 'LOGIN_FAILURE_LIMIT': 3, 'LOGIN_FAILURE_IP_LIMIT': 20})
class PostgreSQLWebAdmissionConcurrencyTests(TransactionTestCase):
    def test_distinct_connections_and_ips_reserve_exact_global_account_quota(self):
        barrier = Barrier(8)

        def worker(index):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                request = RequestFactory().post(
                    '/', {'username': 'synthetic-shared-account'}, REMOTE_ADDR=f'192.0.2.{index + 1}',
                )
                barrier.wait(timeout=30)
                admission = admit_web_authentication(request, 'login')
                return backend_pid, admission.allowed
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, index) for index in range(8)]
            results = [future.result(timeout=60) for future in futures]
        self.assertEqual(len({pid for pid, _allowed in results}), 8)
        self.assertEqual(sum(allowed for _pid, allowed in results), 3)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 9)
        self.assertEqual(sorted(AuthRateLimitBucket.objects.values_list('hits', flat=True)), [1] * 8 + [3])
