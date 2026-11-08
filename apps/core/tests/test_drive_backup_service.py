"""Synthetic-only regressions: all PostgreSQL, GPG and HTTPS operations are mocks."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.core import drive_backup as service


CONFIG = service.Config(
    True, 'synthetic.apps.googleusercontent.com',
    'synthetic-client-secret-012345', 'synthetic-refresh-token-0123456789',
    'synthetic-only-passphrase-01234567890123456789',
)
CONFIG_SETTINGS = {
    'DRIVE_BACKUP_ENABLED': True,
    'DRIVE_BACKUP_CLIENT_ID': CONFIG.client_id,
    'DRIVE_BACKUP_CLIENT_SECRET': CONFIG.client_secret,
    'DRIVE_BACKUP_REFRESH_TOKEN': CONFIG.refresh_token,
    'DRIVE_BACKUP_PASSPHRASE': CONFIG.passphrase,
}
TOOLS = {
    'pg_dump': '/synthetic/pg_dump', 'pg_restore': '/synthetic/pg_restore',
    'gpg': '/synthetic/gpg', 'pg_dump_major': 18, 'pg_restore_major': 18,
}
DATABASE = {
    'ENGINE': 'django.db.backends.postgresql', 'NAME': 'synthetic',
    'HOST': 'synthetic-db.invalid', 'PORT': '5432', 'USER': 'synthetic_reader',
    'PASSWORD': 'synthetic-database-secret', 'OPTIONS': {'sslmode': 'require'},
}
FOLDER_ID = 'synthetic_folder_12345'
FILE_ID = 'synthetic_cipher_12345'


def private_file(**extra):
    return {
        'id': FOLDER_ID, 'mimeType': service.FOLDER_MIME, 'trashed': False,
        'appProperties': service.MARKER.copy(),
        'permissions': [{'type': 'user', 'role': 'owner'}], **extra,
    }


@contextmanager
def fake_postgres(*, locked=True, version=180006, size=1024):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (locked, version, size)
    database = SimpleNamespace(vendor='postgresql', cursor=MagicMock(return_value=cursor))
    with patch.object(service, 'connection', database), patch.object(
        service.transaction, 'atomic', return_value=nullcontext(),
    ):
        yield cursor


class DriveBackupConfigurationTests(SimpleTestCase):
    @override_settings(DRIVE_BACKUP_LOCAL_CONNECTION_TEST=True)
    def test_connection_only_mode_forbids_archive_before_config_tools_or_network(self):
        with patch.object(service.Config, 'from_settings') as config, patch.object(service, '_tools') as tools:
            for supplied in (None, CONFIG):
                with self.assertRaises(service.DriveBackupError) as error:
                    service.create_drive_backup(config=supplied)
                self.assertEqual(error.exception.code, 'disabled')
        config.assert_not_called()
        tools.assert_not_called()

    def test_connection_only_flag_cannot_be_enabled_by_environment_or_truthy_strings(self):
        for value in ('true', '1', 1, False, None):
            with self.subTest(value=value), override_settings(DRIVE_BACKUP_LOCAL_CONNECTION_TEST=value):
                self.assertFalse(service.connection_test_only())
        with patch.object(service, 'settings', SimpleNamespace()), patch.dict(os.environ, {
            'DRIVE_BACKUP_LOCAL_CONNECTION_TEST': 'true',
        }):
            self.assertFalse(service.connection_test_only())

    @override_settings(DRIVE_BACKUP_ENABLED=False)
    def test_disabled_configuration_needs_no_credentials(self):
        self.assertFalse(service.Config.from_settings().enabled)

    @override_settings(**CONFIG_SETTINGS)
    def test_valid_configuration(self):
        actual = service.Config.from_settings()
        self.assertTrue(actual.enabled)
        self.assertEqual(actual.passphrase, CONFIG.passphrase)

    def test_environment_fallback_does_not_read_dotenv(self):
        values = {key: str(value).lower() if isinstance(value, bool) else value
                  for key, value in CONFIG_SETTINGS.items()}
        with patch.object(service, 'settings', SimpleNamespace()), patch.dict(os.environ, values):
            self.assertEqual(service.Config.from_settings().refresh_token, CONFIG.refresh_token)

    def test_settings_take_precedence_over_environment(self):
        with override_settings(DRIVE_BACKUP_ENABLED=False), patch.dict(os.environ, {
            'DRIVE_BACKUP_ENABLED': 'true',
        }):
            self.assertFalse(service.Config.from_settings().enabled)

    @override_settings(**CONFIG_SETTINGS)
    def test_missing_each_credential_is_rejected(self):
        for name in CONFIG_SETTINGS:
            if name == 'DRIVE_BACKUP_ENABLED':
                continue
            with self.subTest(name=name), override_settings(**{name: ''}):
                with self.assertRaises(service.DriveBackupError) as caught:
                    service.Config.from_settings()
                self.assertEqual(caught.exception.code, 'configuration')

    @override_settings(**CONFIG_SETTINGS)
    def test_whitespace_control_characters_and_oversized_values_are_rejected(self):
        for value in (' value', 'value ', 'value\nsecret', 'value\rsecret',
                      'value\0secret', 'x' * 4097, None, 123):
            with self.subTest(value_type=type(value).__name__), override_settings(
                DRIVE_BACKUP_REFRESH_TOKEN=value,
            ):
                with self.assertRaises(service.DriveBackupError):
                    service.Config.from_settings()

    @override_settings(**CONFIG_SETTINGS)
    def test_short_passphrase_is_rejected(self):
        with override_settings(DRIVE_BACKUP_PASSPHRASE='synthetic-short'):
            with self.assertRaises(service.DriveBackupError):
                service.Config.from_settings()

    @override_settings(**CONFIG_SETTINGS)
    def test_client_id_must_be_google_oauth_client(self):
        with override_settings(DRIVE_BACKUP_CLIENT_ID='synthetic.attacker.invalid'):
            with self.assertRaises(service.DriveBackupError):
                service.Config.from_settings()

    def test_invalid_feature_flag_is_rejected(self):
        with override_settings(DRIVE_BACKUP_ENABLED='enabled'):
            with self.assertRaises(service.DriveBackupError):
                service.Config.from_settings()

    def test_config_repr_does_not_disclose_credentials(self):
        rendered = repr(CONFIG)
        for value in (CONFIG.client_secret, CONFIG.refresh_token, CONFIG.passphrase):
            self.assertNotIn(value, rendered)

    def test_errors_are_fixed_and_unknown_code_is_not_echoed(self):
        error = service.DriveBackupError(CONFIG.refresh_token)
        self.assertEqual(error.code, 'drive_transfer')
        self.assertNotIn(CONFIG.refresh_token, str(error))
        self.assertEqual(str(error), service.ERRORS['drive_transfer'])

    @override_settings(DRIVE_BACKUP_ENABLED=False)
    def test_availability_disabled_has_no_database_network_or_subprocess_access(self):
        with patch.object(service, '_tools') as tools, patch.object(
            service, '_database_guard',
        ) as guard, patch.object(service, 'DriveClient') as client:
            result = service.availability()
        self.assertFalse(result['ready'])
        tools.assert_not_called()
        guard.assert_not_called()
        client.assert_not_called()

    @override_settings(**CONFIG_SETTINGS)
    def test_availability_reports_presence_only_without_operational_side_effects(self):
        with patch.object(service, '_tools') as tools, patch.object(service, 'DriveClient') as client:
            result = service.availability()
        self.assertTrue(result['configured'])
        tools.assert_not_called()
        client.assert_not_called()

    @override_settings(DRIVE_BACKUP_ENABLED=True, DRIVE_BACKUP_REFRESH_TOKEN='')
    def test_availability_invalid_configuration_is_safe(self):
        result = service.availability()
        self.assertFalse(result['ready'])
        self.assertEqual(result['issues'], [service.ERRORS['configuration']])


class DriveBackupSourceSafetyTests(SimpleTestCase):
    def test_deadline_expiration_is_explicit(self):
        with patch.object(service.time, 'monotonic', side_effect=[100, 181]):
            deadline = service.Deadline(80)
            with self.assertRaises(service.DriveBackupError) as caught:
                deadline.remaining()
        self.assertEqual(caught.exception.code, 'timeout')

    def test_deadline_caps_individual_operation(self):
        with patch.object(service.time, 'monotonic', side_effect=[100, 101]):
            self.assertEqual(service.Deadline(80).remaining(3), 3)

    def test_subprocess_timeout_has_no_sensitive_exception_details(self):
        with patch.object(service.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
            ['synthetic', CONFIG.passphrase], 1,
        )):
            with self.assertRaises(service.DriveBackupError) as caught:
                service._run(['synthetic'], {}, service.Deadline(), 'dump')
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertNotIn(CONFIG.passphrase, str(caught.exception))

    def test_subprocess_failure_has_no_stderr_or_sensitive_details(self):
        with patch.object(service.subprocess, 'run', side_effect=subprocess.CalledProcessError(
            1, ['synthetic'], stderr=CONFIG.refresh_token.encode(),
        )):
            with self.assertRaises(service.DriveBackupError) as caught:
                service._run(['synthetic'], {}, service.Deadline(), 'dump')
        self.assertEqual(caught.exception.code, 'dump')
        self.assertNotIn(CONFIG.refresh_token, str(caught.exception))

    def test_command_passphrase_uses_stdin_not_process_arguments(self):
        phrase = CONFIG.passphrase.encode()
        with patch.object(service.subprocess, 'run', return_value=SimpleNamespace(stdout=b'ok')) as run:
            self.assertEqual(service._run(['synthetic', '--passphrase-fd', '0'], {},
                                         service.Deadline(), 'encryption', phrase=phrase), b'ok')
        args, kwargs = run.call_args
        self.assertNotIn(CONFIG.passphrase, repr(args[0]))
        self.assertEqual(kwargs['input'], phrase)
        self.assertTrue(kwargs['check'])
        self.assertTrue(kwargs['capture_output'])

    def test_subprocess_environment_excludes_inherited_cloud_and_pg_overrides(self):
        with patch.dict(os.environ, {
            'PGHOSTADDR': 'synthetic.invalid', 'PGSERVICE': 'synthetic',
            'AWS_SECRET_ACCESS_KEY': 'synthetic', 'GOOGLE_APPLICATION_CREDENTIALS': 'synthetic',
            'GNUPGHOME': 'synthetic',
        }):
            result = service._environment()
        for name in ('PGHOSTADDR', 'PGSERVICE', 'AWS_SECRET_ACCESS_KEY',
                     'GOOGLE_APPLICATION_CREDENTIALS', 'GNUPGHOME'):
            self.assertNotIn(name, result)

    def test_compatible_native_tools_are_identified_without_real_execution(self):
        with patch.object(service.shutil, 'which', side_effect=lambda name: '/synthetic/' + name), patch.object(
            service, '_run', side_effect=[b'pg_dump (PostgreSQL) 18.6',
                                         b'pg_restore (PostgreSQL) 18.6', b'gpg (GnuPG) 2.4.7'],
        ):
            tools = service._tools(service.Deadline())
        self.assertEqual(tools['pg_dump_major'], 18)
        self.assertEqual(tools['pg_restore_major'], 18)

    def test_missing_native_tool_is_rejected(self):
        with patch.object(service.shutil, 'which', return_value=None):
            with self.assertRaises(service.DriveBackupError) as caught:
                service._tools(service.Deadline())
        self.assertEqual(caught.exception.code, 'tools')

    def test_sqlite_is_never_a_production_source_fallback(self):
        with patch.object(service, 'connection', SimpleNamespace(vendor='sqlite')):
            with self.assertRaises(service.DriveBackupError) as caught:
                with service._database_guard(TOOLS):
                    self.fail('SQLite source must not be accepted')
        self.assertEqual(caught.exception.code, 'database')

    def test_database_guard_is_read_only_and_uses_transaction_scoped_lock(self):
        with fake_postgres() as cursor:
            with service._database_guard(TOOLS) as major:
                self.assertEqual(major, 18)
        commands = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(commands[0], 'SET TRANSACTION READ ONLY')
        self.assertIn('pg_try_advisory_xact_lock', commands[-1])
        self.assertIn('pg_database_size', commands[-1])
        self.assertTrue(any('statement_timeout' in command for command in commands))

    def test_locked_source_prevents_second_backup(self):
        with fake_postgres(locked=False):
            with self.assertRaises(service.DriveBackupError) as caught:
                with service._database_guard(TOOLS):
                    self.fail('Concurrent backup must not proceed')
        self.assertEqual(caught.exception.code, 'busy')

    def test_pg17_client_is_rejected_for_pg18_source(self):
        with fake_postgres():
            with self.assertRaises(service.DriveBackupError) as caught:
                with service._database_guard(TOOLS | {'pg_dump_major': 17}):
                    self.fail('Incompatible client must not proceed')
        self.assertEqual(caught.exception.code, 'postgres_version')

    def test_restore_client_must_also_match_source_major(self):
        with fake_postgres():
            with self.assertRaises(service.DriveBackupError) as caught:
                with service._database_guard(TOOLS | {'pg_restore_major': 17}):
                    self.fail('Incompatible client must not proceed')
        self.assertEqual(caught.exception.code, 'postgres_version')

    def test_database_volume_bound(self):
        for size in (0, service.MAX_DATABASE_BYTES + 1):
            with self.subTest(size=size), fake_postgres(size=size):
                with self.assertRaises(service.DriveBackupError) as caught:
                    with service._database_guard(TOOLS):
                        self.fail('Unsafe source size must not proceed')
                self.assertEqual(caught.exception.code, 'too_large')

    @override_settings(DATABASES={'default': DATABASE})
    def test_database_credentials_use_environment_and_require_tls(self):
        environment = service._postgres_environment()
        self.assertEqual(environment['PGPASSWORD'], DATABASE['PASSWORD'])
        self.assertEqual(environment['PGSSLMODE'], 'require')
        self.assertIn('default_transaction_read_only=on', environment['PGOPTIONS'])

    def test_database_tls_and_unsupported_options_are_rejected(self):
        for options in ({'sslmode': 'disable'}, {'sslmode': 'require', 'service': 'synthetic'}):
            with self.subTest(options=options), override_settings(DATABASES={
                'default': DATABASE | {'OPTIONS': options},
            }):
                with self.assertRaises(service.DriveBackupError) as caught:
                    service._postgres_environment()
                self.assertEqual(caught.exception.code, 'database')

    def test_database_connection_parameter_injection_is_rejected(self):
        for field, value in (('HOST', 'host=synthetic'), ('NAME', 'postgresql://synthetic.invalid'),
                             ('USER', 'synthetic\nuser'), ('PASSWORD', 'synthetic\0password')):
            with self.subTest(field=field), override_settings(DATABASES={
                'default': DATABASE | {field: value},
            }):
                with self.assertRaises(service.DriveBackupError):
                    service._postgres_environment()


class DriveBackupEncryptionTests(SimpleTestCase):
    def make_archive(self, directory, *, ciphertext=b'\x8csynthetic-ciphertext',
                     decrypt_matches=True, dump_content=b'PGDMPsynthetic-database'):
        calls = []

        def synthetic_run(command, environment, deadline, code, **kwargs):
            calls.append((command, environment, code, kwargs))
            if '--format=custom' in command:
                Path(command[command.index('--file') + 1]).write_bytes(dump_content)
            elif '--symmetric' in command:
                Path(command[command.index('--output') + 1]).write_bytes(ciphertext)
            elif '--decrypt' in command:
                return (directory / 'database-backup.tar').read_bytes() if decrypt_matches else b'tampered'
            return b''

        with patch.object(service, '_postgres_environment', return_value={}), patch.object(
            service, '_run', side_effect=synthetic_run,
        ):
            archive = service._encrypted_archive(directory, CONFIG, TOOLS, service.Deadline(), 18)
        return archive, calls

    def test_archive_is_explicit_database_only_and_gpg_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, calls = self.make_archive(directory)
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['scope'], 'database_only')
            self.assertFalse(manifest['media_included'])
            self.assertFalse(manifest['restore_verified'])
            self.assertEqual(manifest['postgresql_major'], 18)
            self.assertEqual(manifest['database_sha256'],
                             hashlib.sha256(b'PGDMPsynthetic-database').hexdigest())
            self.assertEqual(archive.suffix, '.gpg')
            self.assertTrue(any('--list' in command for command, *_ in calls))
            self.assertTrue(any('--decrypt' in command for command, *_ in calls))

    def test_plaintext_renamed_as_gpg_is_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(service.DriveBackupError) as caught:
                self.make_archive(Path(temporary), ciphertext=b'plaintext-not-a-gpg-envelope')
        self.assertEqual(caught.exception.code, 'encryption')

    def test_decryption_roundtrip_must_match_original_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(service.DriveBackupError) as caught:
                self.make_archive(Path(temporary), decrypt_matches=False)
        self.assertEqual(caught.exception.code, 'encryption')

    def test_dump_magic_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(service.DriveBackupError) as caught:
                self.make_archive(Path(temporary), dump_content=b'not-a-postgres-dump')
        self.assertEqual(caught.exception.code, 'dump')

    def test_gpg_uses_passphrase_stdin_and_private_home_without_plaintext_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, calls = self.make_archive(directory)
            encryption = next(item for item in calls if '--symmetric' in item[0])
            command, environment, _, kwargs = encryption
            self.assertNotIn(CONFIG.passphrase, repr(command))
            self.assertEqual(kwargs['phrase'], (CONFIG.passphrase + '\n').encode())
            self.assertEqual(environment['GNUPGHOME'], str(directory / 'gnupg'))
            self.assertIn('--no-options', command)
            self.assertIn('--no-symkey-cache', command)
            self.assertIn('AES256', command)


class DriveBackupTransportTests(SimpleTestCase):
    def drive_client(self):
        result = service.DriveClient(CONFIG, service.Deadline())
        result.token = 'synthetic-access-token-0123456789'
        return result

    def response(self, *, status=200, chunks=None):
        client = MagicMock()
        client.getresponse.return_value.status = status
        client.getresponse.return_value.read1.side_effect = chunks or [b'{}', b'']
        return client

    def test_selected_account_is_rechecked_with_the_existing_drive_scope(self):
        client = service.DriveClient(replace(CONFIG, expected_permission_id='synthetic-owner'), service.Deadline())
        with patch.object(client, '_request', return_value={'user': {'permissionId': 'synthetic-owner'}}) as request:
            client.verify_account()
        self.assertEqual(request.call_args.args[:2], ('GET', 'www.googleapis.com'))
        self.assertIn('/drive/v3/about?', request.call_args.args[2])
        self.assertIn('permissionId', request.call_args.args[2])

    def test_wrong_or_absent_selected_account_is_rejected(self):
        client = service.DriveClient(replace(CONFIG, expected_permission_id='synthetic-owner'), service.Deadline())
        for response in ({}, {'user': {}}, {'user': {'permissionId': 'another-owner'}}):
            with self.subTest(response=response), patch.object(client, '_request', return_value=response):
                with self.assertRaises(service.DriveBackupError) as caught:
                    client.verify_account()
                self.assertEqual(caught.exception.code, 'drive_auth')

    def test_standalone_compatibility_does_not_invent_an_account_identity(self):
        client = self.drive_client()
        with patch.object(client, '_request') as request:
            client.verify_account()
        request.assert_not_called()

    def test_malformed_selected_account_fails_before_network(self):
        client = service.DriveClient(replace(CONFIG, expected_permission_id='bad/id?token=secret'), service.Deadline())
        with patch.object(client, '_request') as request:
            with self.assertRaises(service.DriveBackupError):
                client.verify_account()
        request.assert_not_called()

    def test_request_uses_https_google_host_and_closes_connection(self):
        fake = self.response()
        with patch.object(service.http.client, 'HTTPSConnection', return_value=fake) as connection:
            result = self.drive_client()._request('GET', 'www.googleapis.com', '/drive/v3/files')
        self.assertEqual(result, {})
        self.assertEqual(connection.call_args.args[0], 'www.googleapis.com')
        self.assertGreater(connection.call_args.kwargs['timeout'], 0)
        self.assertIsNotNone(connection.call_args.kwargs['context'])
        fake.close.assert_called_once()

    def test_non_google_host_is_rejected_before_connection(self):
        with patch.object(service.http.client, 'HTTPSConnection') as connection:
            with self.assertRaises(service.DriveBackupError):
                self.drive_client()._request('GET', 'attacker.invalid', '/upload')
        connection.assert_not_called()

    def test_authenticated_request_requires_access_token_before_connection(self):
        client = service.DriveClient(CONFIG, service.Deadline())
        with patch.object(service.http.client, 'HTTPSConnection') as connection:
            with self.assertRaises(service.DriveBackupError) as caught:
                client._request('GET', 'www.googleapis.com', '/drive/v3/files')
        self.assertEqual(caught.exception.code, 'drive_auth')
        connection.assert_not_called()

    def test_non_absolute_request_path_is_rejected_before_connection(self):
        with patch.object(service.http.client, 'HTTPSConnection') as connection:
            with self.assertRaises(service.DriveBackupError):
                self.drive_client()._request('GET', 'www.googleapis.com', 'https://attacker.invalid')
        connection.assert_not_called()

    def test_redirect_is_not_followed(self):
        fake = self.response(status=302)
        with patch.object(service.http.client, 'HTTPSConnection', return_value=fake) as connection:
            with self.assertRaises(service.DriveBackupError) as caught:
                self.drive_client()._request('GET', 'www.googleapis.com', '/drive/v3/files')
        self.assertEqual(caught.exception.code, 'drive_transfer')
        connection.assert_called_once()
        fake.close.assert_called_once()

    def test_http_status_errors_are_fixed_and_do_not_echo_provider_body(self):
        for status, code in ((401, 'drive_auth'), (403, 'drive_quota'), (429, 'drive_quota'),
                             (500, 'drive_transfer')):
            fake = self.response(status=status, chunks=[CONFIG.refresh_token.encode(), b''])
            with self.subTest(status=status), patch.object(
                service.http.client, 'HTTPSConnection', return_value=fake,
            ):
                with self.assertRaises(service.DriveBackupError) as caught:
                    self.drive_client()._request('GET', 'www.googleapis.com', '/drive/v3/files')
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn(CONFIG.refresh_token, str(caught.exception))
                fake.getresponse.return_value.read1.assert_not_called()

    def test_oversized_provider_response_is_rejected(self):
        fake = self.response(chunks=[b'x' * 4096] * 17 + [b''])
        with patch.object(service.http.client, 'HTTPSConnection', return_value=fake):
            with self.assertRaises(service.DriveBackupError):
                self.drive_client()._request('GET', 'www.googleapis.com', '/drive/v3/files')
        fake.close.assert_called_once()

    def test_invalid_json_or_non_object_provider_response_is_rejected(self):
        for body in (b'invalid', b'[]', b'null'):
            with self.subTest(body=body), patch.object(
                service.http.client, 'HTTPSConnection', return_value=self.response(chunks=[body, b'']),
            ):
                with self.assertRaises(service.DriveBackupError):
                    self.drive_client()._request('GET', 'www.googleapis.com', '/drive/v3/files')

    def test_network_exception_does_not_leak_credentials(self):
        fake = self.response()
        fake.request.side_effect = OSError(CONFIG.client_secret)
        with patch.object(service.http.client, 'HTTPSConnection', return_value=fake):
            with self.assertRaises(service.DriveBackupError) as caught:
                self.drive_client()._request('GET', 'www.googleapis.com', '/drive/v3/files')
        self.assertNotIn(CONFIG.client_secret, str(caught.exception))
        fake.close.assert_called_once()

    def test_authenticate_requests_only_fixed_google_token_endpoint(self):
        client = self.drive_client()
        with patch.object(client, '_request', return_value={
            'scope': service.SCOPE, 'token_type': 'Bearer',
            'access_token': 'synthetic-access-token-0123456789',
        }) as request:
            client.authenticate()
        args, kwargs = request.call_args
        self.assertEqual(args, ('POST', 'oauth2.googleapis.com', '/token'))
        self.assertFalse(kwargs['auth'])
        self.assertIn(b'grant_type=refresh_token', kwargs['body'])

    def test_authenticate_rejects_broader_or_missing_drive_scope(self):
        for scope in ('', 'https://www.googleapis.com/auth/drive',
                      service.SCOPE + ' https://www.googleapis.com/auth/drive.readonly'):
            client = self.drive_client()
            with self.subTest(scope=scope), patch.object(client, '_request', return_value={
                'scope': scope, 'token_type': 'Bearer',
                'access_token': 'synthetic-access-token-0123456789',
            }):
                with self.assertRaises(service.DriveBackupError) as caught:
                    client.authenticate()
                self.assertEqual(caught.exception.code, 'drive_auth')

    def test_invalid_token_is_rejected_without_echo(self):
        client = self.drive_client()
        with patch.object(client, '_request', return_value={
            'scope': service.SCOPE, 'token_type': 'Bearer', 'access_token': 'synthetic\r\ninjection',
        }):
            with self.assertRaises(service.DriveBackupError) as caught:
                client.authenticate()
        self.assertNotIn('injection', str(caught.exception))

    def test_malformed_scope_and_token_type_are_rejected_cleanly(self):
        baseline = {'scope': service.SCOPE, 'token_type': 'Bearer',
                    'access_token': 'synthetic-access-token-0123456789'}
        for change in ({'scope': []}, {'scope': None}, {'token_type': []},
                       {'token_type': None}, {'access_token': []}):
            client = self.drive_client()
            with self.subTest(change=change), patch.object(client, '_request', return_value=baseline | change):
                with self.assertRaises(service.DriveBackupError) as caught:
                    client.authenticate()
                self.assertEqual(caught.exception.code, 'drive_auth')

    def test_file_id_cannot_inject_query_or_external_url(self):
        client = self.drive_client()
        for value in ('https://attacker.invalid', '../synthetic', 'synthetic?fields=*', None):
            with self.subTest(value=value), patch.object(client, '_request') as request:
                with self.assertRaises(service.DriveBackupError):
                    client._file(value)
                request.assert_not_called()


class DriveBackupFolderAndUploadTests(SimpleTestCase):
    def drive_client(self):
        return service.DriveClient(CONFIG, service.Deadline())

    def test_existing_private_app_owned_folder_is_reused(self):
        client = self.drive_client()
        with patch.object(client, '_request', return_value={'files': [{'id': FOLDER_ID}]}) as request, patch.object(
            client, '_file', return_value=private_file(),
        ):
            self.assertEqual(client.folder(), FOLDER_ID)
        self.assertEqual(request.call_args.args[0], 'GET')
        self.assertIn('appProperties', request.call_args.args[2])

    def test_folder_is_created_privately_without_sharing_permissions(self):
        client = self.drive_client()
        with patch.object(client, '_request', side_effect=[{'files': []}, {'id': FOLDER_ID}]) as request, patch.object(
            client, '_file', return_value=private_file(),
        ):
            self.assertEqual(client.folder(), FOLDER_ID)
        creation = request.call_args_list[1]
        self.assertEqual(creation.args, (
            'POST', 'www.googleapis.com', '/drive/v3/files?ignoreDefaultVisibility=true&fields=id',
        ))
        metadata = json.loads(creation.kwargs['body'])
        self.assertEqual(metadata['mimeType'], service.FOLDER_MIME)
        self.assertEqual(metadata['appProperties'], service.MARKER)
        self.assertNotIn('permissions', metadata)

    def test_created_folder_is_still_rejected_if_permissions_are_not_private(self):
        client = self.drive_client()
        shared = private_file(permissions=[{'type': 'user', 'role': 'owner'},
                                           {'type': 'domain', 'role': 'reader'}])
        with patch.object(client, '_request', side_effect=[{'files': []}, {'id': FOLDER_ID}]) as request, \
                patch.object(client, '_file', return_value=shared), self.assertRaises(service.DriveBackupError) as error:
            client.folder()
        self.assertEqual(error.exception.code, 'drive_folder')
        self.assertEqual(request.call_args_list[1].args[2],
                         '/drive/v3/files?ignoreDefaultVisibility=true&fields=id')

    def test_ambiguous_or_paginated_folder_search_is_rejected(self):
        for listing in ({'files': [{'id': FOLDER_ID}, {'id': FILE_ID}]},
                        {'files': [], 'nextPageToken': 'synthetic-next'}, {'files': None}):
            client = self.drive_client()
            with self.subTest(listing=listing), patch.object(client, '_request', return_value=listing):
                with self.assertRaises(service.DriveBackupError) as caught:
                    client.folder()
                self.assertEqual(caught.exception.code, 'drive_folder')

    def test_malformed_folder_search_item_is_rejected_cleanly(self):
        client = self.drive_client()
        with patch.object(client, '_request', return_value={'files': ['synthetic-not-an-object']}):
            with self.assertRaises(service.DriveBackupError) as caught:
                client.folder()
        self.assertEqual(caught.exception.code, 'drive_folder')

    def test_malformed_permission_entry_is_not_treated_as_private(self):
        for permissions in ([None], ['owner'], {}, None, []):
            with self.subTest(permissions=permissions):
                self.assertFalse(service.DriveClient._private(private_file(permissions=permissions)))

    def test_shared_trashed_or_unmarked_folders_are_rejected(self):
        for item in (private_file(trashed=True), private_file(appProperties={}),
                     private_file(permissions=[{'type': 'anyone', 'role': 'reader'}]),
                     private_file(permissions=[{'type': 'user', 'role': 'owner'},
                                               {'type': 'user', 'role': 'reader'}])):
            client = self.drive_client()
            with self.subTest(item=item), patch.object(client, '_request', return_value={
                'files': [{'id': FOLDER_ID}],
            }), patch.object(client, '_file', return_value=item):
                with self.assertRaises(service.DriveBackupError) as caught:
                    client.folder()
                self.assertEqual(caught.exception.code, 'drive_folder')

    def test_only_encrypted_file_is_sent_and_remote_metadata_is_verified(self):
        client = self.drive_client()
        with tempfile.TemporaryDirectory() as temporary:
            encrypted = Path(temporary) / 'synthetic.tar.gpg'
            payload = b'\x8csynthetic-ciphertext'
            encrypted.write_bytes(payload)
            verified = private_file(id=FILE_ID, parents=[FOLDER_ID], size=str(len(payload)),
                                    sha256Checksum=hashlib.sha256(payload).hexdigest())
            with patch.object(client, '_request', return_value={'id': FILE_ID}) as request, patch.object(
                client, '_file', return_value=verified,
            ):
                result = client.upload(encrypted, FOLDER_ID)
            self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertFalse(result['restore_verified'])
            self.assertEqual(result['scope'], 'database_only')
            self.assertEqual(request.call_args.args, (
                'POST', 'www.googleapis.com',
                '/upload/drive/v3/files?uploadType=multipart&ignoreDefaultVisibility=true&fields=id',
            ))
            self.assertIn(payload, request.call_args.kwargs['body'])
            for value in (CONFIG.passphrase.encode(), CONFIG.refresh_token.encode()):
                self.assertNotIn(value, request.call_args.kwargs['body'])

    def test_plaintext_extension_and_empty_file_are_not_uploaded(self):
        client = self.drive_client()
        with tempfile.TemporaryDirectory() as temporary:
            for name, payload in (('synthetic.dump', b'PGDMPsynthetic'), ('synthetic.gpg', b'')):
                candidate = Path(temporary) / name
                candidate.write_bytes(payload)
                with self.subTest(name=name), patch.object(client, '_request') as request:
                    with self.assertRaises(service.DriveBackupError):
                        client.upload(candidate, FOLDER_ID)
                    request.assert_not_called()

    def test_plaintext_disguised_as_gpg_is_not_uploaded(self):
        client = self.drive_client()
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'renamed-plaintext.gpg'
            candidate.write_bytes(b'PGDMPsynthetic-plaintext')
            with patch.object(client, '_request') as request:
                with self.assertRaises(service.DriveBackupError):
                    client.upload(candidate, FOLDER_ID)
            request.assert_not_called()

    def test_upload_name_cannot_contain_multipart_control_characters(self):
        client = self.drive_client()
        # Quoted/non-ASCII names are legal on all target filesystems, but are
        # not permitted in the fixed multipart backup-upload contract.
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'synthetic-é.gpg'
            candidate.write_bytes(b'\x8csynthetic-ciphertext')
            with patch.object(client, '_request') as request:
                with self.assertRaises(service.DriveBackupError):
                    client.upload(candidate, FOLDER_ID)
            request.assert_not_called()

    def test_symbolic_link_is_rejected_before_reading_ciphertext(self):
        client = self.drive_client()
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'synthetic.gpg'
            candidate.write_bytes(b'\x8csynthetic-ciphertext')
            with patch.object(Path, 'is_symlink', return_value=True), patch.object(client, '_request') as request:
                with self.assertRaises(service.DriveBackupError):
                    client.upload(candidate, FOLDER_ID)
            request.assert_not_called()

    def test_ciphertext_over_web_size_limit_is_not_uploaded(self):
        client = self.drive_client()
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'synthetic.gpg'
            candidate.write_bytes(b'\x8c' + b'x' * service.MAX_CIPHER_BYTES)
            with patch.object(client, '_request') as request:
                with self.assertRaises(service.DriveBackupError):
                    client.upload(candidate, FOLDER_ID)
            request.assert_not_called()

    def test_digest_size_parent_marker_and_privacy_mismatches_are_failures(self):
        client = self.drive_client()
        with tempfile.TemporaryDirectory() as temporary:
            encrypted = Path(temporary) / 'synthetic.gpg'
            payload = b'\x8csynthetic-ciphertext'
            encrypted.write_bytes(payload)
            baseline = private_file(id=FILE_ID, parents=[FOLDER_ID], size=str(len(payload)),
                                    sha256Checksum=hashlib.sha256(payload).hexdigest())
            for change in ({'sha256Checksum': '0' * 64}, {'size': '999'},
                           {'parents': ['unexpected_synthetic_folder']}, {'appProperties': {}},
                           {'permissions': [{'type': 'anyone', 'role': 'reader'}]}):
                with self.subTest(change=change), patch.object(client, '_request', return_value={
                    'id': FILE_ID,
                }), patch.object(client, '_file', return_value=baseline | change):
                    with self.assertRaises(service.DriveBackupError) as caught:
                        client.upload(encrypted, FOLDER_ID)
                    self.assertEqual(caught.exception.code, 'integrity')


class DriveBackupOrchestrationTests(SimpleTestCase):
    @override_settings(**CONFIG_SETTINGS)
    def test_wrong_selected_account_prevents_archive_and_folder_creation(self):
        with patch.object(service, '_tools', return_value=TOOLS), patch.object(
            service, '_postgres_environment', return_value={},
        ), patch.object(service, '_database_guard', return_value=nullcontext(18)), patch.object(
            service, 'DriveClient',
        ) as client, patch.object(service, '_encrypted_archive') as archive:
            client.return_value.verify_account.side_effect = service.DriveBackupError('drive_auth')
            with self.assertRaises(service.DriveBackupError):
                service.create_drive_backup(config=replace(CONFIG, expected_permission_id='synthetic-owner'))
        client.return_value.folder.assert_not_called()
        client.return_value.upload.assert_not_called()
        archive.assert_not_called()

    @override_settings(DRIVE_BACKUP_ENABLED=False)
    def test_disabled_feature_stops_before_tools_database_or_google(self):
        with patch.object(service, '_tools') as tools, patch.object(
            service, '_database_guard',
        ) as guard, patch.object(service, 'DriveClient') as client:
            with self.assertRaises(service.DriveBackupError) as caught:
                service.create_drive_backup()
        self.assertEqual(caught.exception.code, 'disabled')
        tools.assert_not_called()
        guard.assert_not_called()
        client.assert_not_called()

    @override_settings(DRIVE_BACKUP_ENABLED=True, DRIVE_BACKUP_PASSPHRASE='')
    def test_invalid_configuration_stops_before_any_source_or_destination_access(self):
        with patch.object(service, '_tools') as tools, patch.object(service, 'DriveClient') as client:
            with self.assertRaises(service.DriveBackupError):
                service.create_drive_backup()
        tools.assert_not_called()
        client.assert_not_called()

    @override_settings(**CONFIG_SETTINGS)
    def test_unsafe_database_configuration_prevents_google_access(self):
        with patch.object(service, '_tools', return_value=TOOLS), patch.object(
            service, '_postgres_environment', side_effect=service.DriveBackupError('database'),
        ), patch.object(service, 'DriveClient') as client:
            with self.assertRaises(service.DriveBackupError):
                service.create_drive_backup()
        client.assert_not_called()

    @override_settings(**CONFIG_SETTINGS)
    def test_no_upload_if_archive_creation_fails_and_temporaries_are_removed(self):
        directories = []

        def failing_archive(directory, *_args):
            directories.append(directory)
            (directory / 'database.dump').write_bytes(b'PGDMPsynthetic')
            raise service.DriveBackupError('encryption')

        with patch.object(service, '_tools', return_value=TOOLS), patch.object(
            service, '_postgres_environment', return_value={},
        ), patch.object(service, '_database_guard', return_value=nullcontext(18)), patch.object(
            service, 'DriveClient',
        ) as client, patch.object(service, '_encrypted_archive', side_effect=failing_archive):
            with self.assertRaises(service.DriveBackupError) as caught:
                service.create_drive_backup()
        self.assertEqual(caught.exception.code, 'encryption')
        client.return_value.upload.assert_not_called()
        self.assertEqual(len(directories), 1)
        self.assertFalse(directories[0].exists())

    @override_settings(**CONFIG_SETTINGS)
    def test_success_uploads_only_ciphertext_and_removes_entire_temp_directory(self):
        directories = []

        def archive(directory, *_args):
            directories.append(directory)
            (directory / 'database.dump').write_bytes(b'PGDMPsynthetic')
            encrypted = directory / 'synthetic.gpg'
            encrypted.write_bytes(b'\x8csynthetic-ciphertext')
            return encrypted

        with patch.object(service, '_tools', return_value=TOOLS), patch.object(
            service, '_postgres_environment', return_value={},
        ), patch.object(service, '_database_guard', return_value=nullcontext(18)), patch.object(
            service, 'DriveClient',
        ) as client, patch.object(service, '_encrypted_archive', side_effect=archive), patch(
            'apps.core.backup.upload_backup_to_object_storage',
        ) as storage:
            client.return_value.folder.return_value = FOLDER_ID
            client.return_value.upload.return_value = {'scope': 'database_only'}
            self.assertEqual(service.create_drive_backup(), {'scope': 'database_only'})
        storage.assert_not_called()
        path, folder = client.return_value.upload.call_args.args
        self.assertEqual(path.suffix, '.gpg')
        self.assertEqual(folder, FOLDER_ID)
        self.assertFalse(directories[0].exists())

    @override_settings(**CONFIG_SETTINGS, MEDIA_STORAGE_BACKEND='s3', AWS_STORAGE_BUCKET_NAME='excluded-synthetic-bucket')
    def test_database_only_backup_does_not_touch_media_or_object_storage(self):
        with patch.object(service, '_tools', return_value=TOOLS), patch.object(
            service, '_postgres_environment', return_value={},
        ), patch.object(service, '_database_guard', return_value=nullcontext(18)), patch.object(
            service, 'DriveClient',
        ), patch.object(service, '_encrypted_archive', return_value=Path('synthetic.gpg')), patch(
            'apps.core.backup.upload_backup_to_object_storage',
        ) as storage:
            service.create_drive_backup()
        storage.assert_not_called()

    @override_settings(**CONFIG_SETTINGS)
    def test_failed_upload_still_removes_plaintext_and_ciphertext_temporaries(self):
        directories = []

        def archive(directory, *_args):
            directories.append(directory)
            (directory / 'database.dump').write_bytes(b'PGDMPsynthetic')
            encrypted = directory / 'synthetic.gpg'
            encrypted.write_bytes(b'\x8csynthetic-ciphertext')
            return encrypted

        with patch.object(service, '_tools', return_value=TOOLS), patch.object(
            service, '_postgres_environment', return_value={},
        ), patch.object(service, '_database_guard', return_value=nullcontext(18)), patch.object(
            service, 'DriveClient',
        ) as client, patch.object(service, '_encrypted_archive', side_effect=archive):
            client.return_value.upload.side_effect = service.DriveBackupError('drive_transfer')
            with self.assertRaises(service.DriveBackupError):
                service.create_drive_backup()
        self.assertFalse(directories[0].exists())
