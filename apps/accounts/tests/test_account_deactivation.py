"""Local CY-03 regressions for durable JWT revocation on deactivation.

All accounts, tokens and requests are synthetic.  The PostgreSQL class uses
only the disposable loopback cluster supplied by the guarded test runner.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.forms import ProfileForm, UserUpdateForm
from apps.api.authentication import MobileTokenSerializer
from apps.core.models import AuditLog


class AccountDeactivationJWTTests(TestCase):
    password = 'CY03-Synthetic-Password-2026!'

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username='cy03-synthetic-user',
            email='cy03-user@example.invalid',
            password=self.password,
        )

    def tokens(self):
        refresh = MobileTokenSerializer.get_token(self.user)
        return str(refresh.access_token), str(refresh)

    @staticmethod
    def authorized(access):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        return client

    def test_disable_then_reactivate_never_revives_old_access_or_refresh(self):
        access, refresh = self.tokens()
        self.assertEqual(self.authorized(access).get(reverse('api-me')).status_code, 200)

        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)
        self.assertEqual(self.authorized(access).get(reverse('api-me')).status_code, 401)

        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)
        rejected_access = self.authorized(access).get(reverse('api-me'))
        self.assertEqual(rejected_access.status_code, 401)
        self.assertEqual(rejected_access.json()['error']['code'], 'TOKEN_REVOKED')
        rejected_refresh = APIClient().post(
            reverse('api-refresh'), {'refresh': refresh}, format='json',
        )
        self.assertEqual(rejected_refresh.status_code, 401)
        self.assertEqual(rejected_refresh.json()['error']['code'], 'TOKEN_REVOKED')

        fresh = APIClient().post(reverse('api-login'), {
            'username': self.user.username, 'password': self.password,
        }, format='json')
        self.assertEqual(fresh.status_code, 200, fresh.data)
        fresh_access = fresh.json()['data']['access']
        self.assertEqual(self.authorized(fresh_access).get(reverse('api-me')).status_code, 200)

    def test_queryset_status_updates_are_atomic_monotonic_and_idempotent(self):
        self.assertEqual(
            self.User.objects.filter(pk=self.user.pk).update(is_active=False), 1,
        )
        self.assertEqual(
            self.User.objects.filter(pk=self.user.pk).update(is_active=False), 1,
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)
        self.User.objects.filter(pk=self.user.pk).update(is_active=True)
        self.User.objects.filter(pk=self.user.pk).update(auth_token_version=1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)

    def test_ambiguous_bulk_authentication_mutations_fail_closed(self):
        self.user.is_active = False
        with self.assertRaisesRegex(ValueError, 'save'):
            self.User.objects.bulk_update([self.user], ['is_active'])
        with self.assertRaisesRegex(ValueError, 'boolean'):
            self.User.objects.filter(pk=self.user.pk).update(is_active=1)
        with self.assertRaisesRegex(ValueError, 'combine'):
            self.User.objects.filter(pk=self.user.pk).update(
                is_active=False, auth_token_version=8,
            )

    def test_stale_full_save_cannot_reactivate_or_lower_generation(self):
        stale = self.User.objects.get(pk=self.user.pk)
        self.User.objects.filter(pk=self.user.pk).update(is_active=False)
        stale.first_name = 'Safe stale profile edit'
        stale.auth_token_version = 1
        stale.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Safe stale profile edit')
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)

    def test_stale_profile_and_management_forms_cannot_reactivate(self):
        profile_user = self.User.objects.get(pk=self.user.pk)
        profile = ProfileForm({
            'first_name': 'Profile edit', 'last_name': '',
            'email': profile_user.email, 'phone': '',
        }, instance=profile_user)
        self.assertTrue(profile.is_valid(), profile.errors)
        self.User.objects.filter(pk=self.user.pk).update(is_active=False)
        profile.save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)

        self.User.objects.filter(pk=self.user.pk).update(is_active=True)
        managed = self.User.objects.get(pk=self.user.pk)
        role = Group.objects.create(name='CY03 synthetic seller role')
        form = UserUpdateForm({
            'username': managed.username,
            'first_name': 'Managed edit', 'last_name': '',
            'email': managed.email, 'phone': '', 'is_active': 'on',
            'assigned_role': role.pk,
            'individual_permissions': [], 'denied_permissions': [],
        }, instance=managed)
        self.assertTrue(form.is_valid(), form.errors)
        self.User.objects.filter(pk=self.user.pk).update(is_active=False)
        form.save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 3)
        self.assertEqual(self.user.first_name, 'Managed edit')


class AccountDeactivationWebTests(TestCase):
    password = 'CY03-Web-Synthetic-2026!'

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username='cy03-owner', email='cy03-owner@example.invalid',
            password=self.password,
        )
        self.target = User.objects.create_user(
            username='cy03-target', email='cy03-target@example.invalid',
            password=self.password,
        )

    def test_toggle_revokes_once_keeps_logs_token_free_and_allows_fresh_login(self):
        old_refresh = MobileTokenSerializer.get_token(self.target)
        old_access = str(old_refresh.access_token)
        browser = Client()
        browser.force_login(self.owner)
        with self.assertLogs('security', level='INFO') as captured:
            response = browser.post(reverse('user_toggle_active', args=[self.target.pk]))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(self.target.auth_token_version, 2)
        logs = '\n'.join(captured.output)
        self.assertNotIn(old_access, logs)
        self.assertNotIn(str(old_refresh), logs)
        for action, path in AuditLog.objects.values_list('action', 'path'):
            self.assertNotIn(old_access, action + path)
            self.assertNotIn(str(old_refresh), action + path)

        response = browser.post(reverse('user_toggle_active', args=[self.target.pk]))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertEqual(self.target.auth_token_version, 2)
        rejected = APIClient()
        rejected.credentials(HTTP_AUTHORIZATION=f'Bearer {old_access}')
        self.assertEqual(rejected.get(reverse('api-me')).status_code, 401)
        fresh = APIClient().post(reverse('api-login'), {
            'username': self.target.username, 'password': self.password,
        }, format='json')
        self.assertEqual(fresh.status_code, 200, fresh.data)

    def test_toggle_requires_post_csrf_permission_and_refuses_self_disable(self):
        anonymous = Client()
        self.assertIn(
            anonymous.post(reverse('user_toggle_active', args=[self.target.pk])).status_code,
            (302, 403),
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(
            csrf.post(reverse('user_toggle_active', args=[self.target.pk])).status_code, 403,
        )
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertEqual(self.target.auth_token_version, 1)

        browser = Client()
        browser.force_login(self.owner)
        self.assertEqual(
            browser.get(reverse('user_toggle_active', args=[self.target.pk])).status_code, 405,
        )
        self.assertEqual(
            browser.post(reverse('user_toggle_active', args=[self.owner.pk])).status_code, 302,
        )
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertEqual(self.owner.auth_token_version, 1)


class AccountDeactivationPostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL-only concurrency proof')
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username='cy03-concurrent', password='CY03-Concurrent-2026!',
        )

    def concurrently(self, *operations):
        barrier = Barrier(len(operations))

        def invoke(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(invoke, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def test_two_concurrent_disables_increment_generation_once(self):
        pk = self.user.pk
        self.concurrently(
            lambda: self.User.objects.filter(pk=pk).update(is_active=False),
            lambda: self.User.objects.filter(pk=pk).update(is_active=False),
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 2)

    def test_concurrent_disable_and_independent_revocation_are_both_retained(self):
        pk = self.user.pk

        def revoke():
            self.User.objects.get(pk=pk).revoke_api_tokens()

        self.concurrently(
            lambda: self.User.objects.filter(pk=pk).update(is_active=False),
            revoke,
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.auth_token_version, 3)
