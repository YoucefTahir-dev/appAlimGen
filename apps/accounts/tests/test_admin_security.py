from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.admin import GroupAdmin, UserAdminPasswordChangeForm
from apps.accounts.admin_site import AdminSelfPasswordChangeForm, SecureAdminSite


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountAdminPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.owner = cls.User.objects.create_superuser(
            username='account-admin-owner', password='Owner-Synthetic-123!',
        )
        cls.operator = cls.User.objects.create_user(
            username='account-admin-delegate', password='Delegate-Synthetic-123!',
            is_staff=True, role=cls.User.ADMIN,
        )
        # Even every explicit model permission must not make a staff user a
        # security administrator. role='admin' is intentionally insufficient.
        cls.operator.user_permissions.set(Permission.objects.all())
        cls.target = cls.User.objects.create_user(
            username='account-admin-target', password='Target-Synthetic-123!',
            date_joined=timezone.now().replace(microsecond=0),
        )
        cls.group = Group.objects.create(name='Admin security synthetic group')

    def setUp(self):
        cache.clear()

    def user_urls(self, target):
        return [
            reverse('admin:accounts_user_changelist'),
            reverse('admin:accounts_user_add'),
            reverse('admin:accounts_user_change', args=[target.pk]),
            reverse('admin:accounts_user_history', args=[target.pk]),
            reverse('admin:accounts_user_delete', args=[target.pk]),
            reverse('admin:auth_user_password_change', args=[target.pk]),
        ]

    def group_urls(self):
        return [
            reverse('admin:auth_group_changelist'),
            reverse('admin:auth_group_add'),
            reverse('admin:auth_group_change', args=[self.group.pk]),
            reverse('admin:auth_group_history', args=[self.group.pk]),
            reverse('admin:auth_group_delete', args=[self.group.pk]),
        ]

    def user_change_data(self, **extra):
        joined = timezone.localtime(self.target.date_joined)
        return {
            'username': self.target.username, 'role': self.target.role, 'is_active': 'on',
            'first_name': self.target.first_name, 'last_name': self.target.last_name,
            'email': self.target.email, 'phone': self.target.phone,
            'date_joined_0': joined.strftime('%Y-%m-%d'),
            'date_joined_1': joined.strftime('%H:%M:%S'),
            # date_joined has a callable model default. A real admin browser
            # also posts its hidden initial split value for has_changed().
            'initial-date_joined_0': joined.strftime('%Y-%m-%d'),
            'initial-date_joined_1': joined.strftime('%H:%M:%S'),
            **extra,
        }

    def test_group_admin_is_registered_with_the_same_guard_as_users(self):
        self.assertIsInstance(admin.site, SecureAdminSite)
        self.assertIsInstance(admin.site._registry[Group], GroupAdmin)
        request = RequestFactory().get('/admin/')
        for user, allowed in ((self.owner, True), (self.operator, False)):
            request.user = user
            for model, obj in ((self.User, self.target), (Group, self.group)):
                model_admin = admin.site._registry[model]
                with self.subTest(user=user.username, model=model.__name__):
                    self.assertEqual(model_admin.has_module_permission(request), allowed)
                    self.assertEqual(model_admin.has_add_permission(request), allowed)
                    for method in ('has_view_permission', 'has_change_permission', 'has_delete_permission'):
                        self.assertEqual(getattr(model_admin, method)(request), allowed)
                        self.assertEqual(getattr(model_admin, method)(request, obj), allowed)

    def test_inactive_or_nonstaff_superuser_cannot_manage_accounts(self):
        request = RequestFactory().get('/admin/')
        for field in ('is_active', 'is_staff'):
            user = self.User.objects.get(pk=self.owner.pk)
            setattr(user, field, False)
            request.user = user
            for model in (self.User, Group):
                with self.subTest(field=field, model=model.__name__):
                    self.assertFalse(admin.site._registry[model].has_module_permission(request))
                    self.assertFalse(admin.site._registry[model].has_change_permission(request))

    def test_delegated_staff_cannot_open_any_user_or_group_admin_url(self):
        self.client.force_login(self.operator)
        urls = self.user_urls(self.operator) + self.user_urls(self.target) + self.user_urls(self.owner)
        for url in dict.fromkeys(urls + self.group_urls()):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_account_and_group_links_are_hidden_from_delegated_staff(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('admin:accounts_user_changelist'))
        self.assertNotContains(response, reverse('admin:auth_group_changelist'))

    def test_delegated_staff_cannot_bypass_guards_with_admin_autocomplete(self):
        self.client.force_login(self.operator)
        for query in (
            {'app_label': 'accounts', 'model_name': 'user', 'field_name': 'groups'},
            {'app_label': 'commerce', 'model_name': 'sale', 'field_name': 'created_by'},
        ):
            with self.subTest(query=query):
                response = self.client.get(reverse('admin:autocomplete'), query)
                self.assertEqual(response.status_code, 403)

    def test_crafted_user_posts_cannot_promote_or_change_password_of_any_target(self):
        self.client.force_login(self.operator)
        for target in (self.operator, self.target, self.owner):
            old_password = target.password
            old_version = target.auth_token_version
            old_flags = (target.is_superuser, target.is_staff, target.role)
            payload = {
                'username': target.username, 'role': self.User.ADMIN,
                'is_superuser': 'on', 'is_staff': 'on', 'is_active': 'on',
                'groups': [self.group.pk],
                'user_permissions': list(Permission.objects.values_list('pk', flat=True)),
                'password': '!crafted',
                'password1': 'Attacker-Synthetic-123!',
                'password2': 'Attacker-Synthetic-123!',
                'usable_password': 'true',
            }
            for name in ('admin:accounts_user_change', 'admin:auth_user_password_change'):
                with self.subTest(target=target.username, endpoint=name):
                    response = self.client.post(reverse(name, args=[target.pk]), payload)
                    self.assertEqual(response.status_code, 403)
                    target.refresh_from_db()
                    self.assertEqual(target.password, old_password)
                    self.assertEqual(target.auth_token_version, old_version)
                    self.assertEqual((target.is_superuser, target.is_staff, target.role), old_flags)
                    self.assertFalse(target.groups.filter(pk=self.group.pk).exists())

    def test_crafted_user_add_delete_and_bulk_delete_posts_are_denied(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('admin:accounts_user_add'), {
            'username': 'forbidden-new-superuser', 'role': self.User.ADMIN,
            'is_superuser': 'on', 'is_staff': 'on',
            'password1': 'Attacker-Synthetic-123!', 'password2': 'Attacker-Synthetic-123!',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.User.objects.filter(username='forbidden-new-superuser').exists())
        for name, payload in (
            ('admin:accounts_user_delete', {'post': 'yes'}),
            ('admin:accounts_user_changelist', {
                'action': 'delete_selected', '_selected_action': [self.target.pk], 'post': 'yes',
            }),
        ):
            args = [self.target.pk] if name.endswith('_delete') else []
            with self.subTest(endpoint=name):
                self.assertEqual(self.client.post(reverse(name, args=args), payload).status_code, 403)
                self.assertTrue(self.User.objects.filter(pk=self.target.pk).exists())

    def test_crafted_group_posts_cannot_add_permissions_or_delete_groups(self):
        self.client.force_login(self.operator)
        for name, payload in (
            ('admin:auth_group_add', {'name': 'forbidden-created-group'}),
            ('admin:auth_group_change', {
                'name': 'forbidden-renamed-group',
                'permissions': list(Permission.objects.values_list('pk', flat=True)),
            }),
            ('admin:auth_group_delete', {'post': 'yes'}),
            ('admin:auth_group_changelist', {
                'action': 'delete_selected', '_selected_action': [self.group.pk], 'post': 'yes',
            }),
        ):
            args = [self.group.pk] if name.endswith(('_change', '_delete')) else []
            with self.subTest(endpoint=name):
                self.assertEqual(self.client.post(reverse(name, args=args), payload).status_code, 403)
                self.group.refresh_from_db()
                self.assertEqual(self.group.name, 'Admin security synthetic group')
                self.assertFalse(self.group.permissions.exists())
                self.assertFalse(Group.objects.filter(name='forbidden-created-group').exists())

    def test_superuser_can_still_open_user_and_group_admin_and_edit_group(self):
        self.client.force_login(self.owner)
        for url in self.user_urls(self.target) + self.group_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        permission = Permission.objects.get(content_type__app_label='accounts', codename='view_dashboard')
        response = self.client.post(reverse('admin:auth_group_change', args=[self.group.pk]), {
            'name': 'Edited by superuser', 'permissions': [permission.pk], '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Edited by superuser')
        self.assertTrue(self.group.permissions.filter(pk=permission.pk).exists())

    def test_user_change_form_cannot_replace_password_through_a_crafted_post(self):
        self.client.force_login(self.owner)
        old_hash = self.target.password
        response = self.client.post(reverse('admin:accounts_user_change', args=[self.target.pk]), {
            'username': self.target.username, 'role': self.target.role, 'is_active': 'on',
            'password': '!crafted-unusable-password', 'date_joined_0': '2026-01-01',
            'date_joined_1': '12:00:00', '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, old_hash)
        self.assertEqual(self.target.auth_token_version, 1)

    def test_superuser_can_still_create_user_with_hashed_password(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('admin:accounts_user_add'), {
            'username': 'admin-created-synthetic-user', 'email': 'created@example.test',
            'role': self.User.SELLER, 'password1': 'Cobalt!8427River#6039',
            'password2': 'Cobalt!8427River#6039', '_save': 'Save',
        })
        self.assertEqual(
            response.status_code, 302,
            response.context['adminform'].form.errors if response.status_code == 200 else None,
        )
        created = self.User.objects.get(username='admin-created-synthetic-user')
        self.assertTrue(created.check_password('Cobalt!8427River#6039'))
        self.assertTrue(created.has_usable_password())
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertEqual(created.auth_token_version, 1)

    def test_ordinary_user_change_preserves_concurrent_password_and_token_revocation(self):
        request = RequestFactory().post('/admin/accounts/user/')
        request.user = self.owner
        model_admin = admin.site._registry[self.User]
        form = model_admin.get_form(request, self.target)(
            data=self.user_change_data(first_name='Requested profile update'), instance=self.target,
        )
        self.assertTrue(form.is_valid(), form.errors)
        stale = model_admin.save_form(request, form, change=True)
        concurrent = self.User.objects.get(pk=self.target.pk)
        concurrent.set_password('Concurrent-Synthetic-Password-789!')
        concurrent.auth_token_version = 8
        concurrent.role = self.User.MANAGER
        concurrent.is_staff = True
        concurrent.force_password_change = True
        concurrent.save(update_fields=[
            'password', 'auth_token_version', 'role', 'is_staff', 'force_password_change',
        ])

        model_admin.save_model(request, stale, form, change=True)

        self.target.refresh_from_db()
        self.assertEqual(self.target.first_name, 'Requested profile update')
        self.assertEqual(self.target.password, concurrent.password)
        self.assertTrue(self.target.check_password('Concurrent-Synthetic-Password-789!'))
        self.assertEqual(self.target.auth_token_version, 8)
        self.assertEqual(self.target.role, self.User.MANAGER)
        self.assertTrue(self.target.is_staff)
        self.assertTrue(self.target.force_password_change)

    def test_unchanged_profile_form_does_not_write_stale_user_row(self):
        # The synthetic timestamp has second precision, matching the admin's
        # split widgets, so every concrete field really is unchanged.
        request = RequestFactory().post('/admin/accounts/user/')
        request.user = self.owner
        model_admin = admin.site._registry[self.User]
        form = model_admin.get_form(request, self.target)(
            data=self.user_change_data(), instance=self.target,
        )
        self.assertTrue(form.is_valid(), form.errors)
        stale = model_admin.save_form(request, form, change=True)
        self.assertEqual(form.changed_data, [])
        self.User.objects.filter(pk=self.target.pk).update(auth_token_version=9)
        with self.assertNumQueries(0):
            model_admin.save_model(request, stale, form, change=True)
        self.target.refresh_from_db()
        self.assertEqual(self.target.auth_token_version, 9)

    def test_user_many_to_many_only_edit_is_still_saved_through_normal_admin_http(self):
        self.client.force_login(self.owner)
        permission = Permission.objects.get(content_type__app_label='accounts', codename='view_dashboard')
        response = self.client.post(reverse('admin:accounts_user_change', args=[self.target.pk]),
                                    self.user_change_data(
                                        groups=[self.group.pk], user_permissions=[permission.pk], _save='Save',
                                    ))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.groups.filter(pk=self.group.pk).exists())
        self.assertTrue(self.target.user_permissions.filter(pk=permission.pk).exists())
        self.assertEqual(self.target.auth_token_version, 1)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AdminPasswordTokenRevocationTests(TestCase):
    old_password = 'Old-Synthetic-Password-123!'
    new_password = 'New-Synthetic-Password-456!'

    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.owner = cls.User.objects.create_superuser(
            username='password-admin-owner', password=cls.old_password,
        )
        cls.target = cls.User.objects.create_user(
            username='password-admin-target', password=cls.old_password,
        )

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.client.force_login(self.owner)

    def login_tokens(self, user, password=None):
        response = self.api.post(reverse('api-login'), {
            'username': user.username, 'password': password or self.old_password,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.json()['data']

    def assert_tokens_revoked(self, tokens):
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.api.get(reverse('api-me'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'TOKEN_REVOKED')
        self.api.credentials()
        response = self.api.post(reverse('api-refresh'), {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'TOKEN_REVOKED')

    def assert_tokens_work(self, tokens):
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(self.api.get(reverse('api-me')).status_code, 200)
        self.api.credentials()
        self.assertEqual(self.api.post(
            reverse('api-refresh'), {'refresh': tokens['refresh']}, format='json',
        ).status_code, 200)

    def password_form(self, **data):
        form = UserAdminPasswordChangeForm(self.target, {
            'password1': self.new_password, 'password2': self.new_password,
            'usable_password': 'true', **data,
        })
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_admin_password_change_revokes_old_access_and_refresh_and_new_login_works(self):
        old_tokens = self.login_tokens(self.target)
        response = self.client.post(reverse('admin:auth_user_password_change', args=[self.target.pk]), {
            'password1': self.new_password, 'password2': self.new_password, 'usable_password': 'true',
        })
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(self.new_password))
        self.assertEqual(self.target.auth_token_version, 2)
        self.assert_tokens_revoked(old_tokens)
        self.assert_tokens_work(self.login_tokens(self.target, self.new_password))

    def test_admin_password_reset_does_not_revoke_the_acting_admin_tokens(self):
        owner_tokens = self.login_tokens(self.owner)
        response = self.client.post(reverse('admin:auth_user_password_change', args=[self.target.pk]), {
            'password1': self.new_password, 'password2': self.new_password, 'usable_password': 'true',
        })
        self.assertEqual(response.status_code, 302)
        self.assert_tokens_work(owner_tokens)

    def test_admin_own_password_route_revokes_tokens_and_preserves_only_current_session(self):
        old_tokens = self.login_tokens(self.owner)
        other_browser = Client()
        other_browser.force_login(self.owner)
        self.assertEqual(other_browser.get(reverse('admin:index')).status_code, 200)
        response = self.client.post(reverse('admin:password_change'), {
            'old_password': self.old_password,
            'new_password1': self.new_password, 'new_password2': self.new_password,
        })
        self.assertRedirects(response, reverse('admin:password_change_done'))
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.auth_token_version, 2)
        self.assertTrue(self.owner.check_password(self.new_password))
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        self.assertEqual(other_browser.get(reverse('admin:index')).status_code, 302)
        self.assert_tokens_revoked(old_tokens)
        self.assert_tokens_work(self.login_tokens(self.owner, self.new_password))

    def test_non_superuser_staff_can_change_only_own_password_and_revoke_own_tokens(self):
        operator = self.User.objects.create_user(
            username='password-staff-delegate', password=self.old_password, is_staff=True,
        )
        operator.user_permissions.set(Permission.objects.all())
        old_tokens = self.login_tokens(operator)
        self.client.force_login(operator)
        response = self.client.post(reverse('admin:password_change'), {
            'old_password': self.old_password,
            'new_password1': self.new_password, 'new_password2': self.new_password,
        })
        self.assertRedirects(response, reverse('admin:password_change_done'))
        operator.refresh_from_db()
        self.assertFalse(operator.is_superuser)
        self.assertTrue(operator.check_password(self.new_password))
        self.assertEqual(operator.auth_token_version, 2)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:accounts_user_change', args=[operator.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:auth_group_changelist')).status_code, 403)
        self.assert_tokens_revoked(old_tokens)
        self.assert_tokens_work(self.login_tokens(operator, self.new_password))

    def test_both_admin_password_routes_require_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        for name, args, payload in (
            ('admin:password_change', [], {
                'old_password': self.old_password,
                'new_password1': self.new_password, 'new_password2': self.new_password,
            }),
            ('admin:auth_user_password_change', [self.target.pk], {
                'password1': self.new_password, 'password2': self.new_password, 'usable_password': 'true',
            }),
        ):
            with self.subTest(endpoint=name):
                self.assertEqual(csrf_client.post(reverse(name, args=args), payload).status_code, 403)
        for user in (self.owner, self.target):
            user.refresh_from_db()
            self.assertTrue(user.check_password(self.old_password))
            self.assertEqual(user.auth_token_version, 1)

    def test_own_password_form_commit_false_preserves_database_and_tokens(self):
        form = AdminSelfPasswordChangeForm(self.owner, {
            'old_password': self.old_password,
            'new_password1': self.new_password, 'new_password2': self.new_password,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save(commit=False).check_password(self.new_password))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(self.old_password))
        self.assertEqual(self.owner.auth_token_version, 1)

    def test_failed_own_password_token_revocation_rolls_password_back(self):
        form = AdminSelfPasswordChangeForm(self.owner, {
            'old_password': self.old_password,
            'new_password1': self.new_password, 'new_password2': self.new_password,
        })
        self.assertTrue(form.is_valid(), form.errors)
        with patch.object(self.User, 'revoke_api_tokens', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                form.save()
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(self.old_password))
        self.assertEqual(self.owner.auth_token_version, 1)

    def test_admin_disabling_password_also_revokes_existing_tokens(self):
        old_tokens = self.login_tokens(self.target)
        response = self.client.post(reverse('admin:auth_user_password_change', args=[self.target.pk]), {
            'usable_password': 'false', 'unset-password': 'Disable password',
        })
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.has_usable_password())
        self.assertEqual(self.target.auth_token_version, 2)
        self.assert_tokens_revoked(old_tokens)

    def test_password_change_commit_false_does_not_persist_or_revoke_tokens(self):
        old_tokens = self.login_tokens(self.target)
        old_hash = self.target.password
        result = self.password_form().save(commit=False)
        self.assertIs(result, self.target)
        self.assertTrue(result.check_password(self.new_password))
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, old_hash)
        self.assertEqual(self.target.auth_token_version, 1)
        self.assert_tokens_work(old_tokens)

    def test_disable_password_commit_false_does_not_persist_or_revoke_tokens(self):
        old_hash = self.target.password
        form = self.password_form(usable_password='false', password1='', password2='')
        self.assertFalse(form.save(commit=False).has_usable_password())
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, old_hash)
        self.assertEqual(self.target.auth_token_version, 1)

    def test_failed_token_revocation_rolls_password_change_back(self):
        old_hash = self.target.password
        form = self.password_form()
        with patch.object(self.User, 'revoke_api_tokens', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                form.save()
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, old_hash)
        self.assertEqual(self.target.auth_token_version, 1)

    def test_failed_token_revocation_rolls_password_disabling_back(self):
        old_hash = self.target.password
        form = self.password_form(usable_password='false', password1='', password2='')
        with patch.object(self.User, 'revoke_api_tokens', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                form.save()
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, old_hash)
        self.assertEqual(self.target.auth_token_version, 1)

    def test_invalid_password_submission_changes_neither_password_nor_token_version(self):
        old_hash = self.target.password
        response = self.client.post(reverse('admin:auth_user_password_change', args=[self.target.pk]), {
            'password1': self.new_password, 'password2': 'Different-Synthetic-123!', 'usable_password': 'true',
        })
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, old_hash)
        self.assertEqual(self.target.auth_token_version, 1)

    def test_password_save_preserves_concurrent_profile_and_token_version_changes(self):
        form = self.password_form()
        self.User.objects.filter(pk=self.target.pk).update(first_name='Concurrent change', auth_token_version=7)
        form.save()
        self.target.refresh_from_db()
        self.assertEqual(self.target.first_name, 'Concurrent change')
        self.assertEqual(self.target.auth_token_version, 8)
        self.assertTrue(self.target.check_password(self.new_password))
