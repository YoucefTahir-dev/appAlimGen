"""Only public fixtures: no private JSON, real Google call or native process."""
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit, parse_qs
from wsgiref.util import setup_testing_defaults

from django.test import SimpleTestCase, TransactionTestCase, override_settings

from apps.core import drive_backup
from apps.core.tests.test_drive_oauth import CONFIG, TOKEN_RESPONSE, IDENTITY, CODE
from scripts import launch_drive_oauth_local as launcher


WEB = {'project_id': launcher.PROJECT, 'client_id': 'synthetic.apps.googleusercontent.com',
       'client_secret': 'synthetic-only-client-secret-not-real',
       'redirect_uris': [launcher.ORIGIN + launcher.CALLBACK]}


class LauncherConfigurationTests(SimpleTestCase):
    def read_fixture(self, value):
        with tempfile.TemporaryDirectory(prefix='synthetic-oauth-client-') as temporary:
            path = Path(temporary) / 'public-synthetic-client.json'
            path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
            return launcher.read_client(path)

    def test_reads_only_web_validation_project_without_secret_repr(self):
        client = self.read_fixture({'web': WEB})
        self.assertTrue(client.downloaded_callback_present)
        self.assertNotIn(WEB['client_secret'], repr(client))
        self.assertNotIn(WEB['client_id'], repr(client))

    def test_missing_downloaded_callback_does_not_claim_cloud_configuration(self):
        self.assertFalse(self.read_fixture({'web': WEB | {'redirect_uris': []}}).downloaded_callback_present)

    def test_rejects_wrong_project_installed_client_and_secret_controls(self):
        for value in ({'installed': WEB}, {'web': WEB | {'project_id': 'wrong'}},
                      {'web': WEB | {'client_secret': 'private\nunsafe-value'}},
                      {'web': WEB | {'client_id': 'evil.invalid'}}, {'web': []}):
            with self.subTest(kind=type(value).__name__), self.assertRaises(launcher.LocalTestError):
                self.read_fixture(value)

    def test_rejects_duplicate_keys_and_oversized_json_without_echo(self):
        for raw in ('{"web": {}, "web": {}}', 'x' * 65537, '{sensitive-invalid-json'):
            with self.assertRaises(launcher.LocalTestError) as error:
                self.read_fixture(raw)
            self.assertNotIn('sensitive', str(error.exception))

    def test_refuses_client_file_within_repository(self):
        with self.assertRaises(launcher.LocalTestError):
            launcher.read_client(launcher.ROOT / 'client_secret.json')

    def test_only_exact_oauth_code_exchange_and_identity_get_are_allowed(self):
        kwargs = {'auth': False, 'content_type': 'application/x-www-form-urlencoded',
                  'body': urlencode({'client_id': WEB['client_id'], 'client_secret': WEB['client_secret'],
                                     'grant_type': 'authorization_code', 'code': CODE,
                                     'redirect_uri': launcher.ORIGIN + launcher.CALLBACK,
                                     'code_verifier': 'synthetic-verifier'}).encode()}
        self.assertTrue(launcher.permitted_google_call('POST', 'oauth2.googleapis.com', '/token', kwargs))
        self.assertTrue(launcher.permitted_google_call('GET', 'www.googleapis.com',
                        '/drive/v3/about?' + urlencode({'fields': launcher.ABOUT_FIELDS}), {}))
        for method, host, path, arguments in (
            ('POST', 'oauth2.googleapis.com', '/token', kwargs | {'body': b'grant_type=refresh_token'}),
            ('POST', 'oauth2.googleapis.com', '/token', kwargs | {'auth': True}),
            ('GET', 'www.googleapis.com', '/drive/v3/files', {}),
            ('POST', 'www.googleapis.com', '/upload/drive/v3/files', {}),
            ('GET', 'evil.invalid', '/drive/v3/about?fields=user', {}),
            ('GET', 'www.googleapis.com', '/drive/v3/about?fields=*', {}),
            ('POST', 'oauth2.googleapis.com', '/revoke', {}),
        ):
            self.assertFalse(launcher.permitted_google_call(method, host, path, arguments))

    def test_sandbox_settings_have_no_erp_apps_database_or_debug(self):
        values = launcher.sandbox_settings(Path('/synthetic/temp'), launcher.Client('id', 'secret'), 'key')
        self.assertFalse(values['DEBUG'])
        self.assertEqual(values['INSTALLED_APPS'], ['django.contrib.sessions'])
        self.assertEqual(values['DATABASES']['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(values['DRIVE_BACKUP_REFRESH_TOKEN'], '')
        self.assertEqual(values['SESSION_COOKIE_PATH'], launcher.PAGE)
        self.assertEqual(values['SESSION_COOKIE_NAME'], 'elamine_drive_connection_test_8000')
        self.assertIs(values['DRIVE_BACKUP_LOCAL_CONNECTION_TEST'], True)
        self.assertNotIn('gestio_stock.settings', repr(values))

    def test_forbidden_operation_does_not_echo_arguments(self):
        with self.assertRaises(launcher.LocalTestError) as error:
            launcher.forbidden_operation('sensitive-value')
        self.assertNotIn('sensitive-value', str(error.exception))

    def test_alternate_port_requires_matching_callback_and_uses_same_sandbox(self):
        with patch.object(launcher, 'ORIGIN', 'http://127.0.0.1:8001'):
            values = launcher.sandbox_settings(Path('/synthetic/temp'), launcher.Client('id', 'secret'), 'key')
            self.assertEqual(values['DRIVE_BACKUP_OAUTH_REDIRECT_URI'],
                             'http://127.0.0.1:8001' + launcher.CALLBACK)
            self.assertFalse(values['DEBUG'])
            self.assertIs(values['DRIVE_BACKUP_LOCAL_CONNECTION_TEST'], True)
            self.assertEqual(values['SESSION_COOKIE_NAME'], 'elamine_drive_connection_test_8001')
            arguments = {'auth': False, 'content_type': 'application/x-www-form-urlencoded',
                         'body': urlencode({'client_id': WEB['client_id'], 'client_secret': WEB['client_secret'],
                                            'grant_type': 'authorization_code', 'code': CODE,
                                            'redirect_uri': 'http://127.0.0.1:8001' + launcher.CALLBACK,
                                            'code_verifier': 'synthetic-verifier'}).encode()}
            self.assertTrue(launcher.permitted_google_call('POST', 'oauth2.googleapis.com', '/token', arguments))
            arguments['body'] = arguments['body'].replace(b'%3A8001', b'%3A8000')
            self.assertFalse(launcher.permitted_google_call('POST', 'oauth2.googleapis.com', '/token', arguments))


@override_settings(**(CONFIG | {
    'DEBUG': False, 'DRIVE_BACKUP_LOCAL_CONNECTION_TEST': True,
    'DRIVE_BACKUP_OAUTH_REDIRECT_URI': launcher.ORIGIN + launcher.CALLBACK,
    'ROOT_URLCONF': 'scripts.launch_drive_oauth_local', 'ALLOWED_HOSTS': ['127.0.0.1'],
    'SESSION_COOKIE_NAME': 'elamine_drive_connection_test', 'SESSION_COOKIE_SECURE': False,
    'CSRF_USE_SESSIONS': True, 'SECURE_SSL_REDIRECT': False,
    'CSRF_FAILURE_VIEW': 'django.views.csrf.csrf_failure',
    'MIDDLEWARE': ['django.middleware.security.SecurityMiddleware',
                   'django.contrib.sessions.middleware.SessionMiddleware',
                   'django.middleware.csrf.CsrfViewMiddleware'],
}))
class LauncherBrowserTests(TransactionTestCase):
    def setUp(self):
        self.counters = dict(begins=0, connections_confirmed=0, callbacks_failed=0, stop=False)
        self.application = launcher.make_application(self.counters)
        self.cookie = ''
        self.google = self.enterContext(patch.object(drive_backup.DriveClient, '_request',
                                                     side_effect=[TOKEN_RESPONSE.copy(), IDENTITY.copy()]))
        self.backup = self.enterContext(patch.object(drive_backup, 'create_drive_backup'))

    def request(self, path=launcher.PAGE, method='GET', data=None, **overrides):
        environ = {}
        setup_testing_defaults(environ)
        parts = urlsplit(path)
        body = urlencode(data or {}).encode()
        environ.update(REQUEST_METHOD=method, PATH_INFO=parts.path, QUERY_STRING=parts.query,
                       HTTP_HOST='127.0.0.1:8000', REMOTE_ADDR='127.0.0.1', HTTP_ORIGIN=launcher.ORIGIN,
                       HTTP_COOKIE=self.cookie, CONTENT_TYPE='application/x-www-form-urlencoded',
                       CONTENT_LENGTH=str(len(body)), **{'wsgi.input': BytesIO(body)})
        environ.update(overrides)
        captured = {}

        def start(status, headers, _exc=None):
            captured.update(status=int(status.split()[0]), headers=dict(headers))
            cookies = [value.split(';', 1)[0] for name, value in headers if name.lower() == 'set-cookie']
            if cookies:
                self.cookie = '; '.join(cookies)

        response = self.application(environ, start)
        try:
            captured['body'] = b''.join(response).decode()
        finally:
            if hasattr(response, 'close'):
                response.close()
        return captured

    def csrf(self):
        page = self.request()
        return re.search(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', page['body']).group(1)

    def test_get_never_contacts_google_and_explains_no_backup(self):
        response = self.request()
        self.assertEqual(response['status'], 200)
        self.assertIn('Aucune sauvegarde', response['body'])
        self.assertNotIn('create/', response['body'])
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        self.assertEqual(response['headers']['Referrer-Policy'], 'same-origin')
        self.assertIn("form-action 'self' https://accounts.google.com;", response['headers']['Content-Security-Policy'])
        self.google.assert_not_called()
        self.backup.assert_not_called()

    @override_settings(DRIVE_BACKUP_OAUTH_REDIRECT_URI='http://127.0.0.1:8001' + launcher.CALLBACK)
    def test_alternate_port_host_origin_and_google_redirect_match(self):
        with patch.object(launcher, 'ORIGIN', 'http://127.0.0.1:8001'):
            page = self.request(HTTP_HOST='127.0.0.1:8001', HTTP_ORIGIN='http://127.0.0.1:8001')
            self.assertEqual(page['status'], 200)
            token = re.search(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', page['body']).group(1)
            start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token},
                                 HTTP_HOST='127.0.0.1:8001', HTTP_ORIGIN='http://127.0.0.1:8001')
            self.assertEqual(start['status'], 302)
            self.assertEqual(parse_qs(urlsplit(start['headers']['Location']).query)['redirect_uri'],
                             ['http://127.0.0.1:8001' + launcher.CALLBACK])
        self.google.assert_not_called()
        self.backup.assert_not_called()

    def test_csrf_and_origin_are_required_for_connection(self):
        self.assertEqual(self.request(launcher.PAGE + 'drive/connect/', 'POST')['status'], 403)
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'drive/connect/', 'POST',
                                     {'csrfmiddlewaretoken': token}, HTTP_ORIGIN='https://evil.invalid')['status'], 400)
        self.google.assert_not_called()

    def test_form_policy_preserves_origin_but_callback_and_google_redirect_do_not(self):
        self.assertEqual(self.request()['headers']['Referrer-Policy'], 'same-origin')
        token = self.csrf()
        start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token})
        self.assertEqual(start['status'], 302)
        self.assertEqual(start['headers']['Referrer-Policy'], 'no-referrer')
        callback = self.request(launcher.CALLBACK + '?error=access_denied')
        self.assertEqual(callback['headers']['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.request('/healthz')['headers']['Referrer-Policy'], 'no-referrer')
        self.google.assert_not_called()
        self.backup.assert_not_called()

    def test_null_missing_and_foreign_origins_stay_denied_without_recording_values(self):
        token = self.csrf()
        for origin in ('null', '', None, 'https://untrusted-private-value.invalid'):
            response = self.request(launcher.PAGE + 'drive/connect/', 'POST',
                                    {'csrfmiddlewaretoken': token}, HTTP_ORIGIN=origin)
            self.assertEqual(response['status'], 400)
            self.assertEqual(response['body'], 'Request refused.')
        health = self.request('/healthz')['body']
        self.assertNotIn('untrusted-private-value', health)
        self.assertEqual(json.loads(health)['refused'], {
            'request': 0, 'origin_null': 1, 'origin_missing': 2, 'origin_mismatch': 1,
        })
        self.google.assert_not_called()
        self.backup.assert_not_called()

    def test_correct_origin_does_not_bypass_invalid_csrf(self):
        self.request()
        response = self.request(launcher.PAGE + 'drive/connect/', 'POST',
                                {'csrfmiddlewaretoken': 'synthetic-invalid-csrf'})
        self.assertEqual(response['status'], 403)
        self.assertEqual(self.counters['begins'], 0)
        self.google.assert_not_called()
        self.backup.assert_not_called()

    def test_rebinding_proxy_and_large_body_are_refused(self):
        for values in ({'HTTP_HOST': 'evil.invalid'}, {'REMOTE_ADDR': '192.0.2.1'},
                       {'HTTP_X_FORWARDED_HOST': 'evil.invalid'}, {'CONTENT_LENGTH': '9000'}):
            self.assertEqual(self.request(**values)['status'], 400)
        self.google.assert_not_called()

    def test_backup_route_is_absent_and_direct_post_cannot_export(self):
        self.assertEqual(self.request(launcher.PAGE + 'create/')['status'], 404)
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'create/', 'POST', {'csrfmiddlewaretoken': token})['status'], 404)
        self.backup.assert_not_called()
        self.google.assert_not_called()

    def test_default_connection_only_has_no_remote_roundtrip_route(self):
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'drive/synthetic-roundtrip/', 'POST',
                                     {'csrfmiddlewaretoken': token})['status'], 404)
        self.google.assert_not_called()
        self.backup.assert_not_called()

    def test_optional_synthetic_test_requires_connected_destination_and_is_one_shot(self):
        calls = []
        fake = SimpleNamespace(used=False, public_status=lambda: 'WAITING',
                               public_result=lambda: {'status': 'WAITING'})
        def run(config):
            calls.append(config)
            fake.used = True
        fake.run = run
        self.application = launcher.make_application(self.counters, synthetic_test=fake)
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'drive/synthetic-roundtrip/', 'POST',
                                     {'csrfmiddlewaretoken': token, 'destination': 'incorrect'})['status'], 403)
        self.assertFalse(calls)
        start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token})
        state = parse_qs(urlsplit(start['headers']['Location']).query)['state'][0]
        self.request(launcher.CALLBACK + '?' + urlencode({'state': state, 'code': CODE}))
        page = self.request()
        destination = re.search(r'name="destination" value="([a-f0-9]{64})"', page['body']).group(1)
        token = re.search(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', page['body']).group(1)
        data = {'csrfmiddlewaretoken': token, 'destination': destination}
        self.assertEqual(self.request(launcher.PAGE + 'drive/synthetic-roundtrip/', 'POST', data)['status'], 302)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.request(launcher.PAGE + 'drive/synthetic-roundtrip/', 'POST', data)['status'], 409)
        health = json.loads(self.request('/healthz')['body'])
        self.assertEqual(health['mode'], 'synthetic_roundtrip')
        self.assertNotIn('archive_operations', health)
        self.backup.assert_not_called()

    def test_cleanup_mode_has_dedicated_route_and_truthful_no_upload_page(self):
        fake = SimpleNamespace(used=False, cleanup_only=True, public_status=lambda: 'WAITING',
                               public_result=lambda: {'status': 'WAITING'}, run=lambda config: None)
        self.application = launcher.make_application(self.counters, synthetic_test=fake)
        page = self.request()
        self.assertEqual(page['status'], 200)
        self.assertIn('nettoyage du seul fichier fictif', page['body'])
        self.assertIn('Aucun envoi, téléchargement', page['body'])
        self.assertNotIn('sera envoyé', page['body'])
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'drive/synthetic-roundtrip/', 'POST',
                                     {'csrfmiddlewaretoken': token})['status'], 404)
        self.assertEqual(self.request(launcher.PAGE + 'drive/synthetic-cleanup/', 'POST',
                                     {'csrfmiddlewaretoken': token, 'destination': 'invalid'})['status'], 403)
        self.assertEqual(json.loads(self.request('/healthz')['body'])['mode'], 'synthetic_cleanup')
        self.google.assert_not_called()
        self.backup.assert_not_called()

    def test_cleanup_route_requires_consent_bound_destination_and_cannot_run_twice(self):
        calls = []
        fake = SimpleNamespace(used=False, cleanup_only=True, public_status=lambda: 'WAITING',
                               public_result=lambda: {'status': 'WAITING'})
        def run(config):
            calls.append(config)
            fake.used = True
        fake.run = run
        self.application = launcher.make_application(self.counters, synthetic_test=fake)
        token = self.csrf()
        start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token})
        state = parse_qs(urlsplit(start['headers']['Location']).query)['state'][0]
        self.request(launcher.CALLBACK + '?' + urlencode({'state': state, 'code': CODE}))
        page = self.request()
        self.assertIn('Vérifier et supprimer uniquement', page['body'])
        destination = re.search(r'name="destination" value="([a-f0-9]{64})"', page['body']).group(1)
        token = re.search(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', page['body']).group(1)
        data = {'csrfmiddlewaretoken': token, 'destination': destination}
        action = launcher.PAGE + 'drive/synthetic-cleanup/'
        self.assertEqual(self.request(action, 'POST', data, HTTP_ORIGIN='null')['status'], 400)
        self.assertEqual(self.request(action, 'POST', data | {'extra': 'x'})['status'], 400)
        self.assertEqual(self.request(action, 'POST', data)['status'], 302)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.request(action, 'POST', data)['status'], 409)
        self.backup.assert_not_called()

    def test_browser_flow_only_connects_displays_identity_then_removes_local_vault(self):
        token = self.csrf()
        start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token})
        self.assertEqual(start['status'], 302)
        target = urlsplit(start['headers']['Location'])
        self.assertEqual(target.netloc, 'accounts.google.com')
        state = parse_qs(target.query)['state'][0]
        callback = self.request(launcher.CALLBACK + '?' + urlencode({'state': state, 'code': CODE}))
        self.assertEqual(callback['status'], 302)
        self.assertEqual(callback['headers']['Location'], launcher.PAGE)
        self.assertEqual(callback['headers']['Referrer-Policy'], 'no-referrer')
        self.assertIn(IDENTITY['user']['emailAddress'], self.request()['body'])
        self.assertEqual(self.counters['connections_confirmed'], 1)
        self.assertEqual(self.google.call_count, 2)
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'drive/disconnect/', 'POST',
                                     {'csrfmiddlewaretoken': token})['status'], 302)
        self.assertNotIn(IDENTITY['user']['emailAddress'], self.request()['body'])
        self.assertEqual(self.google.call_count, 2)
        self.backup.assert_not_called()

    def test_invalid_callback_and_health_never_expose_query_or_identity(self):
        self.request()
        self.request(launcher.CALLBACK + '?code=sensitive-never-echo')
        response = self.request('/healthz')
        self.assertEqual(json.loads(response['body'])['archive_operations'], 0)
        self.assertNotIn('sensitive-never-echo', response['body'])
        self.assertNotIn(IDENTITY['user']['emailAddress'], response['body'])
        self.google.assert_not_called()

    def test_replayed_callback_never_exchanges_twice(self):
        token = self.csrf()
        start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token})
        state = parse_qs(urlsplit(start['headers']['Location']).query)['state'][0]
        path = launcher.CALLBACK + '?' + urlencode({'state': state, 'code': CODE})
        self.request(path)
        self.request(path)
        self.assertEqual(self.google.call_count, 2)
        self.assertEqual(self.counters['connections_confirmed'], 1)
        self.assertEqual(self.counters['callbacks_failed'], 1)

    def test_consent_denial_never_exchanges_tokens(self):
        token = self.csrf()
        start = self.request(launcher.PAGE + 'drive/connect/', 'POST', {'csrfmiddlewaretoken': token})
        state = parse_qs(urlsplit(start['headers']['Location']).query)['state'][0]
        self.request(launcher.CALLBACK + '?' + urlencode({'state': state, 'error': 'access_denied'}))
        self.assertEqual(self.counters['callbacks_failed'], 1)
        self.google.assert_not_called()

    def test_stop_is_csrf_protected_and_sets_cleanup_signal(self):
        token = self.csrf()
        self.assertEqual(self.request(launcher.PAGE + 'stop/', 'POST', {'csrfmiddlewaretoken': token})['status'], 200)
        self.assertTrue(self.counters['stop'])
        self.google.assert_not_called()
