from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from unittest.mock import patch

from apps.core.models import AuditLog


class AuthTests(TestCase):
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
        resp = self.client.get(reverse('logout'))
        # logout redirects to login
        self.assertIn(resp.status_code, (302, 301))

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
