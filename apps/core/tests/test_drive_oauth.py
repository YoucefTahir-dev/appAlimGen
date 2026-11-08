"""Synthetic OAuth tests: Google, PostgreSQL and native tools are never used."""
import base64
from datetime import timedelta
import hashlib
import json
from importlib.util import find_spec
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.http import QueryDict
from django.db import transaction
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.core import drive_backup, drive_oauth as oauth


# Deliberately public synthetic fixtures. These are not real OAuth credentials.
TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()
ACCESS = 'ya29.' + 'A' * 40
REFRESH = '1//' + 'R' * 40
CODE = '4/synthetic-authorization-code-only'
TOKEN_RESPONSE = {
    'access_token': ACCESS, 'refresh_token': REFRESH,
    'scope': drive_backup.SCOPE, 'token_type': 'Bearer', 'expires_in': 3600,
}
IDENTITY = {'user': {
    'emailAddress': 'synthetic-owner@example.invalid',
    'displayName': 'Synthetic Drive Owner', 'permissionId': '12345678901234567890',
}}
CONFIG = {
    'DRIVE_BACKUP_ENABLED': True,
    'DRIVE_BACKUP_OAUTH_ENABLED': True,
    'DRIVE_BACKUP_CLIENT_ID': 'synthetic-client.apps.googleusercontent.com',
    'DRIVE_BACKUP_CLIENT_SECRET': 'synthetic-client-secret-not-real',
    'DRIVE_BACKUP_PASSPHRASE': 'Synthetic archive passphrase not production 368529!',
    'DRIVE_BACKUP_REFRESH_TOKEN': 'legacy-env-refresh-must-not-be-used',
    'DRIVE_BACKUP_TOKEN_KEY': TEST_KEY,
    'DRIVE_BACKUP_OAUTH_REDIRECT_URI': 'https://backup-test.invalid' + oauth.CALLBACK_PATH,
    'SESSION_ENGINE': 'django.contrib.sessions.backends.db',
    'SESSION_COOKIE_AGE': 8 * 60 * 60,
    'DEBUG': False,
}
CRYPTO_PRESENT = find_spec('cryptography') is not None


class OAuthDisabledTests(SimpleTestCase):
    @override_settings(DRIVE_BACKUP_OAUTH_ENABLED=False)
    def test_disabled_status_never_loads_configuration_or_session(self):
        with patch.object(oauth, '_configuration') as config:
            result = oauth.status(object())
        self.assertEqual(result, {
            'mode_enabled': False, 'available': False, 'connected': False,
            'account': None, 'issues': [],
        })
        config.assert_not_called()

    @override_settings(DRIVE_BACKUP_OAUTH_ENABLED=False)
    def test_disabled_entry_points_fail_before_database_crypto_or_google(self):
        for action in (oauth.begin, oauth.complete, oauth.runtime_config):
            with self.subTest(action=action.__name__):
                with patch.object(oauth, '_binding') as binding, patch.object(drive_backup, 'DriveClient') as client:
                    with self.assertRaises(oauth.DriveOAuthError) as error:
                        action(object())
                    self.assertEqual(error.exception.code, 'disabled')
                    binding.assert_not_called()
                    client.assert_not_called()

    def test_mode_flag_accepts_only_explicit_true_values(self):
        for value, expected in ((True, True), ('true', True), ('1', True),
                                (False, False), ('false', False), ('no', False), (1, False)):
            with self.subTest(value=value), override_settings(DRIVE_BACKUP_OAUTH_ENABLED=value):
                self.assertEqual(oauth.enabled(), expected)

    def test_errors_never_echo_unknown_sensitive_input(self):
        for value in ('secret-must-not-leak', [], {'token': 'secret'}):
            error = oauth.DriveOAuthError(value)
            self.assertEqual(error.code, 'exchange')
            self.assertEqual(str(error), oauth.ERRORS['exchange'])


