from unittest.mock import patch
import re
from importlib.util import find_spec
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connections, transaction
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.core.backup_forms import BackupDriveForm
from apps.core.backup_views import SESSION_RESULT_KEY
from apps.core.drive_backup import DriveBackupError, ERRORS
from apps.core.drive_oauth import status as actual_oauth_status, runtime_config as actual_runtime_config


READY = {'mode_enabled': True, 'available': True, 'connected': True,
         'account': {'email': 'test@example.invalid', 'display_name': 'Synthetic account'},
         'destination_token': 'd' * 64, 'issues': []}
NOT_READY = {
    'mode_enabled': False, 'available': False, 'connected': False, 'account': None,
    'issues': ['Configuration de test indisponible.'],
}
RESULT = {
    'name': 'elamine-database-test.dump.gpg',
    'sha256': 'a' * 64,
    'size': 1024,
    'scope': 'database_only',
    'restore_verified': False,
}
PASSWORD = 'Synthetic-Backup-Password-Only-936!'
CSP_POLICY = ("default-src 'self'; script-src 'self' 'nonce-public-test'; "
              "img-src 'self' data:; form-action 'self'; object-src 'none'; frame-ancestors 'none'")


class BackupDriveFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='drive-form-test', password=PASSWORD,
        )

    def test_current_password_and_scope_confirmation_are_required(self):
        form = BackupDriveForm({}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('current_password', form.errors)
        self.assertIn('confirm_database_only', form.errors)

    def test_wrong_password_is_rejected(self):
        form = BackupDriveForm({
            'current_password': 'incorrect-test-password',
            'confirm_database_only': 'on',
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('current_password', form.errors)

    def test_correct_password_without_confirmation_is_rejected(self):
        form = BackupDriveForm({'current_password': PASSWORD}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('confirm_database_only', form.errors)

    def test_correct_confirmation_is_valid_without_repopulating_password(self):
        form = BackupDriveForm({
            'current_password': PASSWORD, 'confirm_database_only': 'on',
        }, user=self.user)
        self.assertTrue(form.is_valid())
        self.assertNotIn(PASSWORD, form.as_p())

    def test_user_without_usable_password_cannot_authorize_export(self):
        self.user.set_unusable_password()
        form = BackupDriveForm({
            'current_password': PASSWORD, 'confirm_database_only': 'on',
        }, user=self.user)
        self.assertFalse(form.is_valid())

    def test_csrf_field_is_permitted_but_provider_parameters_are_rejected(self):
        base = {'current_password': PASSWORD, 'confirm_database_only': 'on'}
        self.assertTrue(BackupDriveForm(base | {'csrfmiddlewaretoken': 'synthetic'}, user=self.user).is_valid())
        for field in ('source', 'folder', 'token', 'url'):
            with self.subTest(field=field):
                form = BackupDriveForm(base | {field: 'not-authorized'}, user=self.user)
                self.assertFalse(form.is_valid())
                self.assertNotIn('not-authorized', str(form.errors))


class DriveBackupViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser(username='drive-web-admin', password=PASSWORD)
        cls.reader = User.objects.create_user(username='drive-web-reader', password=PASSWORD)
        cls.creator = User.objects.create_user(username='drive-web-creator', password=PASSWORD)
        cls.viewer_creator = User.objects.create_user(username='drive-web-operator', password=PASSWORD)
        cls.seller = User.objects.create_user(username='drive-web-seller', password=PASSWORD)
        cls.view_permission = Permission.objects.get(
            content_type__app_label='accounts', codename='view_backups',
        )
        cls.create_permission = Permission.objects.get(
            content_type__app_label='accounts', codename='create_backups',
        )
        cls.reader.user_permissions.add(cls.view_permission)
        cls.creator.user_permissions.add(cls.create_permission)
        cls.viewer_creator.user_permissions.add(cls.view_permission, cls.create_permission)

    def setUp(self):
        self.availability = self.enterContext(patch(
            'apps.core.backup_views.drive_oauth.status', return_value=READY.copy(),
        ))
        self.server_config = object()
        self.session_configuration = self.enterContext(patch(
            'apps.core.backup_views.drive_oauth.runtime_config', return_value=self.server_config,
        ))
        self.create_backup = self.enterContext(patch(
            'apps.core.backup_views.drive_backup.create_drive_backup', return_value=RESULT.copy(),
        ))
        self.audit = self.enterContext(patch('apps.core.backup_views.log_security_event'))
        self.rate_limit = self.enterContext(patch(
            'apps.core.backup_views.is_event_rate_limited', return_value=False,
        ))
        self.client.force_login(self.admin)
        self.payload = {'current_password': PASSWORD, 'confirm_database_only': 'on', 'drive_destination': 'd' * 64}

    def test_routes_are_isolated_under_core(self):
        self.assertEqual(reverse('backup_list'), '/core/backups/')
        self.assertEqual(reverse('backup_create'), '/core/backups/create/')

    def test_backup_create_is_explicitly_non_atomic(self):
        from apps.core.backup_views import backup_create
        self.assertIn('default', backup_create._non_atomic_requests)

    @override_settings(DRIVE_BACKUP_LOCAL_CONNECTION_TEST=True)
    def test_connection_only_mode_never_creates_archive_even_when_connected(self):
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 503)
        self.create_backup.assert_not_called()
        self.session_configuration.assert_not_called()

    def test_anonymous_requests_require_login_and_never_export(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('backup_list')).status_code, 302)
        self.assertEqual(self.client.post(reverse('backup_create'), self.payload).status_code, 302)
        self.create_backup.assert_not_called()

    def test_seller_cannot_view_or_export(self):
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse('backup_list')).status_code, 403)
        self.assertEqual(self.client.post(reverse('backup_create'), self.payload).status_code, 403)
        self.create_backup.assert_not_called()

    def test_reader_can_view_but_cannot_export(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse('backup_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'action="' + reverse('backup_create') + '"')
        self.assertEqual(self.client.post(reverse('backup_create'), self.payload).status_code, 403)
        self.create_backup.assert_not_called()

    def test_create_permission_without_view_is_insufficient(self):
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(reverse('backup_list')).status_code, 403)
        self.assertEqual(self.client.post(reverse('backup_create'), self.payload).status_code, 403)
        self.create_backup.assert_not_called()

    def test_non_superuser_with_both_permissions_can_export(self):
        self.client.force_login(self.viewer_creator)
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertRedirects(response, reverse('backup_list'), fetch_redirect_response=False)
        self.create_backup.assert_called_once_with(config=self.server_config)

    def test_explicit_permission_denial_overrides_a_role_grant(self):
        group = Group.objects.create(name='Synthetic backup operator')
        group.permissions.add(self.view_permission, self.create_permission)
        self.seller.groups.add(group)
        self.seller.denied_permissions.add(self.create_permission)
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse('backup_list')).status_code, 200)
        self.assertEqual(self.client.post(reverse('backup_create'), self.payload).status_code, 403)
        self.create_backup.assert_not_called()

    def test_get_never_exports_and_explains_the_limited_scope(self):
        response = self.client.get(reverse('backup_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'médias exclus')
        self.assertContains(response, 'aucun traitement en arrière-plan')
        self.assertContains(response, 'Restauration', count=0)
        self.assertContains(response, reverse('backup_create'))
        self.create_backup.assert_not_called()

    def test_create_get_is_method_not_allowed(self):
        response = self.client.get(reverse('backup_create'))
        self.assertEqual(response.status_code, 405)
        self.create_backup.assert_not_called()

    def test_list_post_is_method_not_allowed(self):
        response = self.client.post(reverse('backup_list'), self.payload)
        self.assertEqual(response.status_code, 405)
        self.create_backup.assert_not_called()

    def test_page_and_create_responses_are_not_cacheable(self):
        for response in (
            self.client.get(reverse('backup_list')),
            self.client.get(reverse('backup_create')),
        ):
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertIn('private', response.headers['Cache-Control'])

    def test_disabled_configuration_disables_button_without_export(self):
        self.availability.return_value = NOT_READY.copy()
        response = self.client.get(reverse('backup_list'))
        self.assertContains(response, 'disabled')
        self.assertContains(response, 'désactivée par défaut')
        self.create_backup.assert_not_called()

    def test_disabled_post_is_refused_even_with_correct_confirmation(self):
        self.availability.return_value = NOT_READY.copy()
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, PASSWORD, status_code=503)
        self.assertNotIn(SESSION_RESULT_KEY, self.client.session)
        self.create_backup.assert_not_called()

    def test_invalid_password_never_reaches_backup_service_or_session(self):
        invalid = 'Incorrect-Synthetic-Password-862!'
        response = self.client.post(reverse('backup_create'), {
            'current_password': invalid, 'confirm_database_only': 'on',
        })
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, invalid, status_code=400)
        self.assertNotIn(invalid, repr(dict(self.client.session)))
        self.create_backup.assert_not_called()

    def test_missing_scope_confirmation_does_not_export(self):
        response = self.client.post(reverse('backup_create'), {'current_password': PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, PASSWORD, status_code=400)
        self.create_backup.assert_not_called()

    def test_unexpected_provider_parameters_never_reach_backup_service(self):
        injected_value = 'https://sensitive.invalid/credential-must-not-leak'
        for field in ('source', 'folder', 'token', 'url'):
            with self.subTest(field=field):
                response = self.client.post(reverse('backup_create'), self.payload | {field: injected_value})
                self.assertEqual(response.status_code, 400)
                self.assertNotContains(response, injected_value, status_code=400)
        self.create_backup.assert_not_called()

    def test_repeated_failed_confirmations_are_throttled_before_password_check(self):
        self.rate_limit.return_value = True
        with patch.object(BackupDriveForm, 'is_valid') as validate:
            response = self.client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 429)
        self.assertNotContains(response, PASSWORD, status_code=429)
        self.rate_limit.assert_called_once()
        self.assertEqual(self.rate_limit.call_args.args[1:], ('backup.drive.request_rejected', 5, 900))
        validate.assert_not_called()
        self.create_backup.assert_not_called()

    def test_csrf_is_required_even_with_valid_password_and_permissions(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response = csrf_client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 403)
        self.create_backup.assert_not_called()

    def test_success_stores_only_allowed_metadata_and_displays_it_once(self):
        self.create_backup.return_value = RESULT | {
            'access_token': 'SECRET-MUST-NOT-BE-RETAINED',
            'download_url': 'https://example.invalid/private',
        }
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertRedirects(response, reverse('backup_list'), fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_RESULT_KEY], RESULT)
        self.assertNotIn(PASSWORD, repr(dict(self.client.session)))
        displayed = self.client.get(reverse('backup_list'))
        self.assertContains(displayed, RESULT['name'])
        self.assertContains(displayed, RESULT['sha256'])
        self.assertContains(displayed, 'Restauration non testée')
        self.assertNotContains(displayed, 'SECRET-MUST-NOT-BE-RETAINED')
        self.assertNotContains(displayed, 'https://example.invalid/private')
        self.assertNotIn(SESSION_RESULT_KEY, self.client.session)
        repeated = self.client.get(reverse('backup_list'))
        self.assertNotContains(repeated, RESULT['name'])

    def test_service_failure_never_displays_or_records_provider_diagnostics(self):
        sensitive_diagnostic = 'secret-refresh-token-or-password-must-not-leak'
        self.create_backup.side_effect = RuntimeError(sensitive_diagnostic)
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, sensitive_diagnostic, status_code=503)
        self.assertNotIn(sensitive_diagnostic, repr(dict(self.client.session)))
        self.assertNotIn(sensitive_diagnostic, repr(self.audit.call_args_list))
        self.assertNotIn(SESSION_RESULT_KEY, self.client.session)

    def test_operational_failures_use_only_fixed_allowlisted_messages(self):
        for code in ('tools', 'postgres_version', 'too_large', 'integrity', 'drive_auth', 'timeout'):
            with self.subTest(code=code):
                self.create_backup.side_effect = DriveBackupError(code)
                response = self.client.post(reverse('backup_create'), self.payload)
                self.assertContains(response, ERRORS[code], status_code=503)
                self.assertNotIn(SESSION_RESULT_KEY, self.client.session)

    def test_untrusted_error_code_and_diagnostic_are_never_echoed(self):
        sensitive = 'provider-secret-must-not-escape-the-allowlist'
        for error in (DriveBackupError(sensitive), DriveBackupError('tools')):
            with self.subTest(error_type=type(error).__name__):
                error.code = sensitive
                error.args = (sensitive,)
                self.create_backup.side_effect = error
                response = self.client.post(reverse('backup_create'), self.payload)
                self.assertEqual(response.status_code, 503)
                self.assertNotContains(response, sensitive, status_code=503)
                self.assertNotIn(sensitive, repr(dict(self.client.session)))
                self.assertNotIn(sensitive, repr(self.audit.call_args_list))

    def test_rejected_request_clears_stale_success(self):
        session = self.client.session
        session[SESSION_RESULT_KEY] = RESULT.copy()
        session.save()
        self.client.post(reverse('backup_create'), {})
        self.assertNotIn(SESSION_RESULT_KEY, self.client.session)
        self.create_backup.assert_not_called()

    def test_invalid_result_metadata_cannot_be_mistaken_for_success(self):
        for change in (
            {'scope': 'complete'}, {'restore_verified': True},
            {'name': '../private.gpg'}, {'name': 'plaintext.dump'},
            {'sha256': 'invalid'}, {'size': True}, {'size': 0},
        ):
            with self.subTest(change=change):
                self.create_backup.return_value = RESULT | change
                response = self.client.post(reverse('backup_create'), self.payload)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(SESSION_RESULT_KEY, self.client.session)

    def test_invalid_stored_metadata_is_ignored(self):
        session = self.client.session
        session[SESSION_RESULT_KEY] = {'name': 'private.gpg'}
        session.save()
        response = self.client.get(reverse('backup_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'private.gpg')
        self.create_backup.assert_not_called()

    def test_sidebar_link_respects_view_permission(self):
        for user, expected in ((self.reader, True), (self.seller, False)):
            request = RequestFactory().get('/core/backups/')
            request.user = user
            html = render_to_string('base/base.html', {'user': user}, request=request)
            self.assertEqual('href="' + reverse('backup_list') + '"' in html, expected)

    def oauth_state(self, connected=False):
        return self.enterContext(patch('apps.core.backup_views.drive_oauth.status', return_value={
            'mode_enabled': True, 'available': True, 'connected': connected,
            'account': {'email': 'synthetic-account@example.invalid', 'display_name': 'Synthetic account'} if connected else {},
            'destination_token': 'd' * 64 if connected else '',
            'issues': [] if connected else ['Connexion Google requise.'],
        }))

    def test_oauth_mode_requires_session_connection_without_legacy_fallback(self):
        self.oauth_state()
        runtime = self.enterContext(patch('apps.core.backup_views.drive_oauth.runtime_config'))
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 503)
        self.create_backup.assert_not_called()
        runtime.assert_not_called()

    def test_connected_mode_uses_only_server_session_configuration(self):
        self.oauth_state(connected=True)
        server_config = object()
        runtime = self.enterContext(patch('apps.core.backup_views.drive_oauth.runtime_config', return_value=server_config))
        response = self.client.post(reverse('backup_create'), self.payload)
        self.assertEqual(response.status_code, 302)
        runtime.assert_called_once()
        self.assertEqual(runtime.call_args.kwargs['expected_destination'], self.payload['drive_destination'])
        self.create_backup.assert_called_once_with(config=server_config)

    def test_oauth_panel_displays_destination_and_escapes_account_metadata(self):
        state = self.oauth_state(connected=True)
        state.return_value['account']['display_name'] = '<script>synthetic-only</script>'
        response = self.client.get(reverse('backup_list'))
        self.assertContains(response, 'synthetic-account@example.invalid')
        self.assertContains(response, 'Changer de compte Google Drive')
        self.assertContains(response, 'sélection d’un autre dossier')
        self.assertNotContains(response, '<script>synthetic-only</script>')
        self.assertContains(response, '&lt;script&gt;synthetic-only&lt;/script&gt;')
        self.assertContains(response, 'id_drive-current_password')
        self.assertNotContains(response, 'id_drive-confirm_database_only')
        self.assertContains(response, 'name="drive_destination"')

    def test_connect_requires_post_and_does_not_start_export(self):
        begin = self.enterContext(patch('apps.core.backup_views.drive_oauth.begin', return_value='https://accounts.google.com/o/oauth2/v2/auth?state=synthetic'))
        self.assertEqual(self.client.get(reverse('backup_drive_connect')).status_code, 405)
        response = self.client.post(reverse('backup_drive_connect'), {'drive-current_password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://accounts.google.com/'))
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        begin.assert_called_once()
        self.create_backup.assert_not_called()

    @override_settings(SECURITY_RESPONSE_HEADERS={
        'Content-Security-Policy': CSP_POLICY, 'Permissions-Policy': 'camera=()',
    }, SECURE_REFERRER_POLICY='no-referrer')
    def test_backup_page_and_oauth_redirect_have_targeted_csp_after_middleware(self):
        target = 'https://accounts.google.com/o/oauth2/v2/auth?state=public-synthetic'
        begin = self.enterContext(patch('apps.core.backup_views.drive_oauth.begin', return_value=target))
        page = self.client.get(reverse('backup_list'))
        start = self.client.post(reverse('backup_drive_connect'), {'drive-current_password': PASSWORD})
        expected = CSP_POLICY.replace("form-action 'self'", "form-action 'self' https://accounts.google.com")
        for response in (page, start):
            self.assertEqual(response['Content-Security-Policy'], expected)
            self.assertEqual(response['Permissions-Policy'], 'camera=()')
            self.assertNotIn('https://*.google.com', response['Content-Security-Policy'])
            self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertEqual(start['Referrer-Policy'], 'no-referrer')
        self.assertEqual(start['Location'], target)
        # The shared middleware must still emit the original policy elsewhere.
        other = self.client.get(reverse('backup_drive_disconnect'))
        self.assertEqual(other.status_code, 405)
        self.assertEqual(other['Content-Security-Policy'], CSP_POLICY)
        begin.assert_called_once()
        self.create_backup.assert_not_called()

    def test_targeted_csp_preserves_other_directives_and_all_existing_policies(self):
        policies = (
            "default-src 'none'; frame-ancestors 'none'",
            "default-src 'self'; form-action 'none'; object-src 'none'",
            "form-action 'self' https://accounts.google.com; script-src 'none'",
            "form-action 'self'; form-action 'self'; base-uri 'none'",
            "default-src 'self'; form-action 'self',script-src 'none'; form-action 'self'",
        )
        for policy in policies:
            with self.subTest(policy=policy), override_settings(SECURITY_RESPONSE_HEADERS={
                    'Content-Security-Policy': policy}):
                response = self.client.get(reverse('backup_list'))
                before, after = policy.split(','), response['Content-Security-Policy'].split(',')
                self.assertEqual(len(before), len(after))
                for old_policy, new_policy in zip(before, after):
                    old_directives = [part.strip() for part in old_policy.split(';') if part.strip()]
                    new_directives = [part.strip() for part in new_policy.split(';') if part.strip()]
                    self.assertEqual(
                        [part for part in old_directives if part.split()[0] != 'form-action'],
                        [part for part in new_directives if part.split()[0] != 'form-action'],
                    )
                    for directive in new_directives:
                        if directive.split()[0] == 'form-action':
                            self.assertEqual(directive.split().count('https://accounts.google.com'), 1)
                            self.assertNotIn("'none'", directive.split())
                # Settings must not be mutated by rendering a targeted response.
                from django.conf import settings
                self.assertEqual(settings.SECURITY_RESPONSE_HEADERS['Content-Security-Policy'], policy)

    @override_settings(SECURITY_RESPONSE_HEADERS={'Content-Security-Policy': CSP_POLICY})
    def test_form_error_keeps_targeted_csp_and_usable_same_origin_referrer(self):
        response = self.client.post(reverse('backup_drive_connect'), {'drive-current_password': 'wrong'})
        self.assertEqual(response.status_code, 400)
        self.assertIn("form-action 'self' https://accounts.google.com;", response['Content-Security-Policy'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.create_backup.assert_not_called()

    def test_connect_checks_password_and_rejects_injected_parameters(self):
        begin = self.enterContext(patch('apps.core.backup_views.drive_oauth.begin'))
        for body in ({}, {'drive-current_password': 'wrong'},
                     {'drive-current_password': PASSWORD, 'redirect_uri': 'https://evil.invalid'}):
            with self.subTest(fields=sorted(body)):
                response = self.client.post(reverse('backup_drive_connect'), body)
                self.assertEqual(response.status_code, 400)
                self.assertNotContains(response, PASSWORD, status_code=400)
        begin.assert_not_called()
        self.create_backup.assert_not_called()

    def test_connect_is_rate_limited_before_password_validation(self):
        self.rate_limit.return_value = True
        begin = self.enterContext(patch('apps.core.backup_views.drive_oauth.begin'))
        check = self.enterContext(patch('apps.core.backup_forms.BackupDriveForm.clean_current_password'))
        self.assertEqual(self.client.post(reverse('backup_drive_connect'), {'drive-current_password': PASSWORD}).status_code, 429)
        check.assert_not_called()
        begin.assert_not_called()

    def test_oauth_mutations_require_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        begin = self.enterContext(patch('apps.core.backup_views.drive_oauth.begin'))
        disconnect = self.enterContext(patch('apps.core.backup_views.drive_oauth.disconnect'))
        for name in ('backup_drive_connect', 'backup_drive_disconnect'):
            self.assertEqual(csrf_client.post(reverse(name), {'drive-current_password': PASSWORD}).status_code, 403)
        begin.assert_not_called()
        disconnect.assert_not_called()

    def test_oauth_endpoints_require_both_rbac_permissions(self):
        begin = self.enterContext(patch('apps.core.backup_views.drive_oauth.begin'))
        complete = self.enterContext(patch('apps.core.backup_views.drive_oauth.complete'))
        disconnect = self.enterContext(patch('apps.core.backup_views.drive_oauth.disconnect'))
        for user in (self.reader, self.creator, self.seller):
            self.client.force_login(user)
            for name in ('backup_drive_connect', 'backup_drive_disconnect'):
                self.assertEqual(self.client.post(reverse(name), {'drive-current_password': PASSWORD}).status_code, 403)
            self.assertEqual(self.client.get(reverse('backup_drive_callback'), {'code': 'synthetic', 'state': 'synthetic'}).status_code, 403)
        begin.assert_not_called()
        complete.assert_not_called()
        disconnect.assert_not_called()

    def test_callback_cleans_url_and_never_exports(self):
        from apps.core.backup_views import backup_drive_callback
        self.assertIn('default', backup_drive_callback._non_atomic_requests)
        complete = self.enterContext(patch('apps.core.backup_views.drive_oauth.complete'))
        response = self.client.get(reverse('backup_drive_callback'), {'code': 'synthetic-sensitive-code', 'state': 'synthetic-state'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('backup_list'))
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.session['drive_connection_notice'], 'connected')
        self.assertNotIn('synthetic-sensitive-code', repr(dict(self.client.session)))
        # The audit function accepts the request, but persists/logs only its path.
        # Inspect the actual stored/logged values, not a Mock's request repr.
        from apps.core.models import AuditLog
        from apps.core.security import log_security_event
        audited_request = self.audit.call_args.args[0]
        with patch('apps.core.security.security_logger') as safe_logger:
            log_security_event(audited_request, 'backup.drive.oauth_connected')
        self.assertEqual(AuditLog.objects.latest('pk').path, reverse('backup_drive_callback'))
        self.assertNotIn('synthetic-sensitive-code', repr(safe_logger.method_calls))
        complete.assert_called_once()
        self.create_backup.assert_not_called()

    def test_anonymous_and_inactive_callback_login_next_never_contains_oauth_query(self):
        complete = self.enterContext(patch('apps.core.backup_views.drive_oauth.complete'))
        query = {'code': 'public-code-never-repeat-in-next', 'state': 'public-state-never-repeat',
                 'userinfo': 'untrusted-userinfo-never-repeat'}
        self.client.logout()
        anonymous = self.client.get(reverse('backup_drive_callback'), query)
        self.client.force_login(self.viewer_creator)
        get_user_model().objects.filter(pk=self.viewer_creator.pk).update(is_active=False)
        inactive = self.client.get(reverse('backup_drive_callback'), query)
        for response in (anonymous, inactive):
            self.assertEqual(response.status_code, 302)
            self.assertEqual(parse_qs(urlsplit(response['Location']).query).get('next'), [reverse('backup_list')])
            self.assertEqual(response['Referrer-Policy'], 'no-referrer')
            self.assertIn('no-store', response['Cache-Control'])
            for value in query.values():
                self.assertNotIn(value, str(response.headers))
                self.assertNotIn(value, response.content.decode())
        complete.assert_not_called()
        self.create_backup.assert_not_called()

    def test_callback_permission_denial_is_private_and_does_not_echo_oauth_query(self):
        complete = self.enterContext(patch('apps.core.backup_views.drive_oauth.complete'))
        self.client.force_login(self.reader)
        query = {'code': 'public-denied-code', 'state': 'public-denied-state', 'userinfo': 'public-denied-info'}
        response = self.client.get(reverse('backup_drive_callback'), query)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])
        for value in query.values():
            self.assertNotIn(value, str(response.headers))
            self.assertNotIn(value, response.content.decode())
        complete.assert_not_called()
        self.create_backup.assert_not_called()

    def test_callback_remains_get_only_and_non_atomic_after_outer_safety_wrapper(self):
        from apps.core.backup_views import backup_drive_callback
        self.assertIn('default', backup_drive_callback._non_atomic_requests)
        complete = self.enterContext(patch('apps.core.backup_views.drive_oauth.complete'))
        response = self.client.post(reverse('backup_drive_callback'), {'code': 'public-invalid-method'})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response['Allow'], 'GET')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])
        complete.assert_not_called()
        self.create_backup.assert_not_called()

    @override_settings(SECURE_REFERRER_POLICY='same-origin', SECURITY_RESPONSE_HEADERS={
        'Content-Security-Policy': CSP_POLICY,
    })
    def test_force_password_change_short_circuit_protects_callback_without_bypass(self):
        complete = self.enterContext(patch('apps.core.backup_views.drive_oauth.complete'))
        get_user_model().objects.filter(pk=self.admin.pk).update(force_password_change=True)
        query = {'code': 'public-force-password-code', 'state': 'public-force-password-state'}
        response = self.client.get(reverse('backup_drive_callback'), query)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('profile'))
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Content-Security-Policy'], CSP_POLICY)
        for value in query.values():
            self.assertNotIn(value, str(response.headers))
            self.assertNotIn(value, response.content.decode())
        # The same password-change redirect on another route remains untouched.
        other = self.client.get(reverse('backup_list'))
        self.assertEqual(other.status_code, 302)
        self.assertEqual(other['Location'], reverse('profile'))
        self.assertEqual(other['Referrer-Policy'], 'same-origin')
        self.assertNotIn('no-store', other.get('Cache-Control', ''))
        self.assertEqual(other['Content-Security-Policy'], CSP_POLICY)
        complete.assert_not_called()
        self.create_backup.assert_not_called()

    def test_disconnect_is_local_confirmed_post_without_export(self):
        disconnect = self.enterContext(patch('apps.core.backup_views.drive_oauth.disconnect'))
        self.assertEqual(self.client.get(reverse('backup_drive_disconnect')).status_code, 405)
        self.assertEqual(self.client.post(reverse('backup_drive_disconnect'), {'drive-current_password': 'wrong'}).status_code, 400)
        disconnect.assert_not_called()
        self.assertEqual(self.client.post(reverse('backup_drive_disconnect'), {'drive-current_password': PASSWORD}).status_code, 302)
        disconnect.assert_called_once()
        self.create_backup.assert_not_called()

    def test_notice_is_allowlisted_and_consumed_once(self):
        self.oauth_state()
        session = self.client.session
        session['drive_connection_notice'] = '<script>untrusted</script>'
        session.save()
        self.assertNotContains(self.client.get(reverse('backup_list')), '<script>untrusted</script>')
        self.assertNotIn('drive_connection_notice', self.client.session)

    def test_local_browser_flow_connects_the_vault_then_confirms_exact_destination(self):
        if find_spec('cryptography') is None:
            self.skipTest('Optional Drive crypto dependency is required for the local browser-flow test.')
        from django.test import override_settings
        from apps.core.tests.test_drive_oauth import CONFIG, TOKEN_RESPONSE, IDENTITY, CODE
        with override_settings(**CONFIG), patch(
            'apps.core.backup_views.drive_oauth.status', side_effect=actual_oauth_status,
        ), patch('apps.core.backup_views.drive_oauth.runtime_config', side_effect=actual_runtime_config), patch(
            'apps.core.drive_backup.DriveClient._request', side_effect=[TOKEN_RESPONSE.copy(), IDENTITY.copy()],
        ) as google:
            opened = self.client.get(reverse('backup_list'))
            self.assertContains(opened, 'Aucun compte Drive connecté')
            start = self.client.post(reverse('backup_drive_connect'), {'drive-current_password': PASSWORD})
            self.assertEqual(start.status_code, 302)
            selected = urlsplit(start['Location'])
            self.assertEqual(selected.netloc, 'accounts.google.com')
            params = parse_qs(selected.query)
            self.assertEqual(params['prompt'], ['consent select_account'])
            self.create_backup.assert_not_called()
            callback = self.client.get(reverse('backup_drive_callback'), {'state': params['state'][0], 'code': CODE})
            self.assertEqual(callback.status_code, 302)
            page = self.client.get(reverse('backup_list'))
            self.assertContains(page, IDENTITY['user']['emailAddress'])
            self.assertNotContains(page, TOKEN_RESPONSE['refresh_token'])
            match = re.search(r'name="drive_destination" value="([0-9a-f]{64})"', page.content.decode())
            self.assertIsNotNone(match)
            bad = self.client.post(reverse('backup_create'), self.payload | {'drive_destination': 'f' * 64})
            self.assertEqual(bad.status_code, 503)
            self.create_backup.assert_not_called()
            success = self.client.post(reverse('backup_create'), self.payload | {'drive_destination': match[1]})
            self.assertEqual(success.status_code, 302)
            self.create_backup.assert_called_once()
            config = self.create_backup.call_args.kwargs['config']
            self.assertEqual(config.refresh_token, TOKEN_RESPONSE['refresh_token'])
            self.assertEqual(config.expected_permission_id, IDENTITY['user']['permissionId'])
            self.assertEqual(google.call_count, 2)


