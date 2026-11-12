"""Shared authentication limits, using only synthetic accounts and test storage."""

from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.db import DatabaseError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.api.authentication import MobileTokenSerializer
from apps.core.models import AuditLog, AuthRateLimitBucket


@override_settings(
    SECRET_KEY='shared-auth-tests-only-abcdefghijklmnopqrstuvwxyz-1234567890',
    SECURE_SSL_REDIRECT=False,
    TRUST_X_FORWARDED_FOR=False,
    PASSWORD_RESET_EMAIL_PROVIDER='django',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_RESET_PUBLIC_BASE_URL='https://erp.example.test',
    PASSWORD_RESET_LIMIT=2,
    PASSWORD_RESET_IP_LIMIT=100,
    PASSWORD_RESET_WINDOW_SECONDS=3600,
    REST_FRAMEWORK={
        **settings.REST_FRAMEWORK,
        # Keep the authentication views' explicit shared throttle enabled.
        # Generic business-endpoint throttles are outside this test contract.
        'DEFAULT_THROTTLE_CLASSES': (),
        'DEFAULT_THROTTLE_RATES': {
            **settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'],
            'auth': '2/minute',
            'password_reset': '10/hour',
        },
    },
)
class SharedAuthenticationThrottleTests(APITestCase):
    password = 'Synthetic-password-2040!'
    public_routes = (
        'api-login',
        'api-refresh',
        'api-password-reset-request',
        'api-password-reset-confirm',
    )

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='limited-api-user', email='limited@example.invalid',
            password=cls.password,
        )

    @staticmethod
    def rates(**values):
        return override_settings(REST_FRAMEWORK={
            **settings.REST_FRAMEWORK,
            'DEFAULT_THROTTLE_RATES': {
                **settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], **values,
            },
        })

    def login(self, *, username=None, password='wrong', ip='192.0.2.10', **headers):
        return self.client.post(
            reverse('api-login'),
            {'username': username or self.user.username, 'password': password},
            format='json', REMOTE_ADDR=ip, **headers,
        )

    def reset_request(self, email, *, ip='192.0.2.10', **headers):
        return self.client.post(
            reverse('api-password-reset-request'), {'email': email},
            format='json', REMOTE_ADDR=ip, **headers,
        )

    def assert_throttled(self, response):
        self.assertEqual(response.status_code, 429, response.data)
        self.assertFalse(response.json()['success'])
        self.assertEqual(response.json()['error']['code'], 'RATE_LIMITED')
        self.assertGreater(int(response['Retry-After']), 0)

    def assert_unavailable(self, response):
        self.assertEqual(response.status_code, 503, response.data)
        payload = response.json()
        self.assertFalse(payload['success'])
        self.assertEqual(payload['error']['code'], 'AUTH_RATE_LIMIT_UNAVAILABLE')
        self.assertNotIn('synthetic database failure', str(payload))

    def test_login_account_budget_is_shared_across_client_ips(self):
        for ip in ('192.0.2.11', '192.0.2.12'):
            response = self.login(ip=ip)
            self.assertEqual(response.status_code, 401, response.data)
        with patch('apps.api.authentication.MobileTokenSerializer.validate') as validate:
            blocked = self.login(ip='192.0.2.13', password=self.password)
        self.assert_throttled(blocked)
        validate.assert_not_called()

    def test_existing_and_unknown_accounts_have_the_same_attempt_budget(self):
        for account_index, username in enumerate((self.user.username, 'no-such-api-user')):
            statuses = [
                self.login(username=username, ip=f'198.51.100.{10 * account_index + attempt}').status_code
                for attempt in range(1, 4)
            ]
            self.assertEqual(statuses, [401, 401, 429], username)

    def test_successful_login_attempts_also_consume_the_account_budget(self):
        for ip in ('192.0.2.11', '192.0.2.12'):
            response = self.login(ip=ip, password=self.password)
            self.assertEqual(response.status_code, 200, response.data)
        self.assert_throttled(self.login(ip='192.0.2.13', password=self.password))

    def test_login_identifiers_apply_nfkc_and_strip_without_case_folding(self):
        self.assertEqual(self.login(username='  limited-api-user  ', ip='192.0.2.11').status_code, 401)
        self.assertEqual(self.login(username='ｌｉｍｉｔｅｄ-api-user', ip='192.0.2.12').status_code, 401)
        self.assert_throttled(self.login(ip='192.0.2.13'))

        # Django usernames remain case-sensitive. Do not lock a different
        # account by lowercasing every attacker-controlled username.
        for attempt in (1, 2):
            response = self.login(username='LIMITED-api-user', ip=f'203.0.113.{attempt}')
            self.assertEqual(response.status_code, 401, response.data)
        self.assert_throttled(self.login(username='LIMITED-api-user', ip='203.0.113.3'))

    def test_numeric_login_usernames_share_the_equivalent_string_account_budget(self):
        for number, username in enumerate((123, '123'), start=1):
            response = self.login(username=username, ip=f'192.0.2.{number}')
            self.assertEqual(response.status_code, 401, response.data)
        self.assert_throttled(self.login(username='123', ip='192.0.2.3'))

        for number, username in enumerate((12.5, '12.5'), start=11):
            response = self.login(username=username, ip=f'192.0.2.{number}')
            self.assertEqual(response.status_code, 401, response.data)
        self.assert_throttled(self.login(username='12.5', ip='192.0.2.13'))

    def test_ip_budget_blocks_rotating_usernames_before_creating_more_accounts(self):
        for username in ('unknown-first', 'unknown-second'):
            self.assertEqual(self.login(username=username).status_code, 401)
        count_before = AuthRateLimitBucket.objects.count()
        for number in range(5):
            self.assert_throttled(self.login(username=f'new-blocked-name-{number}'))
        self.assertEqual(AuthRateLimitBucket.objects.count(), count_before)

    def test_malformed_json_still_consumes_ip_budget_before_parsing(self):
        with self.rates(auth='1/minute'):
            first = self.client.post(
                reverse('api-login'), data='{', content_type='application/json',
                REMOTE_ADDR='192.0.2.10',
            )
            second = self.client.post(
                reverse('api-login'), data='{', content_type='application/json',
                REMOTE_ADDR='192.0.2.10',
            )
        self.assertEqual(first.status_code, 400, first.data)
        self.assert_throttled(second)

    def test_surrogate_json_identifiers_are_rejected_after_consuming_ip_budget(self):
        # Send ASCII JSON escapes directly: rendering a Python surrogate value
        # through APIClient's JSON renderer would fail in the test client, not
        # exercise the server-side parser/account gate.
        cases = (
            ('api-refresh', r'{"refresh":"\ud800"}'),
            ('api-password-reset-confirm', (
                r'{"uid":"\ud800","token":"invalid-token",'
                r'"new_password":"Synthetic-new-password-2041!",'
                r'"new_password_confirm":"Synthetic-new-password-2041!"}'
            )),
        )
        with self.rates(auth='1/minute', password_reset='1/hour'):
            for route, body in cases:
                with self.subTest(route=route):
                    first = self.client.post(
                        reverse(route), data=body, content_type='application/json',
                        REMOTE_ADDR='192.0.2.10',
                    )
                    self.assertEqual(first.status_code, 400, first.data)
                    second = self.client.post(
                        reverse(route), data=body, content_type='application/json',
                        REMOTE_ADDR='192.0.2.10',
                    )
                    self.assert_throttled(second)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 2)

    def test_shared_buckets_do_not_depend_on_process_local_cache_contents(self):
        from django.core.cache import cache

        self.assertEqual(self.login(ip='192.0.2.11').status_code, 401)
        cache.clear()
        self.assertEqual(self.login(ip='192.0.2.12').status_code, 401)
        cache.clear()
        self.assert_throttled(self.login(ip='192.0.2.13'))

    def test_refresh_account_budget_is_shared_across_client_ips(self):
        for ip in ('192.0.2.11', '192.0.2.12'):
            response = self.client.post(
                reverse('api-refresh'),
                {'refresh': str(MobileTokenSerializer.get_token(self.user))},
                format='json', REMOTE_ADDR=ip,
            )
            self.assertEqual(response.status_code, 200, response.data)
        refresh = str(MobileTokenSerializer.get_token(self.user))
        with patch('apps.api.authentication.MobileTokenRefreshSerializer.validate') as validate:
            blocked = self.client.post(
                reverse('api-refresh'), {'refresh': refresh},
                format='json', REMOTE_ADDR='192.0.2.13',
            )
        self.assert_throttled(blocked)
        validate.assert_not_called()

    def test_refresh_whitespace_cannot_bypass_the_shared_account_budget(self):
        for ip in ('192.0.2.11', '192.0.2.12'):
            token = str(MobileTokenSerializer.get_token(self.user))
            response = self.client.post(
                reverse('api-refresh'), {'refresh': f'  {token}\t '},
                format='json', REMOTE_ADDR=ip,
            )
            self.assertEqual(response.status_code, 200, response.data)
        token = str(MobileTokenSerializer.get_token(self.user))
        with patch('apps.api.authentication.MobileTokenRefreshSerializer.validate') as validate:
            blocked = self.client.post(
                reverse('api-refresh'), {'refresh': f'  {token}  '},
                format='json', REMOTE_ADDR='192.0.2.13',
            )
        self.assert_throttled(blocked)
        validate.assert_not_called()

    def test_invalid_refresh_tokens_consume_ip_but_do_not_create_account_buckets(self):
        for token in ('not-a-jwt', 'another-not-a-jwt'):
            response = self.client.post(
                reverse('api-refresh'), {'refresh': token}, format='json',
                REMOTE_ADDR='192.0.2.10',
            )
            self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 1)
        blocked = self.client.post(
            reverse('api-refresh'), {'refresh': 'third-invalid-token'},
            format='json', REMOTE_ADDR='192.0.2.10',
        )
        self.assert_throttled(blocked)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 1)

    def test_unverified_or_wrong_type_token_claims_cannot_lock_an_account(self):
        refresh = str(MobileTokenSerializer.get_token(self.user))
        header, payload, signature = refresh.split('.')
        tampered_signature = ('A' if signature[0] != 'A' else 'B') + signature[1:]
        wrong_signature = '.'.join((header, payload, tampered_signature))
        access = str(MobileTokenSerializer.get_token(self.user).access_token)
        for token, ip in ((wrong_signature, '192.0.2.11'), (access, '192.0.2.12')):
            response = self.client.post(
                reverse('api-refresh'), {'refresh': token}, format='json', REMOTE_ADDR=ip,
            )
            self.assertEqual(response.status_code, 401, response.data)
        # Only the two independent IP buckets were created, not the otherwise
        # plausible account claim inside either signed/unsigned token payload.
        self.assertEqual(AuthRateLimitBucket.objects.count(), 2)

    def test_login_refresh_and_reset_operations_use_separate_namespaces(self):
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        self.assert_throttled(self.login())

        refreshed = self.client.post(
            reverse('api-refresh'),
            {'refresh': str(MobileTokenSerializer.get_token(self.user))},
            format='json', REMOTE_ADDR='192.0.2.10',
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        reset = self.reset_request('unknown@example.invalid')
        self.assertEqual(reset.status_code, 200, reset.data)
        confirmed = self.client.post(
            reverse('api-password-reset-confirm'), {}, format='json',
            REMOTE_ADDR='192.0.2.10',
        )
        self.assertEqual(confirmed.status_code, 400, confirmed.data)

    def test_reset_email_budget_is_normalized_and_shared_across_client_ips(self):
        for email, ip in (
            ('unknown@example.invalid', '192.0.2.11'),
            ('UNKNOWN@EXAMPLE.INVALID', '192.0.2.12'),
        ):
            response = self.reset_request(email, ip=ip)
            self.assertEqual(response.status_code, 200, response.data)
        with patch('apps.api.authentication.PasswordResetService.request') as deliver:
            blocked = self.reset_request('unknown@example.invalid', ip='192.0.2.13')
        self.assert_throttled(blocked)
        deliver.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def test_reset_existing_and_unknown_emails_receive_same_response_and_budget(self):
        first_payload = None
        for offset, email in ((0, self.user.email), (10, 'missing@example.invalid')):
            for attempt in (1, 2):
                response = self.reset_request(email, ip=f'198.51.100.{offset + attempt}')
                self.assertEqual(response.status_code, 200, response.data)
                first_payload = first_payload or response.json()
                self.assertEqual(response.json(), first_payload)
            self.assert_throttled(self.reset_request(email, ip=f'198.51.100.{offset + 3}'))
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(PASSWORD_RESET_IP_LIMIT=1)
    def test_legacy_reset_ip_limit_is_preserved_when_scoped_rate_is_higher(self):
        first = self.reset_request('first@example.invalid')
        self.assertEqual(first.status_code, 200, first.data)
        count_before = AuthRateLimitBucket.objects.count()
        self.assert_throttled(self.reset_request('second@example.invalid'))
        self.assertEqual(AuthRateLimitBucket.objects.count(), count_before)

    def test_reset_confirmation_budget_uses_canonical_uid_across_client_ips(self):
        for raw_uid, ip in ((str(self.user.pk), '192.0.2.11'), (f'000{self.user.pk}', '192.0.2.12')):
            response = self.client.post(
                reverse('api-password-reset-confirm'), {
                    # DRF trims CharField values before the service sees them.
                    # The account gate must canonicalize that same padded UID.
                    'uid': f' {urlsafe_base64_encode(force_bytes(raw_uid))} ',
                    'token': 'invalid-token',
                    'new_password': 'New-synthetic-password-2041!',
                    'new_password_confirm': 'New-synthetic-password-2041!',
                }, format='json', REMOTE_ADDR=ip,
            )
            self.assertEqual(response.status_code, 400, response.data)
        # Confirmation uses the DRF reset scope (not the email request quota).
        with self.rates(password_reset='2/hour'):
            with patch('apps.api.authentication.PasswordResetService.confirm') as confirm:
                blocked = self.client.post(
                    reverse('api-password-reset-confirm'), {
                        'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
                        'token': 'invalid-token',
                    }, format='json', REMOTE_ADDR='192.0.2.13',
                )
        self.assert_throttled(blocked)
        confirm.assert_not_called()

    def test_public_reset_ignores_authorization_headers_but_not_its_rate_limits(self):
        # Reset is deliberately a public, account-neutral recovery flow. An
        # expired/malformed bearer header must not prevent the shared IP gate.
        first = self.reset_request(
            'unknown@example.invalid', ip='192.0.2.11', HTTP_AUTHORIZATION='Bearer invalid',
        )
        second = self.reset_request(
            'unknown@example.invalid', ip='192.0.2.12', HTTP_AUTHORIZATION='Bearer invalid',
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assert_throttled(self.reset_request(
            'unknown@example.invalid', ip='192.0.2.13', HTTP_AUTHORIZATION='Bearer invalid',
        ))

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_only_rightmost_trusted_proxy_address_identifies_the_client(self):
        for number in (1, 2):
            response = self.login(
                username=f'unknown-{number}', ip=f'198.51.100.{number}',
                HTTP_X_FORWARDED_FOR=f'203.0.113.{number}, 192.0.2.50',
            )
            self.assertEqual(response.status_code, 401, response.data)
        self.assert_throttled(self.login(
            username='unknown-3', ip='198.51.100.3',
            HTTP_X_FORWARDED_FOR='203.0.113.3, 192.0.2.50',
        ))

    def test_untrusted_forwarded_headers_cannot_bypass_remote_address_limit(self):
        for number in (1, 2):
            response = self.login(
                username=f'unknown-{number}', HTTP_X_FORWARDED_FOR=f'203.0.113.{number}',
            )
            self.assertEqual(response.status_code, 401, response.data)
        self.assert_throttled(self.login(
            username='unknown-3', HTTP_X_FORWARDED_FOR='203.0.113.3',
        ))

    def test_missing_and_invalid_ip_addresses_share_a_fail_closed_common_budget(self):
        for username, ip in (('first-unknown', ''), ('second-unknown', 'not-an-ip')):
            response = self.login(username=username, ip=ip)
            self.assertEqual(response.status_code, 401, response.data)
        self.assert_throttled(self.login(username='third-unknown', ip='another-invalid-ip'))

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_different_trusted_client_addresses_have_independent_ip_budgets(self):
        for number in (1, 2, 3):
            response = self.login(
                username=f'unknown-{number}', ip='192.0.2.50',
                HTTP_X_FORWARDED_FOR=f'198.51.100.20, 203.0.113.{number}',
            )
            self.assertEqual(response.status_code, 401, response.data)

    def test_get_and_options_do_not_consume_authentication_budgets(self):
        for route in self.public_routes:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 405, response.data)
                response = self.client.options(reverse(route))
                self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 0)

    def test_store_failure_rejects_before_password_validation_or_email_delivery(self):
        with patch('apps.api.throttling.consume_limit', side_effect=DatabaseError('synthetic database failure')):
            with patch('apps.api.authentication.MobileTokenSerializer.validate') as validate:
                login = self.login(password=self.password)
            with patch('apps.api.authentication.PasswordResetService.request') as deliver:
                reset = self.reset_request(self.user.email)
        self.assert_unavailable(login)
        self.assert_unavailable(reset)
        validate.assert_not_called()
        deliver.assert_not_called()

    def test_store_failure_rejects_before_refresh_token_verification(self):
        with patch('apps.api.throttling.consume_limit', side_effect=DatabaseError('synthetic database failure')):
            with patch('apps.api.throttling.UntypedToken') as verify:
                with patch('apps.api.authentication.MobileTokenRefreshSerializer.validate') as validate:
                    response = self.client.post(
                        reverse('api-refresh'), {'refresh': 'not-to-be-verified'},
                        format='json', REMOTE_ADDR='192.0.2.10',
                    )
        self.assert_unavailable(response)
        verify.assert_not_called()
        validate.assert_not_called()

    def test_secondary_reset_audit_check_database_failure_cannot_dispatch_email(self):
        with patch(
            'apps.api.authentication.is_password_reset_rate_limited',
            side_effect=DatabaseError('synthetic database failure'),
        ):
            with patch('apps.api.authentication.PasswordResetService.request') as deliver:
                response = self.reset_request(self.user.email)
        self.assert_unavailable(response)
        deliver.assert_not_called()

    def test_login_security_log_records_real_throttled_and_unavailable_statuses(self):
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        self.assert_throttled(self.login())
        self.assertTrue(AuditLog.objects.filter(
            action='api.auth.login.blocked', status_code=429,
        ).exists())
        with patch('apps.api.throttling.consume_limit', side_effect=DatabaseError('synthetic database failure')):
            unavailable = self.login(ip='192.0.2.11')
        self.assert_unavailable(unavailable)
        self.assertTrue(AuditLog.objects.filter(
            action='api.auth.login.unavailable', status_code=503,
        ).exists())

    def test_pruning_failure_rejects_before_any_bucket_or_credentials_are_used(self):
        with patch('apps.api.throttling.prune_expired_buckets', side_effect=DatabaseError('synthetic database failure')):
            with patch('apps.api.throttling.consume_limit') as consume:
                with patch('apps.api.authentication.MobileTokenSerializer.validate') as validate:
                    response = self.login(password=self.password)
        self.assert_unavailable(response)
        consume.assert_not_called()
        validate.assert_not_called()

    def test_missing_invalid_and_nonpositive_rate_configuration_fail_closed(self):
        for value in (None, 'not-a-rate', '10/', '0/minute', '-1/minute', '2147483648/minute'):
            with self.subTest(rate=value), self.rates(auth=value):
                with patch('apps.api.authentication.MobileTokenSerializer.validate') as validate:
                    response = self.login(password=self.password)
                self.assert_unavailable(response)
                validate.assert_not_called()

    def test_missing_auth_scope_configuration_fails_closed(self):
        rates = dict(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])
        rates.pop('auth')
        with override_settings(REST_FRAMEWORK={
            **settings.REST_FRAMEWORK, 'DEFAULT_THROTTLE_RATES': rates,
        }):
            response = self.login(password=self.password)
        self.assert_unavailable(response)
        self.assertEqual(AuthRateLimitBucket.objects.count(), 0)

    def test_invalid_legacy_reset_configuration_fails_closed_before_email_delivery(self):
        invalid_values = (
            ('PASSWORD_RESET_LIMIT', 0),
            ('PASSWORD_RESET_LIMIT', True),
            ('PASSWORD_RESET_LIMIT', '2'),
            ('PASSWORD_RESET_LIMIT', 2147483648),
            ('PASSWORD_RESET_IP_LIMIT', -1),
            ('PASSWORD_RESET_WINDOW_SECONDS', 31536001),
        )
        for name, value in invalid_values:
            with self.subTest(setting=name, value=value), override_settings(**{name: value}):
                with patch('apps.api.authentication.PasswordResetService.request') as deliver:
                    response = self.reset_request(self.user.email)
                self.assert_unavailable(response)
                deliver.assert_not_called()
        self.assertEqual(AuthRateLimitBucket.objects.count(), 0)

    def test_retry_after_uses_remaining_bucket_lifetime_not_the_full_window(self):
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        AuthRateLimitBucket.objects.update(expires_at=timezone.now() + timedelta(seconds=3))
        blocked = self.login()
        self.assert_throttled(blocked)
        self.assertLessEqual(int(blocked['Retry-After']), 3)

    def test_expired_buckets_allow_a_new_attempt(self):
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        self.assert_throttled(self.login())
        AuthRateLimitBucket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.login()
        self.assertEqual(response.status_code, 401, response.data)

    def test_persisted_bucket_keys_do_not_contain_passwords_tokens_or_identifiers(self):
        self.assertEqual(self.login(password='secret-marker-not-for-storage').status_code, 401)
        reset = self.reset_request('sensitive-identifier@example.invalid')
        self.assertEqual(reset.status_code, 200, reset.data)
        keys = list(AuthRateLimitBucket.objects.values_list('key', flat=True))
        self.assertTrue(keys)
        for key in keys:
            self.assertRegex(key, r'^[0-9a-f]{64}$')
            self.assertNotIn(self.user.username, key)
            self.assertNotIn('sensitive-identifier', key)
            self.assertNotIn('secret-marker', key)
