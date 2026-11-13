"""Pure synthetic diagnostics: no real environment, secrets or database."""

from io import StringIO
import json
import unittest
from unittest.mock import patch

from scripts.check_migration_environment import main, migration_environment_report


class MigrationEnvironmentReportTests(unittest.TestCase):
    def report(self, url='postgresql://synthetic_user:synthetic_password@ep-fixture.region.neon.tech/synthetic_db', **extra):
        return migration_environment_report({'DATABASE_URL': url, **extra})

    def test_non_pooler_is_never_declared_a_verified_direct_connection_or_ready(self):
        result = self.report()
        self.assertTrue(result['postgresql_endpoint_syntax_valid'])
        self.assertFalse(result['known_pooler_detected'])
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        for name in ('deployment_ready', 'direct_connection_verified',
                     'render_compute_plan_verified', 'rollback_floor_attested'):
            self.assertFalse(result[name])

    def test_known_pooler_is_blocked_even_when_uppercase(self):
        for host in ('EP-FIXTURE-POOLER.REGION.NEON.TECH', 'fixture-pooler'):
            with self.subTest(host=host):
                result = self.report(f'postgresql://u:p@{host}/fixture')
                self.assertTrue(result['known_pooler_detected'])
                self.assertEqual(result['status'], 'BLOCKED')

    def test_url_has_precedence_over_discrete_configuration_as_in_settings(self):
        result = self.report(DATABASE_ENGINE='postgresql', DATABASE_HOST='fixture-pooler', DATABASE_NAME='other')
        self.assertEqual(result['configuration_source'], 'database_url')
        self.assertFalse(result['known_pooler_detected'])

    def test_discrete_postgresql_is_syntax_only(self):
        result = migration_environment_report({
            'DATABASE_ENGINE': 'postgresql', 'DATABASE_HOST': '127.0.0.1',
            'DATABASE_NAME': 'fixture', 'DATABASE_PORT': '5432',
        })
        self.assertEqual(result['configuration_source'], 'discrete_environment')
        self.assertTrue(result['postgresql_endpoint_syntax_valid'])
        self.assertFalse(result['direct_connection_verified'])

    def test_missing_sqlite_and_incomplete_discrete_configuration_are_blocked(self):
        for environment in ({}, {'DATABASE_ENGINE': 'sqlite'},
                            {'DATABASE_ENGINE': 'postgresql'},
                            {'DATABASE_ENGINE': 'postgresql', 'DATABASE_HOST': 'fixture',
                             'DATABASE_NAME': 'fixture', 'DATABASE_PORT': '0'}):
            with self.subTest(environment=environment):
                result = migration_environment_report(environment)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertFalse(result['postgresql_endpoint_syntax_valid'])

    def test_malformed_urls_do_not_report_values_or_exception_details(self):
        for url in ('postgresql://u:private-sentinel@[broken/db',
                    'postgresql://u:private-sentinel@host:notaport/db',
                    'postgresql://u:private-sentinel@host:65536/db',
                    'postgresql://u:private-sentinel@host:0/db',
                    'postgresql://u:private-sentinel@host/',
                    'postgresql://u:private-sentinel@host/db#private-sentinel',
                    'postgresql://u:private-sentinel@host/da\ntabase',
                    'mysql://u:private-sentinel@host/db'):
            with self.subTest(url_kind='synthetic_invalid'):
                result = self.report(url)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertFalse(result['postgresql_endpoint_syntax_valid'])
                self.assertNotIn('private-sentinel', json.dumps(result))

    def test_connection_routing_options_are_never_treated_as_direct_attestation(self):
        for key in ('host', 'hostaddr', 'service', 'servicefile', 'passfile', 'port', 'dbname', 'user', 'password', 'dsn', 'options'):
            with self.subTest(option=key):
                result = self.report(f'postgresql://u:p@host/db?{key}=private-sentinel')
                self.assertTrue(result['routing_options_detected'])
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertNotIn('private-sentinel', json.dumps(result))

    def test_overlong_decimal_port_is_reported_instead_of_raising_or_echoing(self):
        result = migration_environment_report({
            'DATABASE_ENGINE': 'postgresql', 'DATABASE_HOST': 'fixture',
            'DATABASE_NAME': 'fixture', 'DATABASE_PORT': '9' * 5000,
        })
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse(result['postgresql_endpoint_syntax_valid'])

    def test_endpoint_syntax_does_not_validate_libpq_options_or_tls(self):
        result = self.report('postgresql://u:p@host/db?sslmode=synthetic-invalid-mode')
        self.assertTrue(result['postgresql_endpoint_syntax_valid'])
        self.assertFalse(result['connection_options_verified'])
        self.assertFalse(result['deployment_ready'])
        self.assertNotIn('synthetic-invalid-mode', json.dumps(result))

    def test_ordinary_connection_options_do_not_echo_credentials_host_database_or_paths(self):
        result = self.report('postgresql://private-user:private-password@private-host/private-db?sslmode=require&sslrootcert=private-path')
        serialized = json.dumps(result)
        for value in ('private-user', 'private-password', 'private-host', 'private-db', 'private-path'):
            self.assertNotIn(value, serialized)
        self.assertFalse(result['routing_options_detected'])

    def test_report_does_not_mutate_environment_read_files_or_open_connections(self):
        environment = {'DATABASE_URL': 'postgresql://u:p@host/db'}
        before = environment.copy()
        with patch('builtins.open', side_effect=AssertionError('File access prohibited')), patch(
            'socket.create_connection', side_effect=AssertionError('Network prohibited'),
        ), patch('subprocess.Popen', side_effect=AssertionError('Subprocess prohibited')):
            result = migration_environment_report(environment)
        self.assertEqual(environment, before)
        for name in ('database_access', 'network_access', 'dotenv_loaded', 'environment_modified'):
            self.assertFalse(result[name])

    def test_cli_uses_supplied_synthetic_environment_and_returns_json_without_authorizing_deploy(self):
        output = StringIO()
        self.assertEqual(main([], environment={'DATABASE_URL': 'postgresql://u:p@host/db'}, stdout=output), 0)
        self.assertFalse(json.loads(output.getvalue())['deployment_ready'])
        output = StringIO()
        self.assertEqual(main([], environment={}, stdout=output), 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')

    def test_cli_rejects_arguments_without_echoing_them(self):
        output = StringIO()
        self.assertEqual(main(['private-sentinel'], environment={}, stdout=output), 2)
        self.assertNotIn('private-sentinel', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['issues'], ['command_arguments_invalid'])

    def test_migration_guard_requires_explicit_operator_attestation_not_just_non_pooler(self):
        for value in ('', 'false', '0', 'yes', 'private-sentinel'):
            output = StringIO()
            environment = {'DATABASE_URL': 'postgresql://u:p@host/db', 'MIGRATION_DIRECT_CONNECTION_CONFIRMED': value}
            self.assertEqual(main(['--migration-guard'], environment=environment, stdout=output), 2)
            report = json.loads(output.getvalue())
            self.assertFalse(report['operator_direct_connection_attested'])
            self.assertNotIn('private-sentinel', output.getvalue())

    def test_attested_config_gate_is_not_a_deployment_or_network_verification(self):
        for value in ('true', '1', 'TRUE'):
            output = StringIO()
            environment = {'DATABASE_URL': 'postgresql://u:p@host/db', 'MIGRATION_DIRECT_CONNECTION_CONFIRMED': value}
            self.assertEqual(main(['--migration-guard'], environment=environment, stdout=output), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['status'], 'CONFIG_GATE_PASSED')
            self.assertTrue(report['operator_direct_connection_attested'])
            for name in ('deployment_ready', 'direct_connection_verified', 'network_access', 'database_access'):
                self.assertFalse(report[name])

    def test_operator_attestation_never_bypasses_pooler_malformed_url_or_routing_option(self):
        for url in ('postgresql://u:p@fixture-pooler.region.neon.tech/db',
                    'postgresql://u:p@host:notaport/db',
                    'postgresql://u:p@host/db?hostaddr=private-sentinel',
                    'postgresql://u:p@host/db?options=private-sentinel'):
            output = StringIO()
            self.assertEqual(main(['--migration-guard'], environment={
                'DATABASE_URL': url, 'MIGRATION_DIRECT_CONNECTION_CONFIRMED': 'true',
            }, stdout=output), 2)
            self.assertNotIn('private-sentinel', output.getvalue())

    def test_routing_environment_is_reported_and_cannot_be_overridden_by_attestation(self):
        for name in ('PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
            environment = {'DATABASE_URL': 'postgresql://u:p@host/db', name: 'private-sentinel',
                           'MIGRATION_DIRECT_CONNECTION_CONFIRMED': 'true'}
            output = StringIO()
            self.assertEqual(main(['--migration-guard'], environment=environment, stdout=output), 2)
            self.assertTrue(json.loads(output.getvalue())['routing_environment_detected'])
            self.assertNotIn('private-sentinel', output.getvalue())

    def test_migration_guard_accepts_no_extra_arguments_or_secret_values(self):
        output = StringIO()
        self.assertEqual(main(['--migration-guard', 'private-sentinel'], environment={}, stdout=output), 2)
        self.assertNotIn('private-sentinel', output.getvalue())


if __name__ == '__main__':
    unittest.main()