@override_settings(**CONFIG)
class OAuthUnavailableTests(TestCase):
    def test_missing_optional_crypto_fails_closed_without_google_or_nonce(self):
        import builtins
        original = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.startswith('cryptography'):
                raise ImportError('synthetic-private-error-must-not-leak')
            return original(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=guarded_import), patch.object(drive_backup, 'DriveClient') as client:
            with self.assertRaises(oauth.DriveOAuthError) as error:
                oauth.begin(object())
            self.assertEqual(error.exception.code, 'configuration')
            self.assertNotIn('synthetic-private-error', str(error.exception))
        client.assert_not_called()
        self.assertEqual(Session.objects.count(), 0)


@skipUnless(CRYPTO_PRESENT, 'Optional cryptography package is absent; run the dedicated Drive requirements to validate OAuth crypto.')
@override_settings(**CONFIG)
class DriveOAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_superuser(username='synthetic-oauth-owner', password='Synthetic-Password-346!')
        cls.other = User.objects.create_superuser(username='synthetic-oauth-other', password='Synthetic-Password-973!')

    def setUp(self):
        self.factory = RequestFactory()
        self.http = self.enterContext(patch.object(
            drive_backup.DriveClient, '_request', side_effect=[TOKEN_RESPONSE.copy(), IDENTITY.copy()],
        ))
        self.native = self.enterContext(patch.object(drive_backup, '_run', side_effect=AssertionError('No native tools permitted')))
        self.backup = self.enterContext(patch.object(drive_backup, 'create_drive_backup'))

    def make_request(self, *, user=None, session=None):
        request = self.factory.get(oauth.CALLBACK_PATH)
        request.user = user or self.user
        request.session = session or SessionStore()
        if not request.session.session_key:
            request.session.save()
        return request

    def start(self, request=None):
        request = request or self.make_request()
        url = oauth.begin(request)
        values = parse_qs(urlsplit(url).query)
        request.session.save()
        return request, values

    def callback(self, request, values, **changes):
        query = QueryDict('', mutable=True)
        query.update({'state': values['state'][0], 'code': CODE} | changes)
        request.GET = query
        return request

    def connected(self):
        request, values = self.start()
        oauth.complete(self.callback(request, values))
        request.session.save()
        return request

    def pending_payload(self, request):
        setup = oauth._configuration()
        row = Session.objects.get(session_key=request.session[oauth.PENDING_KEY])
        return oauth._open(setup, row.session_data, 'state')

    def connection_payload(self, request):
        setup = oauth._configuration()
        row = Session.objects.get(session_key=request.session[oauth.CONNECTION_KEY])
        envelope = json.loads(row.session_data)
        return oauth._open(setup, envelope['cipher'], 'connection')

    def test_available_status_does_not_contact_google_or_native_tools(self):
        request = self.make_request()
        result = oauth.status(request)
        self.assertTrue(result['mode_enabled'])
        self.assertTrue(result['available'])
        self.assertFalse(result['connected'])
        self.assertIsNone(result['account'])
        self.http.assert_not_called()
        self.native.assert_not_called()

    @override_settings(DEBUG=False, DRIVE_BACKUP_LOCAL_CONNECTION_TEST=True,
                       DRIVE_BACKUP_OAUTH_REDIRECT_URI='http://127.0.0.1:8000' + oauth.CALLBACK_PATH)
    def test_connection_only_loopback_works_without_debug(self):
        for port in (8000, 8001):
            with self.subTest(port=port), override_settings(
                    DRIVE_BACKUP_OAUTH_REDIRECT_URI='http://127.0.0.1:' + str(port) + oauth.CALLBACK_PATH):
                self.assertTrue(oauth.status(self.make_request())['available'])
        self.http.assert_not_called()

    @override_settings(DEBUG=False, DRIVE_BACKUP_LOCAL_CONNECTION_TEST=True)
    def test_connection_only_http_exception_refuses_other_hosts_ports_or_paths(self):
        for uri in ('http://localhost:8000' + oauth.CALLBACK_PATH,
                    'http://127.0.0.1:8002' + oauth.CALLBACK_PATH,
                    'http://backup-test.invalid' + oauth.CALLBACK_PATH,
                    'http://127.0.0.1:8000/wrong/'):
            with self.subTest(uri=uri), override_settings(DRIVE_BACKUP_OAUTH_REDIRECT_URI=uri):
                self.assertFalse(oauth.status(self.make_request())['available'])
        self.http.assert_not_called()

    def test_missing_server_configuration_cannot_create_pending_authorization(self):
        request = self.make_request()
        for name in ('DRIVE_BACKUP_CLIENT_ID', 'DRIVE_BACKUP_CLIENT_SECRET',
                     'DRIVE_BACKUP_PASSPHRASE', 'DRIVE_BACKUP_TOKEN_KEY', 'DRIVE_BACKUP_OAUTH_REDIRECT_URI'):
            with self.subTest(name=name), override_settings(**{name: ''}):
                self.assertFalse(oauth.status(request)['available'])
                with self.assertRaises(oauth.DriveOAuthError):
                    oauth.begin(request)
        self.assertEqual(Session.objects.filter(session_key__startswith='drive_').count(), 0)
        self.http.assert_not_called()

    def test_backup_feature_must_also_be_enabled(self):
        with override_settings(DRIVE_BACKUP_ENABLED=False):
            self.assertFalse(oauth.status(self.make_request())['available'])
            with self.assertRaises(oauth.DriveOAuthError):
                oauth.begin(self.make_request())
        self.http.assert_not_called()

    def test_signed_cookie_cache_and_file_backends_are_refused(self):
        request = self.make_request()
        for backend in ('signed_cookies', 'cache', 'cached_db', 'file'):
            with self.subTest(backend=backend), override_settings(SESSION_ENGINE='django.contrib.sessions.backends.' + backend):
                result = oauth.status(request)
                self.assertFalse(result['available'])
                with self.assertRaises(oauth.DriveOAuthError) as error:
                    oauth.begin(request)
                self.assertEqual(error.exception.code, 'session')
        self.http.assert_not_called()

    def test_anonymous_or_inactive_user_is_refused(self):
        from django.contrib.auth.models import AnonymousUser
        for user in (AnonymousUser(), self.user):
            if user is self.user:
                user.is_active = False
            request = self.make_request(user=user)
            with self.assertRaises(oauth.DriveOAuthError) as error:
                oauth.begin(request)
            self.assertEqual(error.exception.code, 'session')
        self.http.assert_not_called()

    def test_token_key_must_be_valid_and_independent(self):
        request = self.make_request()
        for values in (
            {'DRIVE_BACKUP_TOKEN_KEY': 'invalid'},
            {'DRIVE_BACKUP_TOKEN_KEY': TEST_KEY, 'SECRET_KEY': TEST_KEY},
            {'DRIVE_BACKUP_TOKEN_KEY': TEST_KEY, 'DRIVE_BACKUP_PASSPHRASE': TEST_KEY},
        ):
            with self.subTest(values=list(values)), override_settings(**values):
                with self.assertRaises(oauth.DriveOAuthError) as error:
                    oauth.begin(request)
                self.assertEqual(error.exception.code, 'configuration')

    def test_callback_uri_is_fixed_and_rejects_unsafe_variants(self):
        for uri in (
            'http://backup-test.invalid' + oauth.CALLBACK_PATH,
            'https://user:secret@backup-test.invalid' + oauth.CALLBACK_PATH,
            'https://backup-test.invalid/wrong/',
            'https://backup-test.invalid' + oauth.CALLBACK_PATH + '?next=evil',
            'https://backup-test.invalid' + oauth.CALLBACK_PATH + '#fragment',
            'https://backup-test.invalid:8443' + oauth.CALLBACK_PATH,
            'http://localhost:invalid' + oauth.CALLBACK_PATH,
        ):
            with self.subTest(uri=uri), override_settings(DRIVE_BACKUP_OAUTH_REDIRECT_URI=uri):
                with self.assertRaises(oauth.DriveOAuthError):
                    oauth.begin(self.make_request())

    def test_http_loopback_is_allowed_only_in_debug(self):
        uri = 'http://localhost:8000' + oauth.CALLBACK_PATH
        with override_settings(DRIVE_BACKUP_OAUTH_REDIRECT_URI=uri, DEBUG=False):
            with self.assertRaises(oauth.DriveOAuthError):
                oauth.begin(self.make_request())
        with override_settings(DRIVE_BACKUP_OAUTH_REDIRECT_URI=uri, DEBUG=True):
            request, values = self.start()
            self.assertEqual(values['redirect_uri'], [uri])
            self.assertIn(oauth.PENDING_KEY, request.session)

    def test_authorization_url_has_only_google_drive_file_scope_and_pkce(self):
        request, values = self.start()
        self.assertEqual(values['scope'], [drive_backup.SCOPE])
        self.assertEqual(values['response_type'], ['code'])
        self.assertEqual(values['access_type'], ['offline'])
        self.assertEqual(values['prompt'], ['consent select_account'])
        self.assertEqual(values['include_granted_scopes'], ['false'])
        self.assertEqual(values['code_challenge_method'], ['S256'])
        payload = self.pending_payload(request)
        expected = base64.urlsafe_b64encode(hashlib.sha256(payload['verifier'].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(values['code_challenge'], [expected])
        self.assertNotIn(payload['verifier'], repr(values))
        self.assertNotIn(CONFIG['DRIVE_BACKUP_CLIENT_SECRET'], repr(values))
        self.assertNotIn(CONFIG['DRIVE_BACKUP_PASSPHRASE'], repr(values))
        self.http.assert_not_called()

    def test_pending_data_is_encrypted_and_session_contains_only_opaque_pointer(self):
        request, values = self.start()
        pointer = request.session[oauth.PENDING_KEY]
        self.assertRegex(pointer, r'^drive_[0-9a-f]{32}$')
        row = Session.objects.get(session_key=pointer)
        self.assertNotIn(values['state'][0], row.session_data)
        self.assertNotIn('verifier', row.session_data)
        self.assertEqual(set(request.session.keys()), {oauth.PENDING_KEY})
        self.assertLessEqual(row.expire_date, timezone.now() + timedelta(seconds=oauth.PENDING_SECONDS))

    def test_begin_replaces_old_pending_and_disconnects_old_account(self):
        request = self.connected()
        self.http.reset_mock()
        request, _ = self.start(request)
        first = request.session[oauth.PENDING_KEY]
        request, _ = self.start(request)
        self.assertNotEqual(request.session[oauth.PENDING_KEY], first)
        self.assertFalse(Session.objects.filter(session_key=first).exists())
        self.assertNotIn(oauth.CONNECTION_KEY, request.session)
        self.http.assert_not_called()

    def test_callback_exchanges_code_server_side_and_uses_scoped_about_identity(self):
        request, values = self.start()
        pending = self.pending_payload(request)
        oauth.complete(self.callback(request, values))
        self.assertEqual(self.http.call_count, 2)
        exchange, about = self.http.call_args_list
        self.assertEqual(exchange.args, ('POST', 'oauth2.googleapis.com', '/token'))
        sent = parse_qs(exchange.kwargs['body'].decode())
        self.assertEqual(sent['grant_type'], ['authorization_code'])
        self.assertEqual(sent['code'], [CODE])
        self.assertEqual(sent['code_verifier'], [pending['verifier']])
        self.assertEqual(sent['redirect_uri'], [CONFIG['DRIVE_BACKUP_OAUTH_REDIRECT_URI']])
        self.assertEqual(about.args[:2], ('GET', 'www.googleapis.com'))
        parsed = urlsplit(about.args[2])
        self.assertEqual(parsed.path, '/drive/v3/about')
        self.assertEqual(parse_qs(parsed.query)['fields'], ['user(emailAddress,displayName,permissionId)'])
        self.native.assert_not_called()
        self.backup.assert_not_called()

    def test_callback_consumes_nonce_before_first_google_request(self):
        request, values = self.start()
        pointer = request.session[oauth.PENDING_KEY]

        def response(*args, **_kwargs):
            self.assertFalse(Session.objects.filter(session_key=pointer).exists())
            return TOKEN_RESPONSE.copy() if args[0] == 'POST' else IDENTITY.copy()

        self.http.side_effect = response
        oauth.complete(self.callback(request, values))
        self.assertNotIn(oauth.PENDING_KEY, request.session)

    def test_repeated_callback_cannot_exchange_twice(self):
        request, values = self.start()
        oauth.complete(self.callback(request, values))
        with self.assertRaises(oauth.DriveOAuthError) as error:
            oauth.complete(request)
        self.assertEqual(error.exception.code, 'state')
        self.assertEqual(self.http.call_count, 2)

    def test_stale_session_save_cannot_resurrect_consumed_nonce(self):
        request, values = self.start()
        stale = self.make_request(session=SessionStore(session_key=request.session.session_key))
        pointer = stale.session[oauth.PENDING_KEY]  # Simulate a concurrent request's cache.
        oauth.complete(self.callback(request, values))
        stale.session.save()
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.complete(self.callback(stale, values))
        self.assertFalse(Session.objects.filter(session_key=pointer).exists())
        self.assertEqual(self.http.call_count, 2)

    def test_wrong_state_is_rejected_without_google(self):
        request, values = self.start()
        self.callback(request, values, state='Z' * 43)
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.complete(request)
        self.http.assert_not_called()

    def test_expired_nonce_is_rejected_without_google(self):
        request, values = self.start()
        Session.objects.filter(session_key=request.session[oauth.PENDING_KEY]).update(
            expire_date=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.complete(self.callback(request, values))
        self.http.assert_not_called()

    def test_tampered_pending_ciphertext_is_rejected(self):
        request, values = self.start()
        Session.objects.filter(session_key=request.session[oauth.PENDING_KEY]).update(session_data='tampered-private-value')
        with self.assertRaises(oauth.DriveOAuthError) as error:
            oauth.complete(self.callback(request, values))
        self.assertNotIn('tampered-private-value', str(error.exception))
        self.http.assert_not_called()

    def test_nonce_is_bound_to_user_session_and_current_auth_hash(self):
        for change in ('user', 'session', 'password'):
            with self.subTest(change=change):
                request, values = self.start()
                if change == 'user':
                    request.user = self.other
                elif change == 'session':
                    pointer = request.session[oauth.PENDING_KEY]
                    request.session = SessionStore()
                    request.session.save()
                    request.session[oauth.PENDING_KEY] = pointer
                else:
                    request.user.set_password('Changed-Synthetic-Password-752!')
                with self.assertRaises(oauth.DriveOAuthError):
                    oauth.complete(self.callback(request, values))
        self.http.assert_not_called()

    def test_client_rotation_invalidates_pending_without_google(self):
        request, values = self.start()
        with override_settings(DRIVE_BACKUP_CLIENT_ID='other-client.apps.googleusercontent.com'):
            with self.assertRaises(oauth.DriveOAuthError):
                oauth.complete(self.callback(request, values))
        self.http.assert_not_called()

    def test_duplicate_malformed_or_injected_callback_queries_are_rejected(self):
        request, values = self.start()
        state = values['state'][0]
        for query in (
            'state=' + state + '&state=' + state + '&code=' + CODE,
            'state=' + state + '&code=' + CODE + '&code=' + CODE,
            'state=' + state + '&code=' + CODE + '&folder=unexpected',
            'state=' + state + '&code=' + CODE + '&iss=https://evil.invalid',
            'state=' + state + '&code=short',
            'state=' + state + '&code=' + CODE + '&error=access_denied',
            'code=' + CODE,
        ):
            with self.subTest(query_type=query.split('&')[-1].split('=')[0]):
                request.GET = QueryDict(query)
                with self.assertRaises(oauth.DriveOAuthError):
                    oauth.complete(request)
        self.http.assert_not_called()

    def test_google_denial_consumes_state_without_echoing_description(self):
        request, values = self.start()
        self.callback(request, values, code='', error='access_denied', error_description='sensitive-description-never-echo')
        with self.assertRaises(oauth.DriveOAuthError) as error:
            oauth.complete(request)
        self.assertEqual(error.exception.code, 'exchange')
        self.assertNotIn('sensitive-description', str(error.exception))
        self.assertNotIn(oauth.PENDING_KEY, request.session)
        self.http.assert_not_called()

    def test_invalid_google_token_or_extra_scope_is_refused(self):
        for changes in (
            {'scope': drive_backup.SCOPE + ' email'}, {'scope': None},
            {'refresh_token': ''}, {'access_token': 'bad'}, {'token_type': 'MAC'},
        ):
            with self.subTest(fields=list(changes)):
                request, values = self.start()
                self.http.side_effect = [TOKEN_RESPONSE | changes]
                with self.assertRaises(oauth.DriveOAuthError) as error:
                    oauth.complete(self.callback(request, values))
                self.assertEqual(error.exception.code, 'exchange')
                self.assertNotIn(oauth.CONNECTION_KEY, request.session)

    def test_provider_exception_is_fixed_and_consumed_state_cannot_retry(self):
        request, values = self.start()
        self.http.side_effect = RuntimeError('sensitive-google-diagnostic-must-not-leak')
        with self.assertRaises(oauth.DriveOAuthError) as error:
            oauth.complete(self.callback(request, values))
        self.assertEqual(error.exception.code, 'exchange')
        self.assertNotIn('sensitive-google', str(error.exception))
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.complete(request)
        self.assertEqual(self.http.call_count, 1)

    def test_identity_must_be_complete_and_safe_to_display(self):
        for user in (None, {}, {'emailAddress': 'bad'},
                     IDENTITY['user'] | {'emailAddress': 'owner\n@example.invalid'},
                     IDENTITY['user'] | {'displayName': 'bad\x00name'}):
            with self.subTest(identity_type=type(user).__name__):
                request, values = self.start()
                self.http.side_effect = [TOKEN_RESPONSE.copy(), {'user': user}]
                with self.assertRaises(oauth.DriveOAuthError) as error:
                    oauth.complete(self.callback(request, values))
                self.assertEqual(error.exception.code, 'identity')
                self.assertNotIn(oauth.CONNECTION_KEY, request.session)

    def test_refresh_and_identity_are_encrypted_in_server_session(self):
        request = self.connected()
        blob = request.session[oauth.CONNECTION_KEY]
        self.assertRegex(blob, r'^drivec_[0-9a-f]{32}$')
        self.assertNotIn(REFRESH, blob)
        self.assertNotIn(ACCESS, blob)
        self.assertNotIn(IDENTITY['user']['emailAddress'], blob)
        self.assertNotIn(CONFIG['DRIVE_BACKUP_CLIENT_SECRET'], repr(dict(request.session)))
        row = Session.objects.get(session_key=request.session.session_key)
        self.assertNotIn(REFRESH, row.session_data)
        self.assertNotIn(ACCESS, row.session_data)
        vault = Session.objects.get(session_key=blob)
        self.assertNotIn(REFRESH, vault.session_data)
        self.assertNotIn(ACCESS, vault.session_data)
        self.assertNotIn(IDENTITY['user']['emailAddress'], vault.session_data)

    def test_connected_status_returns_only_identity_without_google(self):
        request = self.connected()
        self.http.reset_mock()
        result = oauth.status(request)
        self.assertTrue(result['connected'])
        self.assertEqual(result['account'], {
            'email': IDENTITY['user']['emailAddress'], 'display_name': IDENTITY['user']['displayName'],
        })
        self.assertNotIn(REFRESH, repr(result))
        self.assertNotIn('permission_id', result['account'])
        self.http.assert_not_called()

    def test_runtime_config_uses_only_the_connected_session_refresh_token(self):
        request = self.connected()
        self.http.reset_mock()
        config = oauth.runtime_config(request)
        self.assertEqual(config.refresh_token, REFRESH)
        self.assertNotEqual(config.refresh_token, CONFIG['DRIVE_BACKUP_REFRESH_TOKEN'])
        self.assertEqual(config.expected_permission_id, IDENTITY['user']['permissionId'])
        self.assertNotIn(REFRESH, repr(config))
        self.http.assert_not_called()

    def test_environment_refresh_token_cannot_replace_a_missing_session_connection(self):
        request = self.make_request()
        with self.assertRaises(oauth.DriveOAuthError) as error:
            oauth.runtime_config(request)
        self.assertEqual(error.exception.code, 'connection')
        self.http.assert_not_called()

    def test_connection_expires_no_later_than_eight_hours(self):
        request = self.connected()
        payload = self.connection_payload(request)
        self.assertLessEqual(payload['expires_at'] - payload['issued_at'], oauth.CONNECTION_SECONDS)
        with patch.object(oauth, '_now', return_value=payload['expires_at'] + 1):
            self.assertFalse(oauth.status(request)['connected'])
            with self.assertRaises(oauth.DriveOAuthError):
                oauth.runtime_config(request)

    def test_shorter_authentication_session_expiry_is_respected(self):
        request, values = self.start()
        request.session.set_expiry(300)
        oauth.complete(self.callback(request, values))
        payload = self.connection_payload(request)
        self.assertLessEqual(payload['expires_at'] - payload['issued_at'], 300)

    def test_token_key_rotation_or_tampering_never_exposes_tokens(self):
        request = self.connected()
        original = request.session[oauth.CONNECTION_KEY]
        vault = Session.objects.get(session_key=original)
        original_data = vault.session_data
        for blob in ('tampered-sensitive-token', 'malformed-json'):
            vault.session_data = blob
            vault.save(update_fields=['session_data'])
            result = oauth.status(request)
            self.assertFalse(result['connected'])
            self.assertNotIn(blob, repr(result))
        vault.session_data = original_data
        vault.save(update_fields=['session_data'])
        other_key = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode()
        with override_settings(DRIVE_BACKUP_TOKEN_KEY=other_key):
            self.assertFalse(oauth.status(request)['connected'])

    def test_connection_is_not_reused_by_another_user_or_browser_session(self):
        request = self.connected()
        blob = request.session[oauth.CONNECTION_KEY]
        other = self.make_request(user=self.other)
        other.session[oauth.CONNECTION_KEY] = blob
        self.assertFalse(oauth.status(other)['connected'])
        same_user_other_browser = self.make_request()
        same_user_other_browser.session[oauth.CONNECTION_KEY] = blob
        self.assertFalse(oauth.status(same_user_other_browser)['connected'])

    def test_logout_flush_makes_connection_unavailable(self):
        request = self.connected()
        request.session.flush()
        result = oauth.status(request)
        self.assertFalse(result['connected'])
        self.assertFalse(result['available'])

    def test_disconnect_purges_locally_without_revocation_or_archive_deletion(self):
        request = self.connected()
        pointer = request.session[oauth.CONNECTION_KEY]
        self.http.reset_mock()
        oauth.disconnect(request)
        self.assertNotIn(oauth.CONNECTION_KEY, request.session)
        self.assertNotIn(oauth.PENDING_KEY, request.session)
        self.assertFalse(Session.objects.filter(session_key=pointer).exists())
        self.http.assert_not_called()
        self.backup.assert_not_called()

    def test_disconnect_removes_owned_pending_nonce(self):
        request, _ = self.start()
        pointer = request.session[oauth.PENDING_KEY]
        oauth.disconnect(request)
        self.assertFalse(Session.objects.filter(session_key=pointer).exists())
        self.assertNotIn(oauth.PENDING_KEY, request.session)
        self.http.assert_not_called()

    def test_disconnect_after_key_rotation_clears_local_connection_safely(self):
        request = self.connected()
        pointer = request.session[oauth.CONNECTION_KEY]
        self.http.reset_mock()
        with override_settings(DRIVE_BACKUP_TOKEN_KEY='unavailable-key'):
            oauth.disconnect(request)
        self.assertNotIn(oauth.CONNECTION_KEY, request.session)
        self.assertFalse(Session.objects.filter(session_key=pointer).exists())
        self.http.assert_not_called()

    def test_stale_auth_session_cannot_resurrect_disconnected_credentials(self):
        request = self.connected()
        stale = self.make_request(session=SessionStore(session_key=request.session.session_key))
        pointer = stale.session[oauth.CONNECTION_KEY]
        oauth.disconnect(request)
        request.session.save()
        stale.session.save()  # SESSION_SAVE_EVERY_REQUEST may restore an opaque pointer.
        self.assertFalse(Session.objects.filter(session_key=pointer).exists())
        self.assertFalse(oauth.status(stale)['connected'])
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.runtime_config(stale)

    def test_begin_account_change_invalidates_old_vault_even_for_stale_sessions(self):
        request = self.connected()
        stale = self.make_request(session=SessionStore(session_key=request.session.session_key))
        old_pointer = stale.session[oauth.CONNECTION_KEY]
        self.http.reset_mock()
        self.start(request)
        stale.session.save()
        self.assertFalse(Session.objects.filter(session_key=old_pointer).exists())
        self.assertFalse(oauth.status(stale)['connected'])
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.runtime_config(stale)
        self.http.assert_not_called()

    def test_forged_vault_pointer_cannot_delete_another_sessions_credentials(self):
        owner = self.connected()
        pointer = owner.session[oauth.CONNECTION_KEY]
        other = self.make_request(user=self.other)
        other.session[oauth.CONNECTION_KEY] = pointer
        oauth.disconnect(other)
        self.assertTrue(Session.objects.filter(session_key=pointer).exists())
        self.assertTrue(oauth.status(owner)['connected'])

    def test_archive_confirmation_is_bound_to_the_displayed_connection(self):
        request = self.connected()
        state = oauth.status(request)
        signature = state['destination_token']
        self.assertRegex(signature, r'^[0-9a-f]{64}$')
        self.assertNotIn(REFRESH, signature)
        self.assertEqual(oauth.runtime_config(request, expected_destination=signature).expected_permission_id,
                         IDENTITY['user']['permissionId'])
        for invalid in ('', 'a' * 64, 'malformed', None):
            # None is the non-Web compatibility mode; Web always supplies a str.
            if invalid is None:
                continue
            with self.subTest(value=invalid), self.assertRaises(oauth.DriveOAuthError):
                oauth.runtime_config(request, expected_destination=invalid)

    def test_old_archive_form_cannot_send_to_a_newly_selected_connection(self):
        request = self.connected()
        old_signature = oauth.status(request)['destination_token']
        self.http.side_effect = [TOKEN_RESPONSE.copy(), IDENTITY.copy()]
        request, values = self.start(request)
        oauth.complete(self.callback(request, values))
        new_signature = oauth.status(request)['destination_token']
        self.assertNotEqual(old_signature, new_signature)
        with self.assertRaises(oauth.DriveOAuthError):
            oauth.runtime_config(request, expected_destination=old_signature)
        self.assertEqual(oauth.runtime_config(request, expected_destination=new_signature).refresh_token, REFRESH)

    def test_disconnect_binding_error_does_not_leave_an_unbound_local_pointer(self):
        request = self.connected()
        request.session.flush()
        request.session[oauth.CONNECTION_KEY] = 'drivec_' + '0' * 32
        oauth.disconnect(request)
        self.assertNotIn(oauth.CONNECTION_KEY, request.session)
        self.assertNotIn(oauth.PENDING_KEY, request.session)

    def test_callback_refuses_exchange_before_a_surrounding_transaction_commits(self):
        request, values = self.start()
        pointer = request.session[oauth.PENDING_KEY]
        with transaction.atomic():
            with self.assertRaises(oauth.DriveOAuthError) as error:
                oauth.complete(self.callback(request, values))
        self.assertEqual(error.exception.code, 'state')
        self.assertTrue(Session.objects.filter(session_key=pointer).exists())
        self.http.assert_not_called()

    def test_database_failure_does_not_report_successful_vault_disconnection(self):
        request = self.connected()
        pointer = request.session[oauth.CONNECTION_KEY]
        self.http.reset_mock()
        with patch.object(Session.objects, 'select_for_update', side_effect=RuntimeError('synthetic DB error')):
            with self.assertRaises(oauth.DriveOAuthError) as error:
                oauth.disconnect(request)
        self.assertEqual(error.exception.code, 'session')
        self.assertTrue(Session.objects.filter(session_key=pointer).exists())
        self.http.assert_not_called()
