-- Metadata only. No business rows, role names, hosts, passwords or URLs.
-- Run the whole block at once. ROLLBACK restores local timeout settings.
-- This describes the role selected in the SQL editor, not necessarily the
-- application's role. Compare that role privately; do not send credentials.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';

SELECT
    pg_catalog.current_setting('transaction_read_only') = 'on' AS read_only_transaction,
    r.rolsuper AS superuser,
    r.rolcreaterole AS can_create_roles,
    r.rolcreatedb AS can_create_databases,
    r.rolbypassrls AS bypasses_rls,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_stat_ssl
        WHERE pid = pg_catalog.pg_backend_pid() AND ssl
    ) AS editor_session_tls,
    pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE') AS public_schema_create,
    pg_catalog.current_setting('server_version_num')::integer / 10000 AS postgres_major,
    pg_catalog.current_setting('idle_in_transaction_session_timeout') <> '0' AS idle_transaction_timeout_enabled;

ROLLBACK;
