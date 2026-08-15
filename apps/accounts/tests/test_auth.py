from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from unittest.mock import patch

from apps.core.models import AuditLog


class AuthTests(TestCase):
    @override_settings(LOGIN_FAILURE_LIMIT=2, LOGIN_FAILURE_WINDOW_SECONDS=900)
    def test_login_is_temporarily_rate_limited(self):
        User = get_user_model()
        User.objects.create_user(username='limited_user', password='ValidPass123!')
        url = reverse('login')

        for _ in range(2):
            response = self.client.post(url, {'username': 'limited_user', 'password': 'wrong'})
            self.assertEqual(response.status_code, 200)

        blocked_response = self.client.post(url, {'username': 'limited_user', 'password': 'ValidPass123!'})
        self.assertEqual(blocked_response.status_code, 429)
        self.assertIn('Retry-After', blocked_response)

    @override_settings(
        LOGIN_FAILURE_LIMIT=10,
        LOGIN_FAILURE_IP_LIMIT=2,
        LOGIN_FAILURE_WINDOW_SECONDS=900,
    )
    def test_login_ip_limit_cannot_be_bypassed_by_rotating_usernames(self):
        url = reverse('login')

        for username in ('unknown-one', 'unknown-two'):
            response = self.client.post(url, {'username': username, 'password': 'wrong'})
            self.assertEqual(response.status_code, 200)

        blocked_response = self.client.post(
            url,
            {'username': 'unknown-three', 'password': 'wrong'},
        )
        self.assertEqual(blocked_response.status_code, 429)
        self.assertIn('Retry-After', blocked_response)

    @override_settings(PASSWORD_RESET_LIMIT=1, PASSWORD_RESET_WINDOW_SECONDS=3600)
    def test_password_reset_is_rate_limited(self):
        url = reverse('password_reset')

        first_response = self.client.post(url, {'email': 'unknown@example.invalid'})
        self.assertEqual(first_response.status_code, 302)

        blocked_response = self.client.post(url, {'email': 'unknown@example.invalid'})
        self.assertEqual(blocked_response.status_code, 429)
        self.assertIn('Retry-After', blocked_response)

    @override_settings(LOGIN_FAILURE_LIMIT=1, LOGIN_FAILURE_WINDOW_SECONDS=900)
    def test_admin_login_is_rate_limited(self):
        url = reverse('admin:login')

        first_response = self.client.post(url, {'username': 'unknown', 'password': 'wrong'})
        self.assertEqual(first_response.status_code, 200)

        blocked_response = self.client.post(url, {'username': 'unknown', 'password': 'wrong'})
        self.assertEqual(blocked_response.status_code, 429)
        self.assertIn('Retry-After', blocked_response)
        self.assertIn('Content-Security-Policy', blocked_response)
        self.assertEqual(blocked_response['X-Frame-Options'], 'DENY')

    def test_login_view_and_authentication(self):
        User = get_user_model()
        user = User.objects.create_user(username='authuser', password='secret')
        url = reverse('login')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # attempt login
        resp = self.client.post(url, {'username': 'authuser', 'password': 'secret'})
        # login view should redirect on success
        self.assertIn(resp.status_code, (302, 301))

    def test_logout(self):
        User = get_user_model()
        user = User.objects.create_user(username='authuser2', password='secret')
        self.client.login(username='authuser2', password='secret')
        get_response = self.client.get(reverse('logout'))
        self.assertEqual(get_response.status_code, 405)

        resp = self.client.post(reverse('logout'))
        # logout redirects to login
        self.assertIn(resp.status_code, (302, 301))

    def test_forced_password_change_blocks_other_pages_until_completed(self):
        User = get_user_model()
        user = User.objects.create_user(
            username='temporary_password_user',
            password='OldPass123!',
            force_password_change=True,
        )
        self.client.force_login(user)

        blocked_response = self.client.get(reverse('dashboard'))
        self.assertRedirects(blocked_response, reverse('profile'))

        change_response = self.client.post(
            reverse('profile'),
            {
                'change_password': '1',
                'old_password': 'OldPass123!',
                'new_password1': 'NewPass456!',
                'new_password2': 'NewPass456!',
            },
        )
        self.assertRedirects(change_response, reverse('password_change_done'))
        user.refresh_from_db()
        self.assertFalse(user.force_password_change)
        self.assertTrue(user.check_password('NewPass456!'))

        allowed_response = self.client.get(reverse('dashboard'))
        self.assertEqual(allowed_response.status_code, 200)

    def test_reset_admin_command_updates_existing_admin_password(self):
        User = get_user_model()
        admin = User.objects.create_superuser(username='admin', password='OldPass123!')
        admin.role = User.SELLER
        admin.save()

        with patch.dict('os.environ', {'ADMIN_RECOVERY_PASSWORD': 'NewPass123!'}):
            call_command('reset_admin', password_env='ADMIN_RECOVERY_PASSWORD')

        admin.refresh_from_db()
        self.assertTrue(admin.check_password('NewPass123!'))
        self.assertFalse(admin.check_password('OldPass123!'))
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)
        self.assertEqual(admin.role, User.ADMIN)
        self.assertTrue(AuditLog.objects.filter(user=admin, action__icontains='Réinitialisation').exists())

    def test_reset_admin_command_rejects_short_password(self):
        User = get_user_model()
        User.objects.create_superuser(username='admin', password='OldPass123!')

        with patch.dict('os.environ', {'ADMIN_RECOVERY_PASSWORD': 'short'}):
            with self.assertRaises(CommandError):
                call_command('reset_admin', password_env='ADMIN_RECOVERY_PASSWORD')
