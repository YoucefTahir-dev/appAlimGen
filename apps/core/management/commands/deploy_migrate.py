from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections

from apps.core.deployment_migrations import forward_plan, migration_lock


class Command(BaseCommand):
    help = 'Apply only reviewed forward expansions under a bounded PostgreSQL deployment lock.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--plan-only', action='store_true')
        mode.add_argument('--check-applied', action='store_true', help='Read-only startup gate: refuse any pending migration; do not acquire a lock or apply SQL.')
        parser.add_argument('--direct-connection-confirmed', action='store_true', help='Confirm an operator-verified direct PostgreSQL connection, not a transaction pooler.')
        parser.add_argument('--wait-seconds', type=int, default=30)
        parser.add_argument('--lock-timeout-ms', type=int, default=5000)
        parser.add_argument('--statement-timeout-ms', type=int, default=120000)

    def handle(self, *args, **options):
        connection = connections['default']
        if options.get('check_applied'):
            plan = forward_plan(connection)
            if plan:
                raise CommandError('Pending reviewed migrations: run the qualified pre-deploy phase before starting this code. No migration applied.')
            self.stdout.write('Schema gate completed: no pending migrations. No migration applied.')
            return
        if options['plan_only']:
            plan = forward_plan(connection)
            for migration, _ in plan:
                self.stdout.write(f'APPROVED EXPAND {migration.app_label}.{migration.name}')
            self.stdout.write(f'Reviewed pending migrations: {len(plan)}. No migration applied.')
            return
        with migration_lock(
            connection, direct_connection_confirmed=options['direct_connection_confirmed'],
            wait_seconds=options['wait_seconds'],
            lock_timeout_ms=options['lock_timeout_ms'],
            statement_timeout_ms=options['statement_timeout_ms'],
        ):
            # Rebuild the plan AFTER acquiring the lock: a preceding build may
            # have completed the same expansion while this build was waiting.
            plan = forward_plan(connection)
            self.stdout.write(f'Applying reviewed forward expansions: {len(plan)}')
            call_command('migrate', database='default', interactive=False, verbosity=options['verbosity'])
        self.stdout.write(self.style.SUCCESS('Forward migration gate completed. No downgrade attempted.'))
