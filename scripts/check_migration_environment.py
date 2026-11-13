"""Non-secret migration configuration diagnostic; never a deploy authorization.

Only the supplied/process environment is inspected. No dotenv, Django, files,
database connection, subprocess or network operation is used. A non-pooler
hostname does NOT prove that a PostgreSQL connection is direct.
"""

import json
import os
import sys
from urllib.parse import parse_qsl, urlsplit


ROUTING_OPTIONS = frozenset({
    'host', 'hostaddr', 'port', 'service', 'servicefile', 'passfile',
    'dbname', 'database', 'user', 'password', 'dsn',
})


def _text(environment, name):
    value = environment.get(name, '')
    return value.strip() if isinstance(value, str) else ''


def migration_environment_report(environment):
    """Return fixed codes/booleans, never input values or parsing exceptions."""
    result = {
        'scope': 'offline_migration_environment_only',
        'status': 'REVIEW_REQUIRED',
        'deployment_ready': False,
        'configuration_source': 'none',
        'postgresql_endpoint_syntax_valid': False,
        'connection_options_verified': False,
        'known_pooler_detected': False,
        'routing_options_detected': False,
        'direct_connection_verified': False,
        'render_compute_plan_verified': False,
        'rollback_floor_attested': False,
        'database_access': False,
        'network_access': False,
        'dotenv_loaded': False,
        'environment_modified': False,
        'issues': [],
    }
    raw = _text(environment, 'DATABASE_URL')
    host = ''
    if raw:
        result['configuration_source'] = 'database_url'
        try:
            if len(raw) > 16384 or any(ord(char) < 32 for char in raw):
                raise ValueError
            parsed = urlsplit(raw)
            # Accessing .port validates invalid/non-numeric/out-of-range ports.
            port = parsed.port
            host = (parsed.hostname or '').lower()
            if (parsed.scheme not in {'postgres', 'postgresql'} or not host
                    or not parsed.path.lstrip('/') or parsed.fragment
                    or port == 0):
                raise ValueError
            pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=64)
            result['routing_options_detected'] = any(
                key.lower() in ROUTING_OPTIONS for key, _value in pairs
            )
            result['postgresql_endpoint_syntax_valid'] = True
        except (ValueError, UnicodeError):
            result['issues'].append('database_url_syntax_invalid_or_unsupported')
    else:
        engine = _text(environment, 'DATABASE_ENGINE').lower()
        if engine in {'postgresql', 'postgres', 'django.db.backends.postgresql'}:
            result['configuration_source'] = 'discrete_environment'
            host = _text(environment, 'DATABASE_HOST').lower()
            database = _text(environment, 'DATABASE_NAME')
            port = _text(environment, 'DATABASE_PORT') or '5432'
            if (host and database and len(port) <= 5 and port.isascii() and port.isdecimal()
                    and 1 <= int(port) <= 65535
                    and not any(ord(char) < 32 for char in host + database)):
                result['postgresql_endpoint_syntax_valid'] = True
            else:
                result['issues'].append('discrete_postgresql_configuration_incomplete')
        else:
            result['issues'].append('postgresql_configuration_not_present')

    result['known_pooler_detected'] = '-pooler.' in host or host.endswith('-pooler')
    if result['known_pooler_detected']:
        result['issues'].append('known_pooler_not_suitable_for_session_lock')
    if result['routing_options_detected']:
        result['issues'].append('connection_routing_options_require_review')
    if result['issues']:
        result['status'] = 'BLOCKED'
    result['issues'].extend([
        'direct_connection_requires_operator_verification',
        'render_compute_plan_requires_verification',
        'initial_rollout_and_rollback_floor_require_attestation',
    ])
    return result


def main(argv=None, *, environment=None, stdout=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        # Never echo an accidentally supplied URL, secret or unsupported option.
        report = {
            'scope': 'offline_migration_environment_only',
            'status': 'BLOCKED', 'deployment_ready': False,
            'issues': ['command_accepts_no_arguments'],
        }
    else:
        report = migration_environment_report(os.environ if environment is None else environment)
    print(json.dumps(report, sort_keys=True), file=sys.stdout if stdout is None else stdout)
    return 2 if report['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
