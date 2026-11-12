"""No real mail/network: public neutrality and single-use Web confirmation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from apps.accounts.forms import StyledSetPasswordForm
from apps.accounts.services import PasswordResetDeliveryError, PasswordResetService
from apps.core.models import AuditLog, AuthRateLimitBucket


_SETTINGS = {
    'PASSWORD_HASHERS': ['django.contrib.auth.hashers.MD5PasswordHasher'],
    'SECURE_SSL_REDIRECT': False,
    'TRUST_X_FORWARDED_FOR': False,
    'PASSWORD_RESET_EMAIL_PROVIDER': 'django',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'PASSWORD_RESET_PUBLIC_BASE_URL': 'https://erp.example.test',
    'PASSWORD_RESET_LIMIT': 20,
    'PASSWORD_RESET_IP_LIMIT': 100,
    'PASSWORD_RESET_WINDOW_SECONDS': 3600,
}


@override_settings(**_SETTINGS)
class PasswordResetPrivacyTests(TestCase):
    password = 'Synthetic-reset-old-password-2040!'

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='reset-private', email='private@example.invalid', password=cls.password,
        )
        cls.inactive = get_user_model().objects.create_user(
            username='reset-inactive', email='inactive@example.invalid', password=cls.password, is_active=False,
        )

    def token_data(self):
        return (
            urlsafe_base64_encode(force_bytes(self.user.pk)),
            default_token_generator.make_token(self.user),
        )

    def reset_form_url(self, client=None):
        uid, token = self.token_data()
        response = (client or self.client).get(reverse('password_reset_confirm', kwargs={
            'uidb64': uid, 'token': token,
        }))
        self.assertEqual(response.status_code, 302)
        return response['Location']

    @patch('apps.accounts.services.password_reset.DjangoEmailDeliveryService.send')
    def test_provider_failure_web_and_api_known_unknown_inactive_have_identical_public_contract(self, send):
        send.side_effect = OSError('private-key recipient link https://secret.invalid/reset/token')
        web_results = []
        api_results = []
        with self.assertLogs('security', level='ERROR') as logs:
            for email in (self.user.email, 'unknown@example.invalid', self.inactive.email):
                response = self.client.post(reverse('password_reset'), {'email': email})
                web_results.append((response.status_code, response.get('Location'), response.content))
                response = APIClient().post(
                    reverse('api-password-reset-request'), {'email': email}, format='json',
                )
                api_results.append((response.status_code, response.json()))
        self.assertTrue(all(value == web_results[0] for value in web_results))
        self.assertTrue(all(value == api_results[0] for value in api_results))
        self.assertEqual(web_results[0][0], 302)
        self.assertEqual(web_results[0][1], reverse('password_reset_done'))
        self.assertEqual(api_results[0][0], 200)
        self.assertEqual(send.call_count, 2)
        diagnostic = '\n'.join(logs.output)
        for private_value in ('private-key', 'secret.invalid', self.user.email, self.inactive.email):
            self.assertNotIn(private_value, diagnostic)
            self.assertNotIn(private_value, str(web_results) + str(api_results))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertEqual(self.user.auth_token_version, 1)
        self.assertEqual(AuditLog.objects.filter(action__endswith='delivery_failed', status_code=503).count(), 2)

    def test_admin_keeps_actionable_delivery_error_without_changing_password(self):
        admin = get_user_model().objects.create_superuser(
            username='reset-controller', password=self.password,
        )
        self.client.force_login(admin)
        with patch('apps.accounts.forms.PasswordResetService.request_for_user', side_effect=PasswordResetDeliveryError):
            response = self.client.post(reverse('user_password_reset_admin', args=[self.user.pk]), {'channel': 'email'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'temporairement indisponible')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    @override_settings(PASSWORD_RESET_LIMIT=1)
    def test_admin_reset_has_persistent_distinct_account_admission(self):
        admin = get_user_model().objects.create_superuser(
            username='reset-quota-controller', password=self.password,
        )
        self.client.force_login(admin)
        url = reverse('user_password_reset_admin', args=[self.user.pk])
        with patch('apps.accounts.forms.PasswordResetService.request_for_user') as deliver:
            self.assertEqual(self.client.post(url, {'channel': 'email'}, REMOTE_ADDR='192.0.2.10').status_code, 302)
            blocked = self.client.post(url, {'channel': 'email'}, REMOTE_ADDR='192.0.2.11')
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked['Retry-After']), 0)
        self.assertEqual(deliver.call_count, 1)
        # Exhausting the authenticated administrator's namespace must not let
        # an attacker block the target's public reset by submitting admin URLs.
        self.assertEqual(Client().post(reverse('password_reset'), {'email': self.user.email}).status_code, 302)

    @override_settings(PASSWORD_RESET_LIMIT=1)
    def test_public_reset_global_account_budget_uses_normalized_email_across_ips(self):
        self.assertEqual(self.client.post(
            reverse('password_reset'), {'email': ' PRIVATE@example.invalid '}, REMOTE_ADDR='192.0.2.10',
        ).status_code, 302)
        with patch('apps.accounts.forms.PasswordResetService.request') as deliver:
            denied = self.client.post(
                reverse('password_reset'), {'email': self.user.email}, REMOTE_ADDR='192.0.2.11',
            )
        self.assertEqual(denied.status_code, 429)
        deliver.assert_not_called()

    def test_reset_store_failure_is_503_for_any_identity_and_never_dispatches(self):
        from django.db import DatabaseError

        for email in (self.user.email, 'unknown@example.invalid'):
            with self.subTest(email=email), \
                    patch('apps.core.web_auth_rate_limit.consume_limit', side_effect=DatabaseError('synthetic private store error')), \
                    patch('apps.accounts.forms.PasswordResetService.request') as deliver:
                response = self.client.post(reverse('password_reset'), {'email': email})
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('synthetic private store error', response.content.decode())
                deliver.assert_not_called()

    @override_settings(PASSWORD_RESET_LIMIT=1)
    def test_valid_link_confirmation_is_also_admitted_before_validating_new_password(self):
        url = self.reset_form_url()
        self.assertEqual(self.client.post(url, {'new_password1': 'short', 'new_password2': 'short'}).status_code, 200)
        with patch('apps.accounts.forms.PasswordResetService.confirm') as confirm, \
                patch('django.contrib.auth.password_validation.validate_password') as validate:
            denied = self.client.post(url, {
                'new_password1': 'Synthetic-reset-new-password-2041!',
                'new_password2': 'Synthetic-reset-new-password-2041!',
            }, REMOTE_ADDR='192.0.2.11')
        self.assertEqual(denied.status_code, 429)
        confirm.assert_not_called()
        validate.assert_not_called()

    def test_recheck_failure_between_dispatch_and_save_has_normal_invalid_link_response(self):
        url = self.reset_form_url()
        with patch('apps.accounts.forms.PasswordResetService.confirm', side_effect=ValidationError({'token': 'private detail'})):
            response = self.client.post(url, {
                'new_password1': 'Synthetic-reset-new-password-2041!',
                'new_password2': 'Synthetic-reset-new-password-2041!',
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['validlink'])
        self.assertNotContains(response, 'private detail')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertFalse(AuditLog.objects.filter(action='auth.password_reset.completed').exists())

    def test_stale_form_token_and_inactive_user_cannot_be_consumed(self):
        uid, token = self.token_data()
        form = StyledSetPasswordForm(self.user, data={
            'new_password1': 'Synthetic-reset-new-password-2041!',
            'new_password2': 'Synthetic-reset-new-password-2041!',
        }, reset_uid=uid, reset_token=token)
        self.assertTrue(form.is_valid(), form.errors)
        PasswordResetService().confirm(
            uid=uid, token=token, new_password='Independent-new-password-2042!',
            new_password_confirm='Independent-new-password-2042!',
        )
        with self.assertRaises(ValidationError):
            form.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Independent-new-password-2042!'))
        self.assertEqual(self.user.auth_token_version, 2)

    def test_deactivation_between_validation_and_lock_is_rechecked(self):
        uid, token = self.token_data()
        service = PasswordResetService()
        original_validate = service.validate_token

        def deactivate_after_validation(*args):
            user = original_validate(*args)
            get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
            return user

        with patch.object(service, 'validate_token', side_effect=deactivate_after_validation):
            with self.assertRaises(ValidationError):
                service.confirm(
                    uid=uid, token=token, new_password='Synthetic-reset-new-password-2041!',
                    new_password_confirm='Synthetic-reset-new-password-2041!',
                )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))


@skipUnless(connection.vendor == 'postgresql', 'Real reset token races require PostgreSQL.')
@override_settings(**_SETTINGS)
class PostgreSQLWebResetConfirmationConcurrencyTests(TransactionTestCase):
    def test_same_token_in_two_browser_sessions_is_consumed_once_under_user_lock(self):
        user = get_user_model().objects.create_user(
            username='reset-race-user', email='race@example.invalid', password='Old-race-password-2040!',
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        url = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
        barrier = Barrier(2)
        original_save = StyledSetPasswordForm.save

        def synchronized_save(form, *args, **kwargs):
            # Both requests have passed Django dispatch's token check and the
            # bound form's password validation before they contend on the user.
            barrier.wait(timeout=30)
            return original_save(form, *args, **kwargs)

        def worker(index):
            close_old_connections()
            try:
                client = Client()
                redirect = client.get(url)
                self.assertEqual(redirect.status_code, 302)
                self.assertEqual(client.session[INTERNAL_RESET_SESSION_TOKEN], token)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                password = f'Race-new-password-{2041 + index}!'
                response = client.post(redirect['Location'], {
                    'new_password1': password, 'new_password2': password,
                })
                return pid, password, response.status_code, response.get('Location')
            finally:
                connections.close_all()

        with patch.object(StyledSetPasswordForm, 'save', synchronized_save):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(worker, index) for index in range(2)]
                results = [future.result(timeout=60) for future in futures]
        self.assertEqual(len({row[0] for row in results}), 2)
        self.assertEqual(sorted(row[2] for row in results), [200, 302])
        winner = next(row for row in results if row[2] == 302)
        self.assertEqual(winner[3], reverse('password_reset_complete'))
        user.refresh_from_db()
        self.assertTrue(user.check_password(winner[1]))
        self.assertEqual(user.auth_token_version, 2)
        self.assertEqual(AuditLog.objects.filter(action='auth.password_reset.completed').count(), 1)
