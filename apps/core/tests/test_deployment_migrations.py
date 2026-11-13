"""Deploy guard tests use fake cursors or the runner's disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from decimal import Decimal
from io import StringIO
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, connections, migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.migration import Migration
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from apps.core.deployment_migrations import MIGRATION_LOCK_ID, forward_plan, migration_lock


class DeploymentPlanTests(SimpleTestCase):
    def executor(self, plan):
        executor = Mock()
        executor.loader.detect_conflicts.return_value = {}
        executor.migration_plan.return_value = plan
        return executor

    def test_empty_forward_plan_is_safe_and_does_not_mutate(self):
        executor = self.executor([])
        with patch('apps.core.deployment_migrations.MigrationExecutor', return_value=executor):
            self.assertEqual(forward_plan(Mock()), [])
        executor.loader.check_consistent_history.assert_called_once()
        executor.migrate.assert_not_called()

    def test_unknown_bootstrap_reverse_and_non_atomic_plans_refused_before_execution(self):
        for name, atomic, backwards in [('0001_initial', True, False), ('unknown', True, True), ('unknown', False, False)]:
            with self.subTest(name=name, atomic=atomic, backwards=backwards):
                migration = SimpleNamespace(app_label='inventory', name=name, atomic=atomic)
                executor = self.executor([(migration, backwards)])
                with patch('apps.core.deployment_migrations.MigrationExecutor', return_value=executor):
                    with self.assertRaises(CommandError):
                        forward_plan(Mock())
                executor.migrate.assert_not_called()

    def test_graph_conflicts_are_refused(self):
        executor = self.executor([])
        executor.loader.detect_conflicts.return_value = {'inventory': ['fork-a', 'fork-b']}
        with patch('apps.core.deployment_migrations.MigrationExecutor', return_value=executor):
            with self.assertRaisesMessage(CommandError, 'graph conflict'):
                forward_plan(Mock())
        executor.migration_plan.assert_not_called()

    def test_reviewed_source_and_exact_operation_contract_are_required(self):
        from importlib import import_module
        cls = import_module('apps.inventory.migrations.0024_clientbalanceadjustment_and_client_permission').Migration
        migration = cls('0024_clientbalanceadjustment_and_client_permission', 'inventory')
        migration.operations = list(migration.operations)
        executor = self.executor([(migration, False)])
        with patch('apps.core.deployment_migrations.MigrationExecutor', return_value=executor):
            self.assertEqual(len(forward_plan(Mock())), 1)
            migration.operations.append(migrations.RunSQL('SELECT 1'))
            with self.assertRaisesMessage(CommandError, 'operation contract'):
                forward_plan(Mock())

    def test_changed_digest_is_refused_even_when_name_and_operation_types_match(self):
        from importlib import import_module
        cls = import_module('apps.inventory.migrations.0024_clientbalanceadjustment_and_client_permission').Migration
        migration = cls('0024_clientbalanceadjustment_and_client_permission', 'inventory')
        executor = self.executor([(migration, False)])
        with patch('apps.core.deployment_migrations.MigrationExecutor', return_value=executor), patch(
            'apps.core.deployment_migrations.APPROVED_EXPANSIONS', {
                ('inventory', migration.name): {'sha256': '0' * 64, 'operations': ()},
            },
        ):
            with self.assertRaisesMessage(CommandError, 'digest changed'):
                forward_plan(Mock())

    def test_invalid_timeouts_non_postgres_and_outer_transactions_are_refused(self):
        fake = SimpleNamespace(vendor='postgresql', in_atomic_block=False, settings_dict={'HOST': '127.0.0.1'})
        for parameters in ({'wait_seconds': 0}, {'wait_seconds': True}, {'lock_timeout_ms': 30001}, {'statement_timeout_ms': -1}):
            with self.subTest(parameters=parameters), self.assertRaises(CommandError):
                with migration_lock(fake, **parameters):
                    self.fail('Must not enter')
        for fake in (SimpleNamespace(vendor='sqlite', in_atomic_block=False), SimpleNamespace(vendor='postgresql', in_atomic_block=True)):
            with self.assertRaises(CommandError):
                with migration_lock(fake):
                    self.fail('Must not enter')

    def test_unknown_connection_and_known_neon_pooler_are_rejected_before_sql(self):
        for host, confirmed in [('direct.example.invalid', False), ('ep-example-pooler.region.neon.tech', True)]:
            fake = SimpleNamespace(
                vendor='postgresql', in_atomic_block=False, settings_dict={'HOST': host}, cursor=Mock(),
            )
            with self.subTest(host=host, confirmed=confirmed), self.assertRaises(CommandError):
                with migration_lock(fake, direct_connection_confirmed=confirmed):
                    self.fail('Must not enter')
            fake.cursor.assert_not_called()

    def test_command_checks_plan_inside_lock_and_never_attempts_compensating_downgrade(self):
        from apps.core.management.commands.deploy_migrate import Command
        events = []

        def lock(*args, **kwargs):
            events.append('lock')
            return nullcontext()

        def plan(*args):
            self.assertEqual(events, ['lock'])
            events.append('plan')
            return []

        with patch('apps.core.management.commands.deploy_migrate.migration_lock', side_effect=lock), patch(
            'apps.core.management.commands.deploy_migrate.forward_plan', side_effect=plan,
        ), patch('apps.core.management.commands.deploy_migrate.call_command', side_effect=RuntimeError('synthetic migration failure')) as migrate:
            command = Command(stdout=StringIO())
            with self.assertRaisesMessage(RuntimeError, 'synthetic migration failure'):
                command.handle(plan_only=False, direct_connection_confirmed=True, wait_seconds=30, lock_timeout_ms=5000, statement_timeout_ms=120000, verbosity=0)
        migrate.assert_called_once_with('migrate', database='default', interactive=False, verbosity=0)

    def test_startup_check_with_empty_plan_never_locks_or_calls_migrate(self):
        from apps.core.management.commands.deploy_migrate import Command
        output = StringIO()
        with patch('apps.core.management.commands.deploy_migrate.forward_plan', return_value=[]) as plan, patch(
            'apps.core.management.commands.deploy_migrate.migration_lock',
        ) as lock, patch('apps.core.management.commands.deploy_migrate.call_command') as mutate:
            Command(stdout=output).handle(check_applied=True, plan_only=False)
        plan.assert_called_once()
        lock.assert_not_called()
        mutate.assert_not_called()
        self.assertIn('No migration applied.', output.getvalue())

    def test_startup_check_refuses_pending_plan_without_fallback_mutation(self):
        from apps.core.management.commands.deploy_migrate import Command
        with patch('apps.core.management.commands.deploy_migrate.forward_plan', return_value=[(object(), False)]), patch(
            'apps.core.management.commands.deploy_migrate.migration_lock',
        ) as lock, patch('apps.core.management.commands.deploy_migrate.call_command') as mutate:
            with self.assertRaisesMessage(CommandError, 'Pending reviewed migrations'):
                Command(stdout=StringIO()).handle(check_applied=True, plan_only=False)
        lock.assert_not_called()
        mutate.assert_not_called()

    def test_startup_check_preserves_unknown_plan_refusal_without_fallback(self):
        from apps.core.management.commands.deploy_migrate import Command
        with patch('apps.core.management.commands.deploy_migrate.forward_plan', side_effect=CommandError('Unapproved fixture')), patch(
            'apps.core.management.commands.deploy_migrate.call_command',
        ) as mutate:
            with self.assertRaisesMessage(CommandError, 'Unapproved fixture'):
                Command(stdout=StringIO()).handle(check_applied=True, plan_only=False)
        mutate.assert_not_called()

    def test_read_only_modes_are_mutually_exclusive(self):
        from apps.core.management.commands.deploy_migrate import Command
        parser = Command().create_parser('manage.py', 'deploy_migrate')
        with self.assertRaises(CommandError):
            parser.parse_args(['--plan-only', '--check-applied'])


class PostgreSQLDeploymentLockTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Deployment locking requires isolated PostgreSQL.')

    def test_competing_build_times_out_without_mutation_then_can_retry(self):
        started = Event()
        release = Event()

        def first():
            close_old_connections()
            try:
                with migration_lock(connection, direct_connection_confirmed=True):
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_backend_pid()')
                        pid = cursor.fetchone()[0]
                    started.set()
                    if not release.wait(15):
                        raise AssertionError('Test release was not signalled')
                    return pid
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first)
            try:
                self.assertTrue(started.wait(15))
                with self.assertRaisesMessage(CommandError, 'Another deployment'):
                    with migration_lock(connection, direct_connection_confirmed=True, wait_seconds=1):
                        self.fail('Second build must not migrate')
            finally:
                release.set()
            other_pid = future.result(timeout=15)
        with migration_lock(connection, direct_connection_confirmed=True, wait_seconds=1):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                self.assertNotEqual(cursor.fetchone()[0], other_pid)

    def test_exception_releases_session_lock_and_restores_timeouts(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            original = cursor.fetchone()
        with self.assertRaisesMessage(RuntimeError, 'synthetic partial failure'):
            with migration_lock(connection, direct_connection_confirmed=True, lock_timeout_ms=1234, statement_timeout_ms=5678):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
                    self.assertEqual(cursor.fetchone(), ('1234ms', '5678ms'))
                raise RuntimeError('synthetic partial failure')
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            self.assertEqual(cursor.fetchone(), original)
        def other_session_can_acquire():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_try_advisory_lock(%s)', [MIGRATION_LOCK_ID])
                    acquired = cursor.fetchone()[0]
                    if acquired:
                        cursor.execute('SELECT pg_advisory_unlock(%s)', [MIGRATION_LOCK_ID])
                    return acquired
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertTrue(pool.submit(other_session_can_acquire).result(timeout=15))

    def test_old_historical_client_model_still_reads_and_updates_profile_after_empty_expansion(self):
        executor = MigrationExecutor(connection)
        apps = executor.loader.project_state([
            ('inventory', '0023_conditioningcorrection_minimum_stock_after_and_more'),
        ]).apps
        OldClient = apps.get_model('inventory', 'Client')
        old = OldClient.objects.create(name='Synthetic old code client', balance=Decimal('25.00'))
        old.phone = '0123456789'
        old.save(update_fields=('phone',))
        from apps.inventory.models import Client, ClientBalanceAdjustment
        self.assertEqual(Client.objects.get(pk=old.pk).balance, Decimal('25.00'))
        self.assertFalse(ClientBalanceAdjustment.objects.filter(client_id=old.pk).exists())
        self.assertEqual(OldClient.objects.get(pk=old.pk).phone, '0123456789')
        old.delete()
        self.assertFalse(Client.objects.filter(pk=old.pk).exists())

    def test_guarded_noop_command_is_forward_only_and_releases_lock(self):
        call_command('deploy_migrate', direct_connection_confirmed=True, verbosity=0, stdout=StringIO())
        self.assertEqual(forward_plan(connection), [])

    def test_read_only_startup_gate_uses_real_schema_without_mutations_or_lock(self):
        with CaptureQueriesContext(connection) as captured, patch(
            'apps.core.management.commands.deploy_migrate.migration_lock',
        ) as lock, patch('apps.core.management.commands.deploy_migrate.call_command') as mutate:
            call_command('deploy_migrate', check_applied=True, verbosity=0, stdout=StringIO())
        self.assertTrue(captured.captured_queries)
        for query in captured.captured_queries:
            sql = query['sql'].lstrip().upper()
            self.assertFalse(sql.startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'TRUNCATE')))
            self.assertNotIn('PG_ADVISORY', sql)
            self.assertNotIn('PG_TRY_ADVISORY', sql)
        lock.assert_not_called()
        mutate.assert_not_called()

    def test_second_atomic_migration_failure_keeps_first_commit_without_automatic_downgrade(self):
        # Demonstrate the exact partial-failure boundary on synthetic DDL, not
        # on any installed application tables. A failed build is not a DB rollback.
        first = Migration('0001_expand', 'deployguardfixture')
        first.operations = [migrations.CreateModel(
            name='First', fields=[('id', models.AutoField(primary_key=True))],
            options={'db_table': 'deploy_guard_fixture_first'},
        )]
        second = Migration('0002_failure', 'deployguardfixture')
        second.operations = [
            migrations.CreateModel(
                name='Second', fields=[('id', models.AutoField(primary_key=True))],
                options={'db_table': 'deploy_guard_fixture_second'},
            ),
            migrations.RunSQL('SELECT 1 / 0'),
        ]
        state = ProjectState()
        try:
            with migration_lock(connection, direct_connection_confirmed=True):
                with connection.schema_editor(atomic=True) as editor:
                    state = first.apply(state, editor)
                with self.assertRaises(DatabaseError):
                    with connection.schema_editor(atomic=True) as editor:
                        second.apply(state.clone(), editor)
            tables = connection.introspection.table_names()
            self.assertIn('deploy_guard_fixture_first', tables)
            self.assertNotIn('deploy_guard_fixture_second', tables)
            with connection.cursor() as cursor:
                cursor.execute('SELECT count(*) FROM deploy_guard_fixture_first')
                self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            if 'deploy_guard_fixture_first' in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(state.apps.get_model('deployguardfixture', 'First'))
