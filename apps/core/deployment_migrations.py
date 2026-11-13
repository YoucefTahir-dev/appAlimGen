"""Forward-only deploy gate. This module never opens a separate DB connection."""

from contextlib import contextmanager
import hashlib
import inspect
import time

from django.core.management.base import CommandError
from django.db import migrations
from django.db.migrations.executor import MigrationExecutor


# A stable application-specific session lock, shared by all builds/workers.
MIGRATION_LOCK_ID = 0x454C414D494E45

# Each expansion is reviewed together with its old/new-code compatibility.
# Do not add a blanket wildcard or approve a migration merely to unblock CI.
APPROVED_EXPANSIONS = {
    ('inventory', '0024_clientbalanceadjustment_and_client_permission'): {
        'sha256': '776ce53c77766f25049726995507930d7fc83db91863c96a6392370cec3a22f3',
        'operations': (migrations.AlterModelOptions, migrations.CreateModel),
    },
}


def forward_plan(connection):
    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    conflicts = executor.loader.detect_conflicts()
    if conflicts:
        raise CommandError('Migration graph conflict: resolve it before deployment.')
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    for migration, backwards in plan:
        key = (migration.app_label, migration.name)
        approval = APPROVED_EXPANSIONS.get(key)
        if backwards or approval is None or not migration.atomic:
            raise CommandError(f'Unapproved deployment migration: {key[0]}.{key[1]}. No automatic downgrade or bootstrap.')
        source = inspect.getsourcefile(type(migration))
        if not source:
            raise CommandError('Migration source is unavailable.')
        with open(source, encoding='utf-8', newline=None) as handle:
            digest = hashlib.sha256(handle.read().encode('utf-8')).hexdigest()
        if digest != approval['sha256']:
            raise CommandError(f'Migration approval digest changed: {key[0]}.{key[1]}.')
        if tuple(type(operation) for operation in migration.operations) != approval['operations']:
            raise CommandError(f'Migration operation contract changed: {key[0]}.{key[1]}.')
    return plan


@contextmanager
def migration_lock(connection, *, direct_connection_confirmed=False, wait_seconds=30, lock_timeout_ms=5000, statement_timeout_ms=120000):
    """Session advisory lock survives individual migration commits, not failures.

    lock_timeout bounds DDL waits against the still-serving old application.
    statement_timeout bounds each statement, not the entire migration command.
    The external build timeout remains the total wall-clock limit.
    """
    for value, maximum in ((wait_seconds, 120), (lock_timeout_ms, 30000), (statement_timeout_ms, 300000)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise CommandError('Migration timeouts must be bounded positive integers.')
    if connection.vendor != 'postgresql':
        raise CommandError('Deployment migration guard requires PostgreSQL.')
    if connection.in_atomic_block:
        raise CommandError('Deployment migrations cannot run inside an outer transaction.')
    host = str(connection.settings_dict.get('HOST', '')).lower()
    if not direct_connection_confirmed or '-pooler.' in host:
        raise CommandError('A confirmed direct PostgreSQL connection is required; never run this session lock through transaction pooling.')
    acquired = False
    owner_pid = None
    previous = None
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_lock(%s), pg_backend_pid()', [MIGRATION_LOCK_ID])
                acquired, owner_pid = cursor.fetchone()
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise CommandError('Another deployment is migrating. Retry after it finishes; no migration was run.')
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            previous = cursor.fetchone()
            cursor.execute("SELECT set_config('lock_timeout', %s, false), set_config('statement_timeout', %s, false)", [f'{lock_timeout_ms}ms', f'{statement_timeout_ms}ms'])
        yield
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    if cursor.fetchone()[0] != owner_pid:
                        raise CommandError('Migration connection changed PostgreSQL sessions.')
                    if previous is not None:
                        cursor.execute("SELECT set_config('lock_timeout', %s, false), set_config('statement_timeout', %s, false)", list(previous))
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [MIGRATION_LOCK_ID])
                    if not cursor.fetchone()[0]:
                        raise CommandError('Migration session lock was unexpectedly lost.')
            except Exception:
                # Closing the owning session is the final lock-release guard.
                # Never attempt a reverse migration, including after partial failure.
                connection.close()
                raise
