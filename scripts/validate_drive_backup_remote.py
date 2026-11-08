"""Authorized one-file synthetic Drive test. Never load ERP settings or data.

The native worker keeps its disposable PostgreSQL credentials and GPG phrase.
This process keeps OAuth tokens in its ephemeral encrypted SQLite vault. Only
ciphertext and non-secret, exact-schema proof cross the local file bridge.
"""
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
from urllib.parse import urlencode
import uuid


ROOT = Path(__file__).resolve().parents[1]
MAX_CIPHER = 5 * 1024 * 1024
SCOPE = 'https://www.googleapis.com/auth/drive.file'
FIELDS = 'id,name,mimeType,size,sha256Checksum,permissions(type,role),ownedByMe,appProperties,trashed'
NATIVE_CONSTRAINTS_BY_MAJOR = {17: 20, 18: 44}
DIAGNOSTIC_STAGES = frozenset({
    'initial', 'refresh', 'identity', 'generate_id', 'create', 'metadata_before',
    'download', 'metadata_after', 'bridge_download', 'bridge_publish', 'native_restore',
    'cleanup_metadata', 'cleanup_verify', 'delete', 'confirm_absent', 'local_cleanup',
    'report', 'aborted',
})
DIAGNOSTIC_NATURES = frozenset({
    'validation', 'allowlist', 'deadline', 'http', 'timeout', 'tls', 'transport',
    'json', 'schema', 'size_limit', 'interrupted', 'unexpected',
})
METADATA_FLAG_NAMES = (
    'object_valid', 'id_match', 'name_match', 'marker_match', 'mime_match', 'stored_blob_type',
    'owned_by_me', 'not_trashed', 'size_match', 'hash_present', 'hash_format_valid',
    'hash_match', 'permissions_list_valid', 'owner_only',
)


class RemoteTestError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in {
            'bridge', 'ciphertext', 'used', 'auth', 'identity', 'network', 'quota',
            'metadata', 'download', 'native_restore', 'cleanup', 'aborted',
        } else 'network'
        super().__init__(self.code)