class DriveBackupAtomicRequestTests(TransactionTestCase):
    def test_export_post_remains_outside_atomic_requests(self):
        from django.core.handlers.base import BaseHandler
        from django.urls import resolve

        user = get_user_model().objects.create_superuser(
            username='synthetic-drive-atomic-request', password=PASSWORD,
        )
        self.client.force_login(user)
        server_config = object()
        observed = []

        def export(*, config):
            self.assertIs(config, server_config)
            observed.append(transaction.get_connection().in_atomic_block)
            self.assertFalse(observed[-1])
            return RESULT.copy()

        # Patch the effective connection settings used by make_view_atomic,
        # without replacing DATABASES or inheriting TestCase's outer atomic block.
        with patch.dict(connections.settings['default'], {'ATOMIC_REQUESTS': True}), \
                patch('apps.core.backup_views.drive_oauth.status', return_value=READY.copy()), \
                patch('apps.core.backup_views.drive_oauth.runtime_config', return_value=server_config), \
                patch('apps.core.backup_views.is_event_rate_limited', return_value=False), \
                patch('apps.core.backup_views.log_security_event'), \
                patch('apps.core.backup_views.drive_backup.create_drive_backup', side_effect=export) as create:
            self.assertFalse(transaction.get_connection().in_atomic_block)
            self.assertTrue(connections.settings['default']['ATOMIC_REQUESTS'])
            ordinary_view = lambda _request: transaction.get_connection().in_atomic_block
            self.assertTrue(BaseHandler().make_view_atomic(ordinary_view)(None))
            self.assertIn('default', resolve(reverse('backup_create')).func._non_atomic_requests)
            response = self.client.post(reverse('backup_create'), {
                'current_password': PASSWORD, 'confirm_database_only': 'on', 'drive_destination': 'd' * 64,
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('backup_list'))
        self.assertEqual(observed, [False])
        create.assert_called_once_with(config=server_config)
