"""Offline Drive Web backup preflight: no Django settings, .env, SQL or Google.

Only fixed issue codes and bounded version numbers are reported. Native commands
are restricted to --version with a credential-free environment. PASS is NOT a
backup, a connectivity test, a quota check or a production restoration proof.
"""
import argparse
import base64
import importlib
import json
import os
import re
import shutil
import subprocess
from urllib.parse import parse_qsl, unquote, urlsplit


CALLBACK = '/core/backups/drive/callback/'
EXPECTED_CRYPTO = '50.0.2'


def enabled(value):
    return isinstance(value, str) and value.strip().lower() in {'true', '1'}


def configuration_issues(environment, public_origin):
    """Check syntax only; never echo inputs or follow configured URLs/paths."""
    issues = []
    flags = [environment.get(name, 'false') for name in (
        'DRIVE_BACKUP_ENABLED', 'DRIVE_BACKUP_OAUTH_ENABLED')]
    if any(not isinstance(value, str) or value.strip().lower() not in {'true', 'false', '1', '0'}
           for value in flags):
        return ['activation_flags_invalid']
    if not any(enabled(value) for value in flags):
        return []
    if not all(enabled(value) for value in flags):
        issues.append('activation_flags_incomplete')

    def single_line(value, minimum):
        return (isinstance(value, str) and minimum <= len(value) <= 4096 and value == value.strip()
                and not any(ord(character) < 32 for character in value))

    identifier = environment.get('DRIVE_BACKUP_CLIENT_ID', '')
    secret = environment.get('DRIVE_BACKUP_CLIENT_SECRET', '')
    phrase = environment.get('DRIVE_BACKUP_PASSPHRASE', '')
    key = environment.get('DRIVE_BACKUP_TOKEN_KEY', '')
    if not isinstance(identifier, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,256}\.apps\.googleusercontent\.com', identifier) is None:
        issues.append('oauth_client_id_invalid')
    if not single_line(secret, 12):
        issues.append('oauth_client_secret_invalid')
    if not single_line(phrase, 32):
        issues.append('archive_passphrase_invalid')
    try:
        if (not isinstance(key, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}=', key) is None or
                len(base64.urlsafe_b64decode(key)) != 32 or key in {phrase, environment.get('SECRET_KEY')}):
            raise ValueError()
    except Exception:
        issues.append('oauth_vault_key_invalid')

    try:
        origin = urlsplit(public_origin)
        if (not isinstance(public_origin, str) or public_origin != public_origin.strip() or
                origin.scheme != 'https' or not origin.hostname or origin.port not in (None, 443) or
                origin.username is not None or origin.password is not None or origin.path not in {'', '/'} or
                origin.query or origin.fragment or any(ord(c) < 33 for c in public_origin)):
            raise ValueError()
        expected = public_origin.rstrip('/') + CALLBACK
        if environment.get('DRIVE_BACKUP_OAUTH_REDIRECT_URI') != expected:
            raise ValueError()
    except Exception:
        issues.append('https_callback_origin_mismatch')

    try:
        value = environment.get('DATABASE_URL', '')
        database = urlsplit(value)
        items = parse_qsl(database.query, keep_blank_values=True, strict_parsing=True)
        options = dict(items)
        if (not value or value != value.strip() or any(ord(c) < 33 for c in value) or
                database.scheme not in {'postgres', 'postgresql'} or not database.hostname or
                not database.username or not database.password or database.path in {'', '/'} or
                database.fragment or (database.port is not None and not 1 <= database.port <= 65535) or
                len(items) != len(options) or
                set(options) - {'sslmode', 'sslrootcert', 'channel_binding', 'connect_timeout'} or
                options.get('sslmode') not in {'require', 'verify-ca', 'verify-full'} or
                ('channel_binding' in options and options['channel_binding'] not in {'disable', 'prefer', 'require'}) or
                ('connect_timeout' in options and (not re.fullmatch(r'[0-9]{1,2}', options['connect_timeout']) or
                                                  not 1 <= int(options['connect_timeout']) <= 60))):
            raise ValueError()
        if any(any(c in unquote(component or '') for c in '\r\n\0') for component in (
                database.hostname, database.username, database.password, database.path, *options.values())):
            raise ValueError()
    except Exception:
        issues.append('postgresql_tls_configuration_invalid')
    try:
        timeout = environment.get('WEB_TIMEOUT', '120')
        if not isinstance(timeout, str) or not timeout.isascii() or not timeout.isdigit() or not 120 <= int(timeout) <= 3600:
            raise ValueError()
    except Exception:
        issues.append('web_worker_timeout_too_short_or_invalid')
    return issues


