"""CY-03 channel compatibility; logout policy deliberately remains unchanged.

These logout characterizations are not a claim of immediate per-device access
token revocation. Change these expectations if that separate policy is approved.
Only synthetic accounts and in-process HTTP clients are used.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.authentication import MobileTokenSerializer


class LogoutPolicyCompatibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='cy03-logout-synthetic',
            email='cy03-logout@example.invalid',
            password='Synthetic-logout-pass-2026!',
        )

    def test_web_logout_affects_only_the_current_browser_session(self):
        first, second = Client(), Client()
        first.force_login(self.user)
        second.force_login(self.user)
        self.assertEqual(first.get(reverse('profile')).status_code, 200)
        self.assertEqual(second.get(reverse('profile')).status_code, 200)
        self.assertEqual(first.post(reverse('logout')).status_code, 302)
        self.assertEqual(first.get(reverse('profile')).status_code, 302)
        self.assertEqual(second.get(reverse('profile')).status_code, 200)

    def test_api_logout_blacklists_submitted_refresh_not_all_access_or_devices(self):
        first = MobileTokenSerializer.get_token(self.user)
        second = MobileTokenSerializer.get_token(self.user)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {first.access_token}')
        self.assertEqual(api.post(
            reverse('api-logout'), {'refresh': str(first)}, format='json',
        ).status_code, 200)
        self.assertEqual(api.get(reverse('api-me')).status_code, 200)
        public = APIClient()
        self.assertEqual(public.post(
            reverse('api-refresh'), {'refresh': str(first)}, format='json',
        ).status_code, 401)
        self.assertEqual(public.post(
            reverse('api-refresh'), {'refresh': str(second)}, format='json',
        ).status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_token_version, 1)

    def test_an_existing_web_session_is_denied_while_account_is_inactive(self):
        browser = Client()
        browser.force_login(self.user)
        self.assertEqual(browser.get(reverse('profile')).status_code, 200)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(browser.get(reverse('profile')).status_code, 302)


class CY03SchemaCompatibilityTests(TestCase):
    def test_models_have_no_pending_migration(self):
        # Compare model/migration state and check only synthetic local history;
        # --dry-run never creates a migration or changes any schema.
        call_command('makemigrations', check=True, dry_run=True, verbosity=0,
                     stdout=StringIO(), stderr=StringIO())
