import hashlib
import hmac
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection


class BackupError(RuntimeError):
    pass


SHA256_PATTERN = re.compile(r'^[0-9a-fA-F]{64}$')
SQLITE_HEADER = b'SQLite format 3\x00'
POSTGRES_CUSTOM_HEADER = b'PGDMP'


def _command_timeout():
    raw_value = os.getenv('BACKUP_COMMAND_TIMEOUT', '3600')
    try:
        timeout = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise BackupError('BACKUP_COMMAND_TIMEOUT doit être un nombre entier positif.') from exc
    if timeout <= 0:
        raise BackupError('BACKUP_COMMAND_TIMEOUT doit être un nombre entier positif.')
    return timeout


def _private_temporary_path(destination):
    destination = Path(destination)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{destination.name}.',
        suffix='.tmp',
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(temporary_name)


def _unlink_if_present(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        # Preserve the original failure when best-effort cleanup also fails.
        pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise BackupError(f'Sauvegarde introuvable : {path}')
    checksum_path = path.with_name(f'{path.name}.sha256')
    temporary_path = _private_temporary_path(checksum_path)
    try:
        temporary_path.write_text(f'{sha256_file(path)}  {path.name}\n', encoding='ascii')
        os.replace(temporary_path, checksum_path)
    finally:
        _unlink_if_present(temporary_path)
    return checksum_path


def verify_checksum(path, *, required=True):
    path = Path(path).resolve()
    checksum_path = path.with_name(f'{path.name}.sha256')
    if not checksum_path.exists():
        if required:
            raise BackupError(f'Fichier de contrôle absent : {checksum_path}')
        return False
    try:
        lines = [
            line.strip()
            for line in checksum_path.read_text(encoding='ascii').splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError) as exc:
        raise BackupError(f'Fichier de contrôle illisible : {checksum_path}') from exc
    if len(lines) != 1:
        raise BackupError('Le fichier de contrôle SHA-256 doit contenir exactement une empreinte.')
    parts = lines[0].split(maxsplit=1)
    expected = parts[0].lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise BackupError('Le fichier de contrôle SHA-256 est mal formé.')
    if len(parts) == 2:
        declared_name = parts[1].strip().lstrip('*')
        if Path(declared_name).name != path.name:
            raise BackupError('Le fichier de contrôle ne correspond pas au nom de la sauvegarde.')
    actual = sha256_file(path)
    if not hmac.compare_digest(expected, actual):
        raise BackupError('La somme SHA-256 de la sauvegarde est invalide.')
    return True


def verify_sqlite_backup(path):
    path = Path(path).resolve()
    try:
        database = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
        result = database.execute('PRAGMA integrity_check').fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError('La sauvegarde SQLite est illisible ou corrompue.') from exc
    finally:
        if 'database' in locals():
            database.close()
    if not result or result[0] != 'ok':
        raise BackupError(f'Échec du contrôle d’intégrité SQLite : {result!r}')


def _postgres_environment(database):
    command_environment = os.environ.copy()
    mapping = {
        'PGDATABASE': database.get('NAME'),
        'PGHOST': database.get('HOST'),
        'PGPORT': database.get('PORT'),
        'PGUSER': database.get('USER'),
        'PGPASSWORD': database.get('PASSWORD'),
    }
    option_mapping = {
        'application_name': 'PGAPPNAME',
        'channel_binding': 'PGCHANNELBINDING',
        'connect_timeout': 'PGCONNECT_TIMEOUT',
        'gssencmode': 'PGGSSENCMODE',
        'options': 'PGOPTIONS',
        'service': 'PGSERVICE',
        'sslcert': 'PGSSLCERT',
        'sslcrl': 'PGSSLCRL',
        'sslkey': 'PGSSLKEY',
        'sslmode': 'PGSSLMODE',
        'sslrootcert': 'PGSSLROOTCERT',
        'target_session_attrs': 'PGTARGETSESSIONATTRS',
    }
    database_options = database.get('OPTIONS') or {}
    for option_name, environment_name in option_mapping.items():
        option_value = database_options.get(option_name)
        if option_value not in (None, ''):
            mapping[environment_name] = option_value
    for name, value in mapping.items():
        if value not in (None, ''):
            command_environment[name] = str(value)
    return command_environment


def _postgres_binary(name, environment_name):
    configured = os.getenv(environment_name, name)
    resolved = shutil.which(configured)
    if not resolved:
        raise BackupError(
            f'{name} est introuvable. Installez les outils client PostgreSQL ou définissez {environment_name}.'
        )
    return resolved


def _run_postgres_command(command, *, error_message, environment=None):
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            env=environment,
            timeout=_command_timeout(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f'{error_message} Délai maximal dépassé.') from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError(error_message) from exc


def _verify_postgres_archive(path):
    path = Path(path).resolve()
    with path.open('rb') as stream:
        if stream.read(len(POSTGRES_CUSTOM_HEADER)) != POSTGRES_CUSTOM_HEADER:
            raise BackupError('La sauvegarde n’est pas une archive PostgreSQL au format custom.')
    command = [_postgres_binary('pg_restore', 'PG_RESTORE_BIN'), '--list', str(path)]
    _run_postgres_command(command, error_message='L’archive PostgreSQL est invalide.')


def _backup_format(path):
    with Path(path).open('rb') as stream:
        header = stream.read(max(len(SQLITE_HEADER), len(POSTGRES_CUSTOM_HEADER)))
    if header.startswith(SQLITE_HEADER):
        return 'sqlite'
    if header.startswith(POSTGRES_CUSTOM_HEADER):
        return 'postgresql'
    raise BackupError('Format de sauvegarde inconnu ou fichier corrompu.')


def backup_database_to(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    vendor = connection.vendor
    if vendor not in {'sqlite', 'postgresql'}:
        raise BackupError(f'Moteur de base non pris en charge : {vendor}')
    if vendor == 'sqlite':
        database_name = settings.DATABASES['default']['NAME']
        if not str(database_name).startswith('file:') and str(database_name) != ':memory:':
            if Path(database_name).resolve() == path:
                raise BackupError('La sauvegarde SQLite ne peut pas écraser la base active.')

    temporary_path = _private_temporary_path(path)
    try:
        if vendor == 'sqlite':
            connection.ensure_connection()
            destination = sqlite3.connect(str(temporary_path))
            try:
                connection.connection.backup(destination)
            finally:
                destination.close()
            verify_sqlite_backup(temporary_path)
        else:
            database = settings.DATABASES['default']
            command = [
                _postgres_binary('pg_dump', 'PG_DUMP_BIN'),
                '--format=custom',
                '--no-owner',
                '--no-acl',
                '--file',
                str(temporary_path),
            ]
            _run_postgres_command(
                command,
                error_message='La sauvegarde PostgreSQL a échoué.',
                environment=_postgres_environment(database),
            )
            _verify_postgres_archive(temporary_path)

        os.replace(temporary_path, path)
        write_checksum(path)
    finally:
        _unlink_if_present(temporary_path)
    return path


def verify_database_backup(path, *, require_checksum=True, expected_vendor=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise BackupError(f'Sauvegarde introuvable : {path}')
    verify_checksum(path, required=require_checksum)
    detected_vendor = _backup_format(path)
    if expected_vendor and expected_vendor not in {'sqlite', 'postgresql'}:
        raise BackupError(f'Moteur de base non pris en charge : {expected_vendor}')
    if expected_vendor and detected_vendor != expected_vendor:
        raise BackupError(
            f'La sauvegarde {detected_vendor} ne correspond pas au moteur actif {expected_vendor}.'
        )
    if detected_vendor == 'sqlite':
        verify_sqlite_backup(path)
    else:
        _verify_postgres_archive(path)
    return path


def restore_sqlite_file(backup_path, database_path):
    backup_path = Path(backup_path).resolve()
    database_path = Path(database_path).resolve()
    if backup_path == database_path:
        raise BackupError('La source et la base SQLite cible doivent être différentes.')
    verify_sqlite_backup(backup_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    active_sidecars = [
        Path(f'{database_path}{suffix}')
        for suffix in ('-journal', '-wal', '-shm')
        if Path(f'{database_path}{suffix}').exists()
    ]
    if active_sidecars:
        names = ', '.join(path.name for path in active_sidecars)
        raise BackupError(
            f'Restauration SQLite refusée : fichiers transactionnels encore présents ({names}). '
            'Arrêtez tous les processus utilisant la base avant de réessayer.'
        )
    temporary_path = _private_temporary_path(database_path)
    try:
        with backup_path.open('rb') as source, temporary_path.open('wb') as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        verify_sqlite_backup(temporary_path)
        os.replace(temporary_path, database_path)
    finally:
        _unlink_if_present(temporary_path)
    verify_sqlite_backup(database_path)


def restore_database_from(path):
    vendor = connection.vendor
    path = verify_database_backup(path, expected_vendor=vendor)
    if vendor == 'sqlite':
        database_name = settings.DATABASES['default']['NAME']
        if str(database_name).startswith('file:') or str(database_name) == ':memory:':
            raise BackupError('La restauration directe d’une base SQLite en mémoire est interdite.')
        connection.close()
        restore_sqlite_file(path, database_name)
    elif vendor == 'postgresql':
        database = settings.DATABASES['default']
        command = [
            _postgres_binary('pg_restore', 'PG_RESTORE_BIN'),
            '--clean',
            '--if-exists',
            '--exit-on-error',
            '--single-transaction',
            '--no-owner',
            '--no-acl',
            str(path),
        ]
        connection.close()
        _run_postgres_command(
            command,
            error_message='La restauration PostgreSQL a échoué.',
            environment=_postgres_environment(database),
        )
    else:
        raise BackupError(f'Moteur de base non pris en charge : {vendor}')


def upload_backup_to_object_storage(path):
    try:
        import boto3
    except ImportError as exc:
        raise ImproperlyConfigured('boto3 est requis pour envoyer les sauvegardes hors site.') from exc

    bucket = os.getenv('BACKUP_S3_BUCKET', '').strip()
    if not bucket:
        raise ImproperlyConfigured('BACKUP_S3_BUCKET doit être défini pour l’envoi hors site.')
    path = Path(path).resolve()
    if not path.is_file():
        raise BackupError(f'Sauvegarde introuvable : {path}')
    verify_checksum(path)
    prefix = os.getenv('BACKUP_S3_PREFIX', 'database-backups').strip('/')
    key = f'{prefix}/{path.name}' if prefix else path.name
    endpoint_url = os.getenv('BACKUP_S3_ENDPOINT_URL') or os.getenv('AWS_S3_ENDPOINT_URL') or None
    if endpoint_url and urlparse(endpoint_url).scheme.lower() != 'https':
        allow_insecure = os.getenv('BACKUP_S3_ALLOW_INSECURE_ENDPOINT', '').lower() in {'1', 'true', 'yes'}
        if not allow_insecure:
            raise ImproperlyConfigured(
                'Le endpoint de sauvegarde doit utiliser HTTPS. '
                'BACKUP_S3_ALLOW_INSECURE_ENDPOINT=True est réservé aux tests locaux.'
            )
    access_key = os.getenv('BACKUP_S3_ACCESS_KEY_ID', '').strip()
    secret_key = os.getenv('BACKUP_S3_SECRET_ACCESS_KEY', '').strip()
    if bool(access_key) != bool(secret_key):
        raise ImproperlyConfigured(
            'BACKUP_S3_ACCESS_KEY_ID et BACKUP_S3_SECRET_ACCESS_KEY doivent être définis ensemble.'
        )
    client_options = {
        'endpoint_url': endpoint_url,
        'region_name': os.getenv('BACKUP_S3_REGION_NAME') or os.getenv('AWS_S3_REGION_NAME') or None,
    }
    if access_key:
        client_options.update(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
    extra_args = {}
    encryption = os.getenv('BACKUP_S3_SERVER_SIDE_ENCRYPTION', 'AES256').strip()
    if encryption and encryption not in {'AES256', 'aws:kms'}:
        raise ImproperlyConfigured(
            'BACKUP_S3_SERVER_SIDE_ENCRYPTION doit valoir AES256, aws:kms ou être vide.'
        )
    if encryption:
        extra_args['ServerSideEncryption'] = encryption
    kms_key_id = os.getenv('BACKUP_S3_SSE_KMS_KEY_ID', '').strip()
    if kms_key_id:
        if encryption != 'aws:kms':
            raise ImproperlyConfigured('BACKUP_S3_SSE_KMS_KEY_ID exige le chiffrement aws:kms.')
        extra_args['SSEKMSKeyId'] = kms_key_id
    kwargs = {'ExtraArgs': extra_args} if extra_args else {}
    checksum_path = path.with_name(f'{path.name}.sha256')
    try:
        client = boto3.client('s3', **client_options)
        client.upload_file(str(path), bucket, key, **kwargs)
        client.upload_file(str(checksum_path), bucket, f'{key}.sha256', **kwargs)
    except Exception as exc:
        raise BackupError('L’envoi de la sauvegarde hors site a échoué.') from exc
    return f's3://{bucket}/{key}'