def runtime_report(environment, *, expected_major=18, check_configuration=False, public_origin=''):
    report = {'status': 'FAIL', 'scope': 'offline_runtime_preflight_only', 'issues': [], 'checks': {},
              'production_database_access': False, 'google_drive_access': False,
              'database_server_version_checked': False, 'drive_space_checked': False,
              'django_configuration_checked': False, 'production_restore_verified': False}
    if type(expected_major) is not int or expected_major not in {17, 18}:
        report['issues'].append('expected_postgresql_major_invalid')
        return report
    child_environment = {name: environment[name] for name in (
        'PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT') if name in environment}
    for name, variable, pattern in (
        ('pg_dump', 'DRIVE_BACKUP_PG_DUMP_BIN', rb'\(PostgreSQL\) (\d+)\.'),
        ('pg_restore', 'DRIVE_BACKUP_PG_RESTORE_BIN', rb'\(PostgreSQL\) (\d+)\.'),
        ('gpg', 'DRIVE_BACKUP_GPG_BIN', rb'^gpg \(GnuPG\) (\d+)\.'),
    ):
        try:
            # Match the service's prefixed-setting -> legacy-alias -> PATH order.
            configured = environment.get(variable, environment.get(variable.removeprefix('DRIVE_BACKUP_'), name))
            if not isinstance(configured, str) or not configured or any(c in configured for c in '\r\n\0'):
                raise ValueError()
            binary = shutil.which(configured, path=environment.get('PATH', os.defpath))
            if not binary:
                raise ValueError()
            result = subprocess.run([binary, '--version'], env=child_environment, capture_output=True,
                                    check=True, timeout=5)
            match = re.search(pattern, result.stdout[:256])
            if not match or int(match[1]) != (2 if name == 'gpg' else expected_major):
                raise ValueError()
            report['checks'][name] = 'PASS'
            report[name + '_major'] = int(match[1])
        except Exception:
            report['checks'][name] = 'FAIL'
            report['issues'].append(name + '_missing_or_incompatible')
    try:
        crypto = importlib.import_module('cryptography')
        fernet = importlib.import_module('cryptography.fernet').Fernet
        cipher = fernet(fernet.generate_key())
        if crypto.__version__ != EXPECTED_CRYPTO or cipher.decrypt(cipher.encrypt(b'public-readiness-fixture')) != b'public-readiness-fixture':
            raise ValueError()
        report['checks']['oauth_crypto'] = 'PASS'
    except Exception:
        report['checks']['oauth_crypto'] = 'FAIL'
        report['issues'].append('locked_oauth_crypto_unavailable')
    if check_configuration:
        config_issues = configuration_issues(environment, public_origin)
        report['issues'].extend(config_issues)
        report['checks']['configuration_syntax'] = 'FAIL' if config_issues else 'PASS'
        report['activation'] = ('ENABLED' if enabled(environment.get('DRIVE_BACKUP_ENABLED')) and
                                enabled(environment.get('DRIVE_BACKUP_OAUTH_ENABLED')) else 'DISABLED')
    report['status'] = 'FAIL' if report['issues'] else 'PASS'
    return report


def build_guard(environment, *, expected_major=18, public_origin=''):
    # Legacy/disabled builds must not require optional tools or import crypto.
    # Invalid flags still fail closed before Django or a migration can run.
    flags = [environment.get(name, 'false') for name in (
        'DRIVE_BACKUP_ENABLED', 'DRIVE_BACKUP_OAUTH_ENABLED')]
    if not any(enabled(value) for value in flags):
        issues = configuration_issues(environment, public_origin)
        return {'status': 'FAIL' if issues else 'PASS', 'issues': issues,
                'scope': 'offline_disabled_build_guard', 'activation': 'DISABLED',
                'runtime_checked': False, 'production_database_access': False, 'google_drive_access': False}
    return runtime_report(environment, expected_major=expected_major,
                          check_configuration=True, public_origin=public_origin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-postgres-major', type=int, choices=(17, 18), default=18)
    parser.add_argument('--check-configuration', action='store_true')
    parser.add_argument('--build-guard', action='store_true')
    parser.add_argument('--public-origin', default=os.environ.get('DRIVE_BACKUP_PUBLIC_ORIGIN', ''))
    args = parser.parse_args(argv)
    if args.build_guard:
        result = build_guard(os.environ, expected_major=args.expected_postgres_major, public_origin=args.public_origin)
    else:
        result = runtime_report(os.environ, expected_major=args.expected_postgres_major,
                                check_configuration=args.check_configuration, public_origin=args.public_origin)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
