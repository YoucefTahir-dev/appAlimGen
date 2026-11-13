"""Sanitised PostgreSQL metadata only; never enumerate business data or secrets."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    help = 'Read-only PostgreSQL role/TLS/timeouts diagnostic. No grants or configuration writes.'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('PostgreSQL required; no connection details disclosed.')
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                    cursor.execute("SET LOCAL statement_timeout = '5s'")
                    cursor.execute("SET LOCAL lock_timeout = '1s'")
                    cursor.execute("""
                        SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls,
                               EXISTS (SELECT 1 FROM pg_catalog.pg_stat_ssl
                                       WHERE pid = pg_catalog.pg_backend_pid() AND ssl),
                               pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE'),
                               pg_catalog.current_setting('server_version_num')::integer / 10000,
                               pg_catalog.current_setting('statement_timeout') <> '0',
                               pg_catalog.current_setting('idle_in_transaction_session_timeout') <> '0'
                        FROM pg_catalog.pg_roles r WHERE r.rolname = current_user
                    """)
                    row = cursor.fetchone()
        except Exception as exc:
            # Database exceptions can contain hosts/SQL/credentials; no traceback.
            raise CommandError(f'Diagnostic unavailable ({type(exc).__name__}); details withheld.') from None
        if row is None:
            raise CommandError('Role metadata unavailable.')
        keys = ('superuser', 'can_create_roles', 'can_create_databases', 'bypasses_rls',
                'session_tls', 'public_schema_create', 'postgres_major',
                'diagnostic_statement_timeout_enabled', 'idle_transaction_timeout_enabled')
        result = dict(zip(keys, row))
        result.update(scope='readonly_database_metadata', business_data_read=False,
                      privileges_modified=False, backup_retention_verified=False,
                      alert_delivery_verified=False, role_separation_verified=False)
        result['review_required'] = bool(not result['session_tls'] or any(
            result[key] for key in ('superuser', 'can_create_roles', 'can_create_databases', 'bypasses_rls', 'public_schema_create')
        ))
        self.stdout.write(json.dumps(result, sort_keys=True))
