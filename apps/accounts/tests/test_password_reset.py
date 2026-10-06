from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from apps.accounts.services import PasswordResetService


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_RESET_PUBLIC_BASE_URL='https://erp.example.test',
    PASSWORD_RESET_LIMIT=5,
    PASSWORD_RESET_IP_LIMIT=10,
    PASSWORD_RESET_WINDOW_SECONDS=3600,
)
class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='reset-user',
            email='person@example.com',
            password='Old-password-2040!',
        )

    def token_data(self):
        return {
            'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': default_token_generator.make_token(self.user),
        }

    def test_web_request_sends_multipart_email_with_public_link(self):
        response = self.client.post(
            reverse('password_reset'), {'email': ' Person@Example.com '},
        )
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('https://erp.example.test/reset/', message.body)
        self.assertEqual(message.alternatives[0].mimetype, 'text/html')
        self.assertIn('Choisir un nouveau mot de passe', message.alternatives[0].content)

    def test_unknown_and_inactive_accounts_receive_same_public_response(self):
        response = self.client.post(
            reverse('password_reset'), {'email': 'unknown@example.com'},
        )
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(mail.outbox, [])

        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.post(
            reverse('password_reset'), {'email': self.user.email},
        )
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(mail.outbox, [])

    def test_api_rejects_malformed_email(self):
        for email in ('', 'invalid'):
            with self.subTest(email=email):
                response = APIClient().post(
                    reverse('api-password-reset-request'), {'email': email}, format='json',
                )
                self.assertEqual(response.status_code, 400)

    @patch('apps.accounts.services.password_reset.DjangoEmailDeliveryService.send')
    def test_api_reports_provider_failure_without_exposing_details(self, send):
        send.side_effect = OSError('secret provider detail')
        response = APIClient().post(
            reverse('api-password-reset-request'),
            {'email': self.user.email},
            format='json',
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret provider detail', str(response.data))

    @override_settings(PASSWORD_RESET_LIMIT=1)
    def test_rate_limit_is_applied_per_normalized_email(self):
        first = self.client.post(reverse('password_reset'), {'email': self.user.email})
        second = self.client.post(
            reverse('password_reset'), {'email': self.user.email.upper()},
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)

    @override_settings(PASSWORD_RESET_LIMIT=100, PASSWORD_RESET_IP_LIMIT=1)
    def test_rate_limit_is_also_applied_globally_per_ip(self):
        first = self.client.post(
            reverse('password_reset'), {'email': 'first-unknown@example.com'},
        )
        second = self.client.post(
            reverse('password_reset'), {'email': 'second-unknown@example.com'},
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)

    def test_confirm_changes_password_and_token_is_single_use(self):
        token = self.token_data()
        PasswordResetService().confirm(
            **token,
            new_password='New-password-2041!',
            new_password_confirm='New-password-2041!',
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-password-2041!'))
        self.assertIsNone(PasswordResetService().validate_token(**token))

    def test_token_is_invalidated_by_an_independent_password_change(self):
        token = self.token_data()
        self.user.set_password('Another-password-2041!')
        self.user.save(update_fields=['password'])
        self.assertIsNone(PasswordResetService().validate_token(**token))

    def test_confirm_rejects_mismatch_weak_and_invalid_token(self):
        token = self.token_data()
        cases = (
            {**token, 'new_password': 'Mismatch-2041!', 'new_password_confirm': 'Other-2041!'},
            {**token, 'new_password': '123', 'new_password_confirm': '123'},
            {**token, 'token': 'bad-token', 'new_password': 'New-password-2041!', 'new_password_confirm': 'New-password-2041!'},
        )
        for payload in cases:
            with self.subTest(payload=payload['token']):
                response = APIClient().post(
                    reverse('api-password-reset-confirm'), payload, format='json',
                )
                self.assertEqual(response.status_code, 400)

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_token_is_rejected(self):
        token = self.token_data()
        self.assertIsNone(PasswordResetService().validate_token(**token))

    def test_web_link_completes_the_full_reset_flow(self):
        self.client.post(reverse('password_reset'), {'email': self.user.email})
        reset_url = next(
            line for line in mail.outbox[0].body.splitlines()
            if line.startswith('https://erp.example.test/reset/')
        )
        reset_path = reset_url.removeprefix('https://erp.example.test')
        response = self.client.get(reset_path)
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            response['Location'],
            {
                'new_password1': 'Web-password-2042!',
                'new_password2': 'Web-password-2042!',
            },
        )
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Web-password-2042!'))

    def test_reset_revokes_existing_access_and_refresh_tokens(self):
        api = APIClient()
        login_response = api.post(
            reverse('api-login'),
            {'username': self.user.username, 'password': 'Old-password-2040!'},
            format='json',
        )
        self.assertEqual(login_response.status_code, 200)
        access = login_response.data['access']
        refresh = login_response.data['refresh']
        self.user.refresh_from_db()
        token = self.token_data()
        PasswordResetService().confirm(
            **token,
            new_password='New-password-2041!',
            new_password_confirm='New-password-2041!',
        )

        api.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(api.get(reverse('api-me')).status_code, 401)
        api.credentials()
        self.assertEqual(
            api.post(reverse('api-refresh'), {'refresh': refresh}, format='json').status_code,
            401,
        )
        self.assertEqual(
            api.post(
                reverse('api-login'),
                {'username': self.user.username, 'password': 'Old-password-2040!'},
                format='json',
            ).status_code,
            401,
        )
        self.assertEqual(
            api.post(
                reverse('api-login'),
                {'username': self.user.username, 'password': 'New-password-2041!'},
                format='json',
            ).status_code,
            200,
        )

    def test_admin_sends_link_instead_of_selecting_password(self):
        admin = get_user_model().objects.create_superuser(
            username='admin-reset', email='admin@example.com', password='Admin-2040!',
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse('user_password_reset_admin', args=[self.user.pk]),
            {'channel': 'email'},
        )
        self.assertRedirects(response, reverse('user_list'))
        self.assertEqual(len(mail.outbox), 1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old-password-2040!'))

    def test_admin_ui_disables_email_when_address_is_missing(self):
        admin = get_user_model().objects.create_superuser(
            username='admin-no-email', email='admin@example.com', password='Admin-2040!',
        )
        target = get_user_model().objects.create_user(
            username='missing-email', password='Old-password-2040!',
        )
        self.client.force_login(admin)
        response = self.client.get(
            reverse('user_password_reset_admin', args=[target.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aucune adresse e-mail configurée.')
        self.assertContains(response, 'SMS — bientôt disponible')