def stored_blob_type(value):
    """An opaque stored file, not a Drive folder, shortcut or native document.

    MIME is descriptive, not proof of ciphertext. A non-default MIME can only
    qualify together with the exact expected ciphertext SHA-256 below.
    """
    return (isinstance(value, str) and len(value) <= 255 and
            re.fullmatch(r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+', value) is not None and
            not value.lower().startswith('application/vnd.google-apps.'))


def unlinked(path):
    current = Path(path).absolute()
    while True:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            return False
        if current.parent == current:
            return True
        current = current.parent


def read_json(path, limit=65536):
    if not unlinked(path) or not path.is_file():
        raise RemoteTestError('bridge')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise RemoteTestError('bridge')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise RemoteTestError('bridge')
            value[key] = item
        return value
    try:
        result = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except Exception:
        raise RemoteTestError('bridge') from None


def write_json(path, value):
    if not unlinked(path.parent) or path.exists() or path.is_symlink():
        raise RemoteTestError('bridge')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if not unlinked(path.parent) or path.exists() or path.is_symlink():
            raise RemoteTestError('bridge')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class TestTransfer:
    """No generic file API: every request is for this run's one preallocated ID."""
    def __init__(self, config, run_id, payload):
        self.config, self.run_id, self.payload = config, run_id, payload
        self.size = len(payload)
        self.sha = hashlib.sha256(payload).hexdigest()
        self.name = 'p0-a-SYNTHETIC-' + run_id + '.tar.gpg'
        self.marker = {'p0aSyntheticRun': run_id, 'scope': 'synthetic-database-only'}
        self.file_id = None
        self.token = ''
        self.attempted = False
        self.until = time.monotonic() + 240
        self.checks = {}
        self.set_stage('initial')

    def set_stage(self, stage, *, preserve_http=False):
        # The public diagnostic never contains an endpoint, ID or raw error.
        previous_status = getattr(self, 'http_status', None) if preserve_http else None
        self.stage = stage if isinstance(stage, str) and stage in DIAGNOSTIC_STAGES else 'initial'
        self.http_status = previous_status
        self.failure_nature = 'validation'
        self.metadata_flags = {}

    def safe_diagnostic(self, error):
        """Snapshot only fixed categories; reusable without a ciphertext payload."""
        code = error.code if isinstance(error, RemoteTestError) else (
            'aborted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'network')
        stage = getattr(self, 'stage', 'initial')
        nature = getattr(self, 'failure_nature', 'unexpected')
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            nature = 'interrupted'
        result = {'code': RemoteTestError(code if isinstance(code, str) else 'network').code,
                  'stage': stage if isinstance(stage, str) and stage in DIAGNOSTIC_STAGES else 'initial',
                  'nature': nature if isinstance(nature, str) and nature in DIAGNOSTIC_NATURES else 'unexpected'}
        status = getattr(self, 'http_status', None)
        if type(status) is int and 100 <= status <= 599:
            result['http_status'] = status
        flags = getattr(self, 'metadata_flags', {})
        if isinstance(flags, dict):
            safe_flags = {name: flags[name] for name in METADATA_FLAG_NAMES
                          if type(flags.get(name)) is bool}
            if safe_flags:
                result['metadata_flags'] = safe_flags
        return result

    def metadata_diagnostics(self, value):
        """Describe comparisons, never the metadata or any permission identity."""
        valid = isinstance(value, dict)
        item = value if valid else {}
        permissions = item.get('permissions')
        digest = item.get('sha256Checksum')
        return {
            'object_valid': valid,
            'id_match': valid and item.get('id') == self.file_id,
            'name_match': valid and item.get('name') == self.name,
            'marker_match': valid and item.get('appProperties') == self.marker,
            'mime_match': valid and item.get('mimeType') == 'application/octet-stream',
            'stored_blob_type': valid and stored_blob_type(item.get('mimeType')),
            'owned_by_me': valid and item.get('ownedByMe') is True,
            'not_trashed': valid and item.get('trashed') is False,
            'size_match': valid and str(item.get('size')) == str(self.size),
            'hash_present': valid and 'sha256Checksum' in item,
            'hash_format_valid': isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) is not None,
            'hash_match': valid and digest == self.sha,
            'permissions_list_valid': isinstance(permissions, list),
            'owner_only': (isinstance(permissions, list) and len(permissions) == 1 and
                           isinstance(permissions[0], dict) and permissions[0].get('type') == 'user' and
                           permissions[0].get('role') == 'owner'),
        }

    def _allowed(self, method, host, path):
        if host == 'oauth2.googleapis.com':
            return method == 'POST' and path == '/token'
        if host != 'www.googleapis.com':
            return False
        if method == 'GET' and path in {
            '/drive/v3/about?' + urlencode({'fields': 'user(permissionId)'}),
            '/drive/v3/files/generateIds?' + urlencode({'count': 1, 'space': 'drive', 'type': 'files'}),
        }:
            return True
        if not self.file_id:
            return False
        base = '/drive/v3/files/' + self.file_id
        return ((method == 'POST' and path == '/upload/drive/v3/files?' + urlencode({
                    'uploadType': 'multipart', 'ignoreDefaultVisibility': 'true', 'fields': 'id'})) or
                (method == 'GET' and path in {base + '?' + urlencode({'fields': FIELDS}), base + '?alt=media'}) or
                (method == 'DELETE' and path == base))

    def _call(self, method, host, path, *, body=None, content_type='application/json', binary=False, missing=False):
        self.http_status = None
        self.failure_nature = 'validation'
        if not self._allowed(method, host, path):
            self.failure_nature = 'allowlist'
            raise RemoteTestError('network')
        auth = host == 'www.googleapis.com'
        if auth and not self.token:
            raise RemoteTestError('auth')
        left = self.until - time.monotonic()
        if left <= 0:
            self.failure_nature = 'deadline'
            raise RemoteTestError('network')
        headers = {'Content-Type': content_type, 'Accept-Encoding': 'identity'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        connection = http.client.HTTPSConnection(host, timeout=min(8, left), context=ssl.create_default_context())
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            self.http_status = response.status
            self.failure_nature = 'http'
            if response.status == 404 and missing:
                return None
            if response.status in {401, 400} and not auth:
                raise RemoteTestError('auth')
            if response.status in {403, 429}:
                raise RemoteTestError('quota')
            if method == 'DELETE' and response.status == 204:
                return {}
            if response.status not in {200, 201}:
                raise RemoteTestError('network')
            maximum = self.size if binary else 65536
            chunks, count = [], 0
            while True:
                left = self.until - time.monotonic()
                if left <= 0:
                    self.failure_nature = 'deadline'
                    raise RemoteTestError('network')
                if connection.sock:
                    connection.sock.settimeout(min(8, left))
                part = response.read1(4096)
                if not part:
                    break
                count += len(part)
                if count > maximum:
                    self.failure_nature = 'size_limit'
                    raise RemoteTestError('download' if binary else 'network')
                chunks.append(part)
            raw = b''.join(chunks)
            if binary:
                return raw
            if method == 'DELETE' and not raw:
                return {}
            self.failure_nature = 'json'
            value = json.loads(raw)
            if not isinstance(value, dict) or (method == 'DELETE' and value != {}):
                self.failure_nature = 'schema'
                raise RemoteTestError('network')
            self.failure_nature = 'validation'
            return value
        except RemoteTestError:
            raise
        except TimeoutError:
            self.failure_nature = 'timeout'
            raise RemoteTestError('network') from None
        except ssl.SSLError:
            self.failure_nature = 'tls'
            raise RemoteTestError('network') from None
        except (json.JSONDecodeError, UnicodeError):
            self.failure_nature = 'json'
            raise RemoteTestError('network') from None
        except Exception:
            self.failure_nature = 'transport'
            raise RemoteTestError('network') from None
        finally:
            connection.close()

    def authenticate(self):
        self.set_stage('refresh')
        value = self._call('POST', 'oauth2.googleapis.com', '/token',
                           content_type='application/x-www-form-urlencoded', body=urlencode({
                               'client_id': self.config.client_id, 'client_secret': self.config.client_secret,
                               'refresh_token': self.config.refresh_token, 'grant_type': 'refresh_token',
                           }).encode())
        token = value.get('access_token')
        if (not isinstance(value.get('scope'), str) or value['scope'].split() != [SCOPE] or
                not isinstance(value.get('token_type'), str) or value['token_type'].lower() != 'bearer' or
                not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9._~+/-]{20,4096}=*', token) is None):
            raise RemoteTestError('auth')
        self.token = token
        self.checks['refresh_token_scope'] = 'PASS'
        self.set_stage('identity')
        value = self._call('GET', 'www.googleapis.com', '/drive/v3/about?' + urlencode({'fields': 'user(permissionId)'}))
        if (not isinstance(value.get('user'), dict) or not self.config.expected_permission_id or
                value['user'].get('permissionId') != self.config.expected_permission_id):
            raise RemoteTestError('identity')
        self.checks['refresh_and_selected_identity'] = 'PASS'

    def metadata(self, *, missing=False):
        return self._call('GET', 'www.googleapis.com', '/drive/v3/files/' + self.file_id + '?' + urlencode({'fields': FIELDS}), missing=missing)

    def verify(self, value, *, cleanup=False):
        self.metadata_flags = self.metadata_diagnostics(value)
        self.failure_nature = 'validation'
        permissions = value.get('permissions') if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get('id') != self.file_id or
                value.get('name') != self.name or value.get('appProperties') != self.marker or
                not stored_blob_type(value.get('mimeType')) or
                value.get('ownedByMe') is not True or value.get('trashed') is not False or
                str(value.get('size')) != str(self.size) or
                not isinstance(permissions, list) or len(permissions) != 1 or
                not isinstance(permissions[0], dict) or permissions[0].get('type') != 'user' or
                permissions[0].get('role') != 'owner'):
            raise RemoteTestError('metadata')
        # Non-default MIME labels are accepted only with the exact ciphertext
        # hash. Retain the original bounded missing-hash cleanup policy solely
        # for the default MIME and our ID/run/name/owner/size-pinned object.
        if value.get('sha256Checksum') != self.sha and not (
                cleanup and value.get('mimeType') == 'application/octet-stream' and
                not value.get('sha256Checksum')):
            raise RemoteTestError('metadata')

    def upload_and_download(self):
        self.authenticate()
        self.set_stage('generate_id')
        value = self._call('GET', 'www.googleapis.com', '/drive/v3/files/generateIds?' + urlencode({
            'count': 1, 'space': 'drive', 'type': 'files'}))
        identifiers = value.get('ids')
        if (not isinstance(identifiers, list) or len(identifiers) != 1 or not isinstance(identifiers[0], str) or
                re.fullmatch(r'[A-Za-z0-9_-]{10,200}', identifiers[0]) is None):
            raise RemoteTestError('metadata')
        self.file_id = identifiers[0]
        self.checks['id_preallocated'] = 'PASS'
        boundary = 'p0a-' + uuid.uuid4().hex
        metadata = json.dumps({'id': self.file_id, 'name': self.name, 'mimeType': 'application/octet-stream',
                               'appProperties': self.marker}, separators=(',', ':')).encode()
        delimiter = boundary.encode()
        body = (b'--' + delimiter + b'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + metadata +
                b'\r\n--' + delimiter + b'\r\nContent-Type: application/octet-stream\r\n\r\n' + self.payload +
                b'\r\n--' + delimiter + b'--\r\n')
        self.attempted = True  # Keep ID for precise cleanup even after a lost response.
        self.checks['upload_attempted'] = True
        self.set_stage('create')
        created = self._call('POST', 'www.googleapis.com', '/upload/drive/v3/files?' + urlencode({
            'uploadType': 'multipart', 'ignoreDefaultVisibility': 'true', 'fields': 'id'}),
            body=body, content_type='multipart/related; boundary=' + boundary)
        if created.get('id') != self.file_id:
            raise RemoteTestError('metadata')
        self.checks['create_id_match'] = 'PASS'
        self.checks['ciphertext_only_upload'] = 'PASS'
        self.set_stage('metadata_before')
        self.verify(self.metadata())
        self.checks['metadata_before_download'] = 'PASS'
        self.checks['private_test_file'] = 'PASS'
        self.set_stage('download')
        downloaded = self._call('GET', 'www.googleapis.com', '/drive/v3/files/' + self.file_id + '?alt=media', binary=True)
        self.failure_nature = 'validation'
        if (len(downloaded) != self.size or
                not hmac.compare_digest(hashlib.sha256(downloaded).hexdigest(), self.sha)):
            raise RemoteTestError('download')
        self.checks['real_download_same_sha256'] = 'PASS'
        self.set_stage('metadata_after')
        self.verify(self.metadata())
        self.checks['metadata_after_download'] = 'PASS'
        return downloaded

    def cleanup(self):
        if not self.attempted or not self.file_id:
            return 'NOT CREATED'
        # Cleanup gets an independent bounded budget after any test failure.
        self.until = time.monotonic() + 30
        self.set_stage('cleanup_metadata')
        item = self.metadata(missing=True)
        for _ in range(2):
            if item is not None:
                break
            time.sleep(.3)
            item = self.metadata(missing=True)
        if item is None:
            return 'NOT FOUND AFTER ATTEMPT'
        self.set_stage('cleanup_verify', preserve_http=True)
        self.verify(item, cleanup=True)
        self.set_stage('delete')
        self._call('DELETE', 'www.googleapis.com', '/drive/v3/files/' + self.file_id)
        self.set_stage('confirm_absent')
        for _ in range(3):
            if self.metadata(missing=True) is None:
                return 'PASS'
            time.sleep(.3)
        raise RemoteTestError('cleanup')


class SyntheticBridge:
    def __init__(self, directory):
        directory = Path(directory).absolute()
        base = (ROOT / 'tmp').resolve()
        if (not unlinked(directory) or directory.resolve().parent != base or
                re.fullmatch(r'p0a-drive-remote-bridge-[0-9a-f]{32}', directory.name) is None):
            raise RemoteTestError('bridge')
        self.directory = directory.resolve(strict=True)
        request = read_json(self.directory / 'request.json', 8192)
        if (set(request) != {'run_id', 'scope', 'cipher_path', 'cipher_sha256', 'cipher_size'} or
                request['run_id'] != directory.name[-32:] or request['scope'] != 'synthetic_database_only' or
                not isinstance(request['cipher_sha256'], str) or re.fullmatch('[0-9a-f]{64}', request['cipher_sha256']) is None or
                type(request['cipher_size']) is not int or not 0 < request['cipher_size'] <= MAX_CIPHER or
                not isinstance(request['cipher_path'], str)):
            raise RemoteTestError('bridge')
        path = Path(request['cipher_path']).absolute()
        if (not unlinked(path) or path.resolve() != path or path.parent.name != 'fake-drive' or
                path.parent.parent.parent != base or not path.parent.parent.name.startswith('p0a-drive-local-') or
                path.suffix != '.gpg' or not path.is_file() or path.stat().st_size != request['cipher_size']):
            raise RemoteTestError('ciphertext')
        for name in ('downloaded.gpg', 'download.ready.json', 'native-result.json', 'remote-result.json', 'abort.json'):
            if (self.directory / name).exists() or (self.directory / name).is_symlink():
                raise RemoteTestError('bridge')
        with path.open('rb') as stream:
            payload = stream.read(MAX_CIPHER + 1)
        if len(payload) != request['cipher_size'] or hashlib.sha256(payload).hexdigest() != request['cipher_sha256']:
            raise RemoteTestError('ciphertext')
        first = payload[0]
        if not first & 128 or (first & 63 if first & 64 else (first >> 2) & 15) != 3:
            raise RemoteTestError('ciphertext')
        self.request, self.payload = request, payload
        self.used = False
        self.result = {'status': 'WAITING', 'scope': 'synthetic_database_only',
                       'production_access': False, 'media_included': False}

    def public_result(self):
        return dict(self.result)

    def public_status(self):
        return self.result['status']

    def abort(self):
        path = self.directory / 'abort.json'
        if not path.exists():
            write_json(path, {'run_id': self.request['run_id'], 'status': 'abort'})

    def cancel_if_incomplete(self):
        if self.result['status'] != 'PASS':
            try:
                self.abort()
            except Exception:
                self.result.update(status='FAIL', error_code='bridge')

    def run(self, config):
        if self.used:
            raise RemoteTestError('used')
        self.used = True
        transfer = TestTransfer(config, self.request['run_id'], self.payload)
        checks = transfer.checks
        self.result = {'status': 'RUNNING', 'scope': 'synthetic_database_only',
                       'production_access': False, 'media_included': False}
        primary_error = None
        cleanup_error = None
        completed = False

        def update_outcome():
            failure = primary_error or cleanup_error
            self.result.update(status='PASS' if completed and failure is None else 'FAIL', checks=checks,
                               cipher_sha256=self.request['cipher_sha256'], cipher_size=len(self.payload))
            if primary_error is not None:
                self.result['primary_error'] = primary_error
            if cleanup_error is not None:
                self.result['cleanup_error'] = cleanup_error
            if failure is not None:
                self.result['error_code'] = failure['code']
            else:
                self.result.pop('error_code', None)

        try:
            if (self.directory / 'abort.json').exists() or (self.directory / 'native-result.json').exists():
                raise RemoteTestError('aborted')
            downloaded = transfer.upload_and_download()
            checks.update(refresh_and_selected_identity='PASS', ciphertext_only_upload='PASS',
                          real_download_same_sha256='PASS', private_test_file='PASS')
            transfer.set_stage('bridge_download')
            with (self.directory / 'downloaded.gpg').open('xb') as stream:
                stream.write(downloaded)
                stream.flush()
                os.fsync(stream.fileno())
            transfer.set_stage('bridge_publish')
            write_json(self.directory / 'download.ready.json', {
                'run_id': self.request['run_id'], 'sha256': self.request['cipher_sha256'],
                'size': len(downloaded), 'source': 'google_drive',
            })
            transfer.set_stage('native_restore')
            until = time.monotonic() + 150
            native_path = self.directory / 'native-result.json'
            while not native_path.exists() and time.monotonic() < until:
                time.sleep(.2)
            envelope = read_json(native_path)
            if set(envelope) != {'run_id', 'result'} or envelope['run_id'] != self.request['run_id']:
                raise RemoteTestError('native_restore')
            native = envelope['result']
            if not isinstance(native, dict):
                raise RemoteTestError('native_restore')
            major = native.get('native_postgresql_major')
            # Check the exact type before dictionary lookup: bool is an int
            # subclass, and untrusted JSON may also contain unhashable values.
            if type(major) is not int or major not in NATIVE_CONSTRAINTS_BY_MAJOR:
                raise RemoteTestError('native_restore')
            expected_objects = {'tables': 6, 'sequences': 6, 'indexes': 12,
                                'constraints': NATIVE_CONSTRAINTS_BY_MAJOR[major]}
            compared = native.get('database_objects_compared')
            if (not isinstance(compared, dict) or set(compared) != set(expected_objects) or
                    any(type(compared[name]) is not int or compared[name] != count
                        for name, count in expected_objects.items())):
                raise RemoteTestError('native_restore')
            native_checks = native.get('checks', {})
            if (native.get('status') != 'PASS' or native.get('ciphertext_sha256') != self.request['cipher_sha256'] or
                    type(native.get('synthetic_row_count')) is not int or native['synthetic_row_count'] != 10 or
                    not isinstance(native_checks, dict) or
                    any(native_checks.get(name) != 'PASS' for name in (
                        'real_pg_restore', 'all_table_values_match', 'all_indexes_constraints_sequences_match',
                        'temporary_runtime_removed', 'wrong_passphrase_rejected', 'tampered_ciphertext_rejected',
                        'remote_download_sha256_match', 'remote_download_used_for_restoration'))):
                raise RemoteTestError('native_restore')
            checks.update(real_pg_restore='PASS', six_tables_and_values_match='PASS',
                          indexes_constraints_sequences_match='PASS', wrong_key_and_tamper_rejected='PASS',
                          native_runtime_removed='PASS')
            self.result['postgresql_major'] = major
            self.result['synthetic_row_count'] = native.get('synthetic_row_count')
            completed = True
        except Exception as error:
            primary_error = transfer.safe_diagnostic(
                error if isinstance(error, RemoteTestError) else RemoteTestError('native_restore'))
            self.cancel_if_incomplete()
        except (KeyboardInterrupt, SystemExit) as error:
            primary_error = transfer.safe_diagnostic(error)
            self.cancel_if_incomplete()
            raise
        finally:
            if not completed:
                primary_error = primary_error or transfer.safe_diagnostic(RemoteTestError('aborted'))
                self.cancel_if_incomplete()
            try:
                cleanup = transfer.cleanup()
                checks['remote_file_cleanup'] = cleanup
                if cleanup != 'PASS' and transfer.attempted:
                    cleanup_error = transfer.safe_diagnostic(RemoteTestError('cleanup'))
            except Exception as error:
                checks['remote_file_cleanup'] = 'FAIL'
                cleanup_error = transfer.safe_diagnostic(error)
            update_outcome()
            if transfer.file_id:
                self.result['test_file_id'] = transfer.file_id
            # Only an encrypted, owned bridge output; preserve reports, never
            # delete a directory, unrelated object or user-supplied path.
            downloaded_path = self.directory / 'downloaded.gpg'
            if downloaded_path.exists():
                transfer.set_stage('local_cleanup')
                if (self.directory / 'native-result.json').exists() and unlinked(downloaded_path):
                    try:
                        downloaded_path.unlink()
                        checks['downloaded_ciphertext_removed'] = 'PASS'
                    except OSError:
                        checks['downloaded_ciphertext_removed'] = 'FAIL'
                        cleanup_error = cleanup_error or transfer.safe_diagnostic(RemoteTestError('cleanup'))
                else:
                    checks['downloaded_ciphertext_removed'] = 'PENDING NATIVE WORKER'
                    cleanup_error = cleanup_error or transfer.safe_diagnostic(RemoteTestError('cleanup'))
            update_outcome()
            try:
                transfer.set_stage('report')
                write_json(self.directory / 'remote-result.json', self.result)
            except Exception:
                self.result['report_error'] = transfer.safe_diagnostic(RemoteTestError('bridge'))
                self.result.update(status='FAIL', error_code=(primary_error or cleanup_error or
                                                              self.result['report_error'])['code'])
                raise RemoteTestError('bridge') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', required=True)
    parser.add_argument('--bridge', required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    for name in ('p0a-drive-remote-dependencies-20261108', 'p0a-drive-oauth-dependencies-20261108'):
        optional = ROOT / 'tmp' / name
        if optional.is_dir():
            sys.path.insert(0, str(optional))
            break
    from scripts import launch_drive_oauth_local as launcher
    launcher.ORIGIN = 'http://127.0.0.1:8001'
    bridge = None
    try:
        bridge = SyntheticBridge(args.bridge)
        client = launcher.read_client(args.client_file)
        launcher.serve(client, synthetic_test=bridge)
        print(json.dumps(bridge.public_result()), flush=True)
        return 0 if bridge.public_status() == 'PASS' else 1
    except KeyboardInterrupt:
        return 1
    except Exception:
        print('SYNTHETIC_DRIVE_TEST_FAILED: local configuration or test unavailable.', flush=True)
        return 1
    finally:
        if bridge is not None:
            try:
                bridge.cancel_if_incomplete()
            except Exception:
                pass  # No diagnostics containing a request, OAuth config or key.


if __name__ == '__main__':
    raise SystemExit(main())
