"""Offline preflight fixtures. No real tool, secret, SQL or Google request."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from django.test import SimpleTestCase

from scripts import check_drive_backup_runtime as readiness


ORIGIN = 'https://backup-fixture.invalid'


def configuration():
    return dict(
        DRIVE_BACKUP_ENABLED='true', DRIVE_BACKUP_OAUTH_ENABLED='true',
        DRIVE_BACKUP_CLIENT_ID='public-fixture.apps.googleusercontent.com',
        DRIVE_BACKUP_CLIENT_SECRET='public-fixture-client-not-a-secret',
        DRIVE_BACKUP_PASSPHRASE='public-fixture-phrase-never-use-for-real-backups',
        DRIVE_BACKUP_TOKEN_KEY=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        DRIVE_BACKUP_OAUTH_REDIRECT_URI=ORIGIN + readiness.CALLBACK,
        DATABASE_URL='postgresql://' + 'fixture_user:fixture_password@database-fixture.invalid/fixture?' +
                     urlencode({'sslmode': 'verify-full', 'channel_binding': 'require', 'sslrootcert': 'system'}),
        WEB_TIMEOUT='120',
    )


class RuntimeReadinessTests(SimpleTestCase):
    def setUp(self):
        # Keep the optional dependency optional for ordinary ERP/CI installs.
        # The real Fernet import/self-test is exercised by the separate native
        # preflight, not silently required by these deterministic unit fixtures.
        class FixtureFernet:
            def __init__(self, key):
                pass

            @staticmethod
            def generate_key():
                return b'public-unit-fixture-key'

            def encrypt(self, value):
                return b'public-unit-token:' + value

            def decrypt(self, value):
                return value[len(b'public-unit-token:'):]

        self.crypto = SimpleNamespace(__version__=readiness.EXPECTED_CRYPTO, Fernet=FixtureFernet)
        actual_import = readiness.importlib.import_module
        def fixture_import(name):
            return self.crypto if name in {'cryptography', 'cryptography.fernet'} else actual_import(name)
        self.enterContext(patch.object(readiness.importlib, 'import_module', side_effect=fixture_import))
        self.which = self.enterContext(patch.object(readiness.shutil, 'which', side_effect=lambda name, **kw: name))
        def version(arguments, **kwargs):
            name = arguments[0]
            return SimpleNamespace(stdout=(b'gpg (GnuPG) 2.4.9' if name == 'gpg' else b'tool (PostgreSQL) 18.6'))
        self.run = self.enterContext(patch.object(readiness.subprocess, 'run', side_effect=version))

    def test_disabled_build_never_imports_crypto_or_checks_tools(self):
        with patch.object(readiness.importlib, 'import_module') as imports:
            for values in ({}, {'DRIVE_BACKUP_ENABLED': 'False', 'DRIVE_BACKUP_OAUTH_ENABLED': '0'}):
                report = readiness.build_guard(values)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['activation'], 'DISABLED')
                self.assertFalse(report['runtime_checked'])
            imports.assert_not_called()
        self.which.assert_not_called()
        self.run.assert_not_called()

    def test_invalid_disabled_flag_fails_before_tools(self):
        result = readiness.build_guard({'DRIVE_BACKUP_ENABLED': 'not-a-flag'})
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['issues'], ['activation_flags_invalid'])
        self.run.assert_not_called()

    def test_compatible_runtime_has_no_false_connectivity_or_restore_claim(self):
        result = readiness.runtime_report(configuration(), check_configuration=True, public_origin=ORIGIN)
        self.assertEqual(result['status'], 'PASS')
        for flag in ('production_database_access', 'google_drive_access', 'database_server_version_checked',
                     'drive_space_checked', 'django_configuration_checked', 'production_restore_verified'):
            self.assertIs(result[flag], False)
        self.assertEqual(len(self.run.call_args_list), 3)
        for call in self.run.call_args_list:
            self.assertEqual(call.args[0][1:], ['--version'])
            self.assertEqual(call.kwargs['env'], {})
            self.assertEqual(call.kwargs['timeout'], 5)
        serialized = json.dumps(result)
        self.assertNotIn('fixture_password', serialized)
        self.assertNotIn(configuration()['DRIVE_BACKUP_TOKEN_KEY'], serialized)
        self.assertNotIn(ORIGIN, serialized)

    def test_native_subprocesses_never_inherit_credentials(self):
        environment = configuration() | {'PGPASSWORD': 'private-not-to-inherit',
                                          'GOOGLE_ACCESS_TOKEN': 'private-not-to-inherit', 'PATH': '/fixture/bin'}
        readiness.runtime_report(environment)
        for call in self.run.call_args_list:
            self.assertEqual(call.kwargs['env'], {'PATH': '/fixture/bin'})

    def test_wrong_major_missing_binary_and_provider_errors_are_fixed_only(self):
        self.run.return_value = SimpleNamespace(stdout=b'tool (PostgreSQL) 17.11')
        self.run.side_effect = None
        result = readiness.runtime_report({})
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('pg_dump_missing_or_incompatible', result['issues'])
        self.assertIn('pg_restore_missing_or_incompatible', result['issues'])
        self.which.return_value, self.which.side_effect = None, None
        self.run.reset_mock()
        result = readiness.runtime_report({})
        self.assertIn('gpg_missing_or_incompatible', result['issues'])
        self.run.assert_not_called()
        self.which.return_value = '/fixture/binary'
        self.run.side_effect = RuntimeError('private-diagnostic-value')
        self.assertNotIn('private-diagnostic-value', json.dumps(readiness.runtime_report({})))

    def test_no_locked_crypto_is_a_safe_failure(self):
        with patch.object(readiness.importlib, 'import_module', side_effect=ImportError('private-module-message')):
            result = readiness.runtime_report({})
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('locked_oauth_crypto_unavailable', result['issues'])
        self.assertNotIn('private-module-message', json.dumps(result))

    def test_different_crypto_version_does_not_qualify_locked_runtime(self):
        self.crypto.__version__ = '0.0.public-unit-fixture'
        self.assertIn('locked_oauth_crypto_unavailable', readiness.runtime_report({})['issues'])

    def test_invalid_expected_major_executes_nothing(self):
        result = readiness.runtime_report({}, expected_major=True)
        self.assertEqual(result['issues'], ['expected_postgresql_major_invalid'])
        self.run.assert_not_called()

    def test_explicit_tool_then_legacy_alias_then_path_matches_service_precedence(self):
        readiness.runtime_report({'PG_DUMP_BIN': '/legacy/pg_dump', 'PG_RESTORE_BIN': '/legacy/pg_restore',
                                  'GPG_BIN': '/legacy/gpg'})
        self.assertEqual([call.args[0] for call in self.which.call_args_list],
                         ['/legacy/pg_dump', '/legacy/pg_restore', '/legacy/gpg'])
        self.which.reset_mock()
        readiness.runtime_report({'PG_DUMP_BIN': '/legacy/pg_dump', 'DRIVE_BACKUP_PG_DUMP_BIN': '/explicit/pg_dump'})
        self.assertEqual(self.which.call_args_list[0].args[0], '/explicit/pg_dump')

    def test_configuration_mismatch_never_prints_values(self):
        for changed in (
            {'DRIVE_BACKUP_OAUTH_ENABLED': 'false'}, {'DRIVE_BACKUP_CLIENT_ID': 'private-client-value'},
            {'DRIVE_BACKUP_CLIENT_SECRET': 'short'}, {'DRIVE_BACKUP_PASSPHRASE': 'short'},
            {'DRIVE_BACKUP_TOKEN_KEY': 'invalid-private-key'}, {'DATABASE_URL': 'private-malformed-url'},
            {'DRIVE_BACKUP_OAUTH_REDIRECT_URI': 'https://wrong-fixture.invalid/' + readiness.CALLBACK},
            {'WEB_TIMEOUT': '30'},
        ):
            with self.subTest(fields=list(changed)):
                issues = readiness.configuration_issues(configuration() | changed, ORIGIN)
                self.assertTrue(issues)
                for value in changed.values():
                    self.assertNotIn(value, json.dumps(issues))

    def test_callback_requires_exact_https_public_origin_and_path(self):
        for origin in ('http://backup-fixture.invalid', ORIGIN + '?private=query', ORIGIN + '/other',
                       'https://user:private@backup-fixture.invalid', ORIGIN + ':444', ''):
            self.assertIn('https_callback_origin_mismatch', readiness.configuration_issues(configuration(), origin))

    def test_database_tls_duplicate_unsupported_and_timeout_options_fail(self):
        base = 'postgresql://' + 'fixture_user:fixture_password@fixture.invalid/source?'
        for query in ('sslmode=disable', 'sslmode=require&sslmode=require', 'sslmode=require&options=private',
                      'sslmode=require&channel_binding=invalid', 'sslmode=require&connect_timeout=999',
                      'sslmode=require&sslrootcert=%00'):
            issues = readiness.configuration_issues(configuration() | {'DATABASE_URL': base + query}, ORIGIN)
            self.assertIn('postgresql_tls_configuration_invalid', issues)

    def test_key_cannot_reuse_archive_phrase_or_django_key(self):
        source = configuration()
        for values in ({'DRIVE_BACKUP_PASSPHRASE': source['DRIVE_BACKUP_TOKEN_KEY']},
                       {'SECRET_KEY': source['DRIVE_BACKUP_TOKEN_KEY']}):
            self.assertIn('oauth_vault_key_invalid', readiness.configuration_issues(source | values, ORIGIN))

    def test_runtime_failure_does_not_falsely_label_configuration_invalid(self):
        self.which.return_value, self.which.side_effect = None, None
        result = readiness.runtime_report(configuration(), check_configuration=True, public_origin=ORIGIN)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['checks']['configuration_syntax'], 'PASS')

    def test_build_guard_precedes_django_and_defaults_are_disabled(self):
        root = Path(__file__).resolve().parents[2]
        build = (root / 'build.sh').read_text()
        self.assertLess(build.index('check_drive_backup_runtime.py'), build.index('python manage.py collectstatic'))
        self.assertIn('--only-binary=:all: --require-hashes -r requirements-drive-backup.lock', build)
        start = (root / 'start.sh').read_text()
        self.assertLess(start.index('check_drive_backup_runtime.py'), start.index('exec gunicorn'))
        self.assertIn("--access-logformat '%(m)s %(U)s %(s)s'", start)
        self.assertNotIn('%(q)s', start)
        blueprint = (root / 'render.yaml').read_text()
        for name in ('DRIVE_BACKUP_BUILD_DEPENDENCIES', 'DRIVE_BACKUP_ENABLED', 'DRIVE_BACKUP_OAUTH_ENABLED'):
            self.assertIn('key: ' + name + '\n        value: "false"', blueprint)
