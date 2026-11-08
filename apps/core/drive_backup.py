"""Opt-in, bounded PostgreSQL-only export to a private Google Drive folder.

No media/storage API, scheduler, detached task, restore or production fallback.
Credentials are configured by the operator, never submitted by the Web form.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlencode
import uuid

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables


SCOPE = 'https://www.googleapis.com/auth/drive.file'
MAX_DATABASE_BYTES = 32 * 1024 ** 2
MAX_PAYLOAD_BYTES = 4 * 1024 ** 2
MAX_CIPHER_BYTES = 5 * 1024 ** 2
FOLDER_MIME = 'application/vnd.google-apps.folder'
MARKER = {'elamineBackup': 'database-only-v1'}
ERRORS = {
    'disabled': 'La sauvegarde Google Drive n’est pas activée.',
    'configuration': 'La configuration sécurisée Google Drive est incomplète ou invalide.',
    'tools': 'Des outils PostgreSQL et GPG compatibles doivent être installés sur le serveur.',
    'postgres_version': 'Les outils PostgreSQL doivent avoir la même version majeure que le serveur (18 pour la production vérifiée).',
    'database': 'La source PostgreSQL sécurisée n’est pas disponible.',
    'busy': 'Une sauvegarde est déjà en cours. Aucune seconde opération n’a été lancée.',
    'too_large': 'Le volume dépasse la limite de cette option Web. Un exécuteur dédié sera nécessaire.',
    'timeout': 'Le délai maximal de cette opération Web est dépassé.',
    'dump': 'La création ou la vérification du dump PostgreSQL a échoué.',
    'encryption': 'Le chiffrement ou sa vérification a échoué. Aucun fichier en clair n’a été envoyé.',
    'drive_auth': 'L’autorisation Google Drive doit être configurée ou renouvelée par l’administrateur.',
    'drive_folder': 'Un dossier Drive privé, appartenant à l’application, est nécessaire.',
    'drive_transfer': 'L’envoi Drive n’est pas confirmé. Vérifiez Drive avant de relancer : une copie chiffrée peut déjà exister.',
    'drive_quota': 'Drive refuse l’opération : vérifiez les droits, l’espace disponible et les quotas.',
    'integrity': 'Le contrôle de la copie chiffrée sur Drive a échoué. Ne la considérez pas comme validée.',
    'cleanup': 'Le nettoyage local a échoué. L’administrateur doit vérifier les fichiers temporaires.',
}


class DriveBackupError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in ERRORS else 'drive_transfer'
        super().__init__(ERRORS[self.code])


def _setting(name, default=''):
    return getattr(settings, name, os.environ.get(name, default))


def connection_test_only():
    # Deliberately a Python setting, not an inherited environment switch. The
    # standalone loopback OAuth harness sets it and cannot create an archive.
    return getattr(settings, 'DRIVE_BACKUP_LOCAL_CONNECTION_TEST', False) is True


@dataclass(frozen=True, repr=False)
class Config:
    enabled: bool = False
    client_id: str = field(default='', repr=False)
    client_secret: str = field(default='', repr=False)
    refresh_token: str = field(default='', repr=False)
    passphrase: str = field(default='', repr=False)
    expected_permission_id: str = field(default='', repr=False)

    @classmethod
    def from_settings(cls, *, refresh_token=None, require_refresh=True):
        enabled = _setting('DRIVE_BACKUP_ENABLED', False)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower()
        if enabled not in (True, False, '1', '0', 'true', 'false'):
            raise DriveBackupError('configuration')
        if enabled not in (True, '1', 'true'):
            return cls()
        token = (refresh_token if refresh_token is not None else
                 _setting('DRIVE_BACKUP_REFRESH_TOKEN') if require_refresh else '')
        values = [_setting('DRIVE_BACKUP_CLIENT_ID'), _setting('DRIVE_BACKUP_CLIENT_SECRET'),
                  token, _setting('DRIVE_BACKUP_PASSPHRASE')]
        required = values if require_refresh else [values[0], values[1], values[3]]
        if any(not isinstance(v, str) or v != v.strip() or not v or
               len(v) > 4096 or any(c in v for c in '\r\n\0') for v in required):
            raise DriveBackupError('configuration')
        if (not values[0].endswith('.apps.googleusercontent.com') or
                len(values[1]) < 12 or (require_refresh and len(values[2]) < 20) or len(values[3]) < 32):
            raise DriveBackupError('configuration')
        return cls(True, *values)


def availability():
    """Configuration presence only: GET must not connect, dump or run commands."""
    try:
        config = Config.from_settings()
        issues = [] if config.enabled else [ERRORS['disabled']]
        return {'enabled': config.enabled, 'configured': config.enabled,
                'ready': config.enabled, 'issues': issues}
    except DriveBackupError:
        return {'enabled': False, 'configured': False, 'ready': False,
                'issues': [ERRORS['configuration']]}


class Deadline:
    def __init__(self, seconds=80):
        self.end = time.monotonic() + seconds

    def remaining(self, cap=5):
        value = min(cap, self.end - time.monotonic())
        if value <= 0:
            raise DriveBackupError('timeout')
        return value


def _environment():
    # No inherited PGHOSTADDR, PGSERVICE, cloud credentials or user GPG config.
    return {k: os.environ[k] for k in
            ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT')
            if k in os.environ}


@sensitive_variables()
def _run(command, environment, deadline, code, *, phrase=None, cap=15):
    try:
        return subprocess.run(command, env=environment, input=phrase,
                              capture_output=True, check=True,
                              timeout=deadline.remaining(cap)).stdout
    except subprocess.TimeoutExpired:
        raise DriveBackupError('timeout') from None
    except (OSError, subprocess.SubprocessError):
        raise DriveBackupError(code) from None


def _tools(deadline):
    result = {}
    for name, variable in (('pg_dump', 'PG_DUMP_BIN'), ('pg_restore', 'PG_RESTORE_BIN'),
                           ('gpg', 'GPG_BIN')):
        configured = _setting('DRIVE_BACKUP_' + variable, _setting(variable, name))
        if not isinstance(configured, str):
            raise DriveBackupError('tools')
        binary = shutil.which(configured)
        if not binary:
            raise DriveBackupError('tools')
        version = _run([binary, '--version'], _environment(), deadline, 'tools', cap=3)
        if name.startswith('pg_'):
            match = re.search(rb'\(PostgreSQL\) (\d+)\.', version[:256])
            if not match:
                raise DriveBackupError('tools')
            result[name + '_major'] = int(match[1])
        elif not re.match(rb'gpg \(GnuPG\) 2\.', version[:256]):
            raise DriveBackupError('tools')
        result[name] = binary
    return result


@contextmanager
def _database_guard(tools):
    if connection.vendor != 'postgresql':
        raise DriveBackupError('database')
    # Shared PostgreSQL transaction-level lock: works across Web workers/instances.
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute("SET LOCAL lock_timeout = '1s'")
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '90s'")
                cursor.execute(
                    'SELECT pg_catalog.pg_try_advisory_xact_lock(1162627394), '
                    "pg_catalog.current_setting('server_version_num')::integer, "
                    'pg_catalog.pg_database_size(pg_catalog.current_database())')
                locked, version, size = cursor.fetchone()
            if not locked:
                raise DriveBackupError('busy')
            major = int(version) // 10000
            if tools['pg_dump_major'] != major or tools['pg_restore_major'] != major:
                raise DriveBackupError('postgres_version')
            if not 0 < int(size) <= MAX_DATABASE_BYTES:
                raise DriveBackupError('too_large')
            yield major
    except DriveBackupError:
        raise
    except Exception:
        raise DriveBackupError('database') from None


@sensitive_variables()
def _postgres_environment():
    database = settings.DATABASES['default']
    options = database.get('OPTIONS', {})
    if options.get('sslmode') not in {'require', 'verify-ca', 'verify-full'}:
        raise DriveBackupError('database')
    environment = _environment()
    for key, target in (('HOST', 'PGHOST'), ('PORT', 'PGPORT'), ('NAME', 'PGDATABASE'),
                        ('USER', 'PGUSER'), ('PASSWORD', 'PGPASSWORD')):
        value = str(database.get(key) or ('5432' if key == 'PORT' else ''))
        if not value or any(c in value for c in '\r\n\0'):
            raise DriveBackupError('database')
        if key in {'HOST', 'NAME', 'USER'} and ('=' in value or '://' in value):
            raise DriveBackupError('database')
        environment[target] = value
    for key, target in (('sslmode', 'PGSSLMODE'), ('sslrootcert', 'PGSSLROOTCERT'),
                        ('channel_binding', 'PGCHANNELBINDING')):
        if options.get(key):
            environment[target] = str(options[key])
    if set(options) - {'sslmode', 'sslrootcert', 'channel_binding', 'connect_timeout'}:
        raise DriveBackupError('database')
    environment.update(PGCONNECT_TIMEOUT='5', PGOPTIONS=(
        '-c default_transaction_read_only=on -c statement_timeout=20000 -c lock_timeout=2000'))
    return environment


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _symmetric_packet(path):
    with path.open('rb') as stream:
        first = stream.read(1)
    if not first:
        return False
    first = first[0]
    tag = first & 63 if first & 64 else (first >> 2) & 15
    return bool(first & 128 and tag == 3)


@sensitive_variables()
def _encrypted_archive(directory, config, tools, deadline, major):
    dump = directory / 'database.dump'
    environment = _postgres_environment()
    _run([tools['pg_dump'], '--no-password', '--format=custom', '--no-owner',
          '--no-acl', '--file', str(dump)], environment, deadline, 'dump', cap=20)
    if not dump.is_file() or not 5 < dump.stat().st_size <= MAX_PAYLOAD_BYTES - 32768:
        raise DriveBackupError('too_large')
    with dump.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise DriveBackupError('dump')
    _run([tools['pg_restore'], '--list', str(dump)], _environment(), deadline, 'dump', cap=5)
    manifest = directory / 'manifest.json'
    manifest.write_text(json.dumps({
        'format': 'elamine-database-backup-v1', 'scope': 'database_only',
        'media_included': False, 'restore_verified': False, 'postgresql_major': major,
        'created_at': timezone.now().isoformat(), 'database_sha256': _digest(dump),
    }), encoding='utf-8')
    payload = directory / 'database-backup.tar'
    with tarfile.open(payload, 'w') as archive:
        for path in (dump, manifest):
            archive.add(path, arcname=path.name, recursive=False)
    if payload.stat().st_size > MAX_PAYLOAD_BYTES:
        raise DriveBackupError('too_large')
    home = directory / 'gnupg'
    home.mkdir(mode=0o700)
    environment = _environment() | {'GNUPGHOME': str(home)}
    name = 'elamine-db-' + timezone.now().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex + '.tar.gpg'
    encrypted = directory / name
    command = [tools['gpg'], '--no-options', '--batch', '--yes', '--no-symkey-cache',
               '--pinentry-mode', 'loopback', '--passphrase-fd', '0']
    phrase = (config.passphrase + '\n').encode('utf-8')
    _run(command + ['--symmetric', '--cipher-algo', 'AES256', '--s2k-mode', '3',
                   '--s2k-digest-algo', 'SHA512', '--s2k-count', '65011712',
                   '--compress-algo', 'none', '--output', str(encrypted), str(payload)],
         environment, deadline, 'encryption', phrase=phrase)
    if not encrypted.is_file() or not 0 < encrypted.stat().st_size <= MAX_CIPHER_BYTES:
        raise DriveBackupError('encryption')
    if not _symmetric_packet(encrypted):
        raise DriveBackupError('encryption')
    decrypted = _run(command + ['--output', '-', '--decrypt', str(encrypted)],
                     environment, deadline, 'encryption', phrase=phrase)
    if not hmac.compare_digest(hashlib.sha256(decrypted).hexdigest(), _digest(payload)):
        raise DriveBackupError('encryption')
    return encrypted


class DriveClient:
    def __init__(self, config, deadline):
        self.config, self.deadline = config, deadline
        self.token = None

    @sensitive_variables()
    def _request(self, method, host, path, *, body=None, content_type='application/json', auth=True):
        if host not in {'oauth2.googleapis.com', 'www.googleapis.com'} or not path.startswith('/'):
            raise DriveBackupError('drive_transfer')
        headers = {'Content-Type': content_type}
        if auth:
            if not self.token:
                raise DriveBackupError('drive_auth')
            headers['Authorization'] = 'Bearer ' + self.token
        client = http.client.HTTPSConnection(host, timeout=self.deadline.remaining(), context=ssl.create_default_context())
        try:
            client.request(method, path, body=body, headers=headers)
            response = client.getresponse()
            if response.status == 401 or (not auth and response.status == 400):
                raise DriveBackupError('drive_auth')
            if response.status in {403, 429}:
                raise DriveBackupError('drive_quota')
            if response.status not in {200, 201}:
                raise DriveBackupError('drive_transfer')  # Never follow redirects or print provider errors.
            chunks, length = [], 0
            while True:
                timeout = self.deadline.remaining()
                if client.sock:
                    client.sock.settimeout(timeout)
                chunk = response.read1(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > 65536:
                    raise DriveBackupError('drive_transfer')
            result = json.loads(b''.join(chunks))
            if not isinstance(result, dict):
                raise DriveBackupError('drive_transfer')
            return result
        except DriveBackupError:
            raise
        except Exception:
            raise DriveBackupError('drive_transfer') from None
        finally:
            client.close()

    def authenticate(self):
        result = self._request('POST', 'oauth2.googleapis.com', '/token', auth=False,
                               content_type='application/x-www-form-urlencoded', body=urlencode({
                                   'client_id': self.config.client_id, 'client_secret': self.config.client_secret,
                                   'refresh_token': self.config.refresh_token, 'grant_type': 'refresh_token',
                               }).encode())
        token = result.get('access_token', '')
        if (not isinstance(result.get('scope'), str) or result['scope'].split() != [SCOPE] or
                not isinstance(result.get('token_type'), str) or result['token_type'].lower() != 'bearer' or
                not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]{20,4096}=*', token)):
            raise DriveBackupError('drive_auth')
        self.token = token

    def verify_account(self):
        """Recheck the selected account before any archive or folder is created."""
        expected = self.config.expected_permission_id
        if not expected:
            return  # Compatibility for standalone operators, not the Web OAuth flow.
        if not isinstance(expected, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', expected):
            raise DriveBackupError('drive_auth')
        identity = self._request('GET', 'www.googleapis.com', '/drive/v3/about?' + urlencode({
            'fields': 'user(permissionId)',
        }))
        user = identity.get('user')
        if not isinstance(user, dict) or user.get('permissionId') != expected:
            raise DriveBackupError('drive_auth')

    def _file(self, file_id):
        if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', file_id):
            raise DriveBackupError('drive_transfer')
        fields = 'id,mimeType,trashed,size,sha256Checksum,parents,appProperties,permissions(type,role)'
        return self._request('GET', 'www.googleapis.com', '/drive/v3/files/' + file_id + '?' + urlencode({'fields': fields}))

    @staticmethod
    def _private(item):
        permissions = item.get('permissions')
        return (item.get('trashed') is False and isinstance(permissions, list) and len(permissions) == 1 and
                isinstance(permissions[0], dict) and
                permissions[0].get('type') == 'user' and permissions[0].get('role') == 'owner')

    def folder(self):
        query = ("mimeType = 'application/vnd.google-apps.folder' and trashed = false and "
                 "'me' in owners and appProperties has { key='elamineBackup' and value='database-only-v1' }")
        listing = self._request('GET', 'www.googleapis.com', '/drive/v3/files?' + urlencode({
            'q': query, 'pageSize': 2, 'fields': 'nextPageToken,files(id)', 'spaces': 'drive',
        }))
        files = listing.get('files')
        if not isinstance(files, list) or len(files) > 1 or listing.get('nextPageToken'):
            raise DriveBackupError('drive_folder')
        if files:
            if not isinstance(files[0], dict):
                raise DriveBackupError('drive_folder')
            folder_id = files[0].get('id')
        else:
            created = self._request('POST', 'www.googleapis.com', '/drive/v3/files?' + urlencode({
                'ignoreDefaultVisibility': 'true', 'fields': 'id',
            }), body=json.dumps({
                'name': 'El Amine - sauvegardes chiffrees', 'mimeType': FOLDER_MIME,
                'appProperties': MARKER,
            }).encode())
            folder_id = created.get('id')
        item = self._file(folder_id)
        if (item.get('id') != folder_id or item.get('mimeType') != FOLDER_MIME or
                item.get('appProperties') != MARKER or not self._private(item)):
            raise DriveBackupError('drive_folder')
        return folder_id

    def upload(self, encrypted, folder_id):
        if (encrypted.suffix != '.gpg' or encrypted.is_symlink() or
                not re.fullmatch(r'[A-Za-z0-9._-]{1,156}\.gpg', encrypted.name) or
                not 0 < encrypted.stat().st_size <= MAX_CIPHER_BYTES or not _symmetric_packet(encrypted)):
            raise DriveBackupError('integrity')
        digest, size = _digest(encrypted), encrypted.stat().st_size
        boundary = 'elamine-' + uuid.uuid4().hex
        metadata = json.dumps({'name': encrypted.name, 'parents': [folder_id], 'appProperties': MARKER}).encode()
        body = (b'--' + boundary.encode() + b'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' +
                metadata + b'\r\n--' + boundary.encode() + b'\r\nContent-Type: application/octet-stream\r\n\r\n' +
                encrypted.read_bytes() + b'\r\n--' + boundary.encode() + b'--\r\n')
        result = self._request('POST', 'www.googleapis.com', '/upload/drive/v3/files?' + urlencode({
            'uploadType': 'multipart', 'ignoreDefaultVisibility': 'true', 'fields': 'id',
        }),
                               body=body, content_type='multipart/related; boundary=' + boundary)
        verified = self._file(result.get('id'))
        if (verified.get('sha256Checksum') != digest or str(verified.get('size')) != str(size) or
                verified.get('parents') != [folder_id] or verified.get('appProperties') != MARKER or
                not self._private(verified)):
            raise DriveBackupError('integrity')
        return {'name': encrypted.name, 'sha256': digest, 'size': size,
                'scope': 'database_only', 'restore_verified': False}


@sensitive_variables()
def create_drive_backup(config=None):
    if connection_test_only():
        raise DriveBackupError('disabled')
    # The optional configuration is supplied only by the server-side OAuth
    # session connector, never deserialized from a request or a client token.
    config = Config.from_settings() if config is None else config
    if not isinstance(config, Config):
        raise DriveBackupError('configuration')
    if not config.enabled:
        raise DriveBackupError('disabled')
    deadline = Deadline()
    tools = _tools(deadline)
    # Validate the isolated libpq environment before any source or destination access.
    _postgres_environment()
    with _database_guard(tools) as major:
        client = DriveClient(config, deadline)
        client.authenticate()
        client.verify_account()
        folder = client.folder()
        try:
            with tempfile.TemporaryDirectory(prefix='elamine-drive-') as temporary:
                directory = Path(temporary)
                encrypted = _encrypted_archive(directory, config, tools, deadline, major)
                result = client.upload(encrypted, folder)
        except DriveBackupError:
            raise
        except OSError:
            raise DriveBackupError('cleanup') from None
    return result
