"""Exercise the real Drive-backup service using disposable PostgreSQL/TLS/GPG.

Google's HTTPS transport is replaced with a local ciphertext-only sink. An
optional filesystem bridge lets a separate, authorized parent upload/download
that ciphertext; this worker never receives OAuth credentials or contacts Google.
No ERP settings, .env, existing database, bucket or Google account is accessed.
No existing cluster or runtime path can be supplied. Results are expurgated.
Without --remote-bridge the round trip is local-only. The optional parent bridge
attests a real transfer separately; the worker restores only its returned bytes.
"""
import argparse
from contextlib import ExitStack
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
TABLES = ('p0a_document', 'p0a_item', 'p0a_line', 'p0a_media', 'p0a_party', 'p0a_payment')
MARKER = 'P0-A DRIVE SYNTHETIC ONLY'
SCOPE = 'https://www.googleapis.com/auth/drive.file'
BRIDGE_PREFIX = 'p0a-drive-remote-bridge-'
BRIDGE_MAX_WAIT_SECONDS = 1200
BRIDGE_MAX_CIPHER_BYTES = 5 * 1024 ** 2
BRIDGE_JSON_BYTES = 4096


class NativeFailure(RuntimeError):
    def __init__(self, tool, returncode):
        self.tool, self.returncode = tool, returncode
        super().__init__('A disposable native operation failed')


class RemoteBridgeFailure(RuntimeError):
    """Only a fixed label, never bridge contents or credentials, is reported."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _bridge_timeout(timeout):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or
            not 0 < timeout <= BRIDGE_MAX_WAIT_SECONDS):
        raise RemoteBridgeFailure('invalid_remote_bridge_timeout')
    return timeout


def _is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _bridge_directory(path, *, create=False):
    base = ROOT.resolve() / 'tmp'
    if _is_link(base):
        raise RemoteBridgeFailure('invalid_remote_bridge_directory')
    candidate = Path(path).absolute()
    if (candidate.parent != base or
            re.fullmatch(BRIDGE_PREFIX + r'[0-9a-f]{32}', candidate.name) is None or
            _is_link(candidate) or candidate.resolve() != candidate):
        raise RemoteBridgeFailure('invalid_remote_bridge_directory')
    if create:
        base.mkdir(exist_ok=True)
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            raise RemoteBridgeFailure('remote_bridge_already_exists') from None
    if not candidate.is_dir() or _is_link(candidate) or candidate.resolve() != candidate:
        raise RemoteBridgeFailure('invalid_remote_bridge_directory')
    return candidate


def prepare_remote_bridge(path):
    """Create one new, direct child of ROOT/tmp; never adopt an existing path."""
    return _bridge_directory(path, create=True)


def _bridge_file(path, parent, maximum):
    path, parent = Path(path), Path(parent)
    if (path.parent != parent or _is_link(parent) or parent.resolve() != parent or
            _is_link(path) or path.resolve() != path):
        raise RemoteBridgeFailure('invalid_remote_bridge_file')
    try:
        info = path.stat()
    except OSError:
        raise RemoteBridgeFailure('invalid_remote_bridge_file') from None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            not 0 < info.st_size <= maximum):
        raise RemoteBridgeFailure('invalid_remote_bridge_file')
    return info


def _unique_bridge_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteBridgeFailure('invalid_remote_bridge_json')
        result[key] = value
    return result


def _read_bridge_json(path, parent):
    _bridge_file(path, parent, BRIDGE_JSON_BYTES)
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(BRIDGE_JSON_BYTES + 1)
        if len(raw) > BRIDGE_JSON_BYTES:
            raise RemoteBridgeFailure('invalid_remote_bridge_json')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_bridge_object)
        if not isinstance(value, dict):
            raise RemoteBridgeFailure('invalid_remote_bridge_json')
        return value
    except RemoteBridgeFailure:
        raise
    except Exception:
        raise RemoteBridgeFailure('invalid_remote_bridge_json') from None


def write_bridge_json(bridge, name, value):
    bridge = _bridge_directory(bridge)
    if name not in {'request.json', 'native-result.json'}:
        raise RemoteBridgeFailure('invalid_remote_bridge_output')
    path = bridge / name
    if path.exists() or _is_link(path):
        raise RemoteBridgeFailure('remote_bridge_output_already_exists')
    raw = json.dumps(value, sort_keys=True, allow_nan=False) + '\n'
    descriptor, temporary_name = tempfile.mkstemp(prefix='.' + name + '.', suffix='.pending', dir=bridge)
    pending = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _bridge_directory(bridge)
        if path.exists() or _is_link(path):
            raise RemoteBridgeFailure('remote_bridge_output_already_exists')
        os.replace(pending, path)
    finally:
        # Only the fresh temporary file owned by this publication is eligible.
        try:
            _bridge_directory(bridge)
            pending.unlink(missing_ok=True)
        except (OSError, RemoteBridgeFailure):
            pass


def publish_bridge_request(bridge, runtime, ciphertext):
    bridge = _bridge_directory(bridge)
    runtime, ciphertext = Path(runtime), Path(ciphertext)
    if (runtime.parent != ROOT.resolve() / 'tmp' or
            not runtime.name.startswith('p0a-drive-local-') or _is_link(runtime) or
            runtime.resolve() != runtime or ciphertext.parent != runtime / 'fake-drive' or
            re.fullmatch(r'[A-Za-z0-9._-]{1,156}\.gpg', ciphertext.name) is None):
        raise RemoteBridgeFailure('invalid_remote_bridge_ciphertext')
    info = _bridge_file(ciphertext, runtime / 'fake-drive', BRIDGE_MAX_CIPHER_BYTES)
    request = {'run_id': bridge.name[len(BRIDGE_PREFIX):], 'scope': 'synthetic_database_only',
               'cipher_path': str(ciphertext), 'cipher_sha256': digest(ciphertext),
               'cipher_size': info.st_size}
    write_bridge_json(bridge, 'request.json', request)
    return request


def wait_for_remote_download(bridge, request, *, timeout=BRIDGE_MAX_WAIT_SECONDS):
    """Accept one bounded ciphertext attested by the authorized parent, not OAuth."""
    bridge = _bridge_directory(bridge)
    timeout = _bridge_timeout(timeout)
    run_id = bridge.name[len(BRIDGE_PREFIX):]
    if (not isinstance(request, dict) or set(request) != {
            'run_id', 'scope', 'cipher_path', 'cipher_sha256', 'cipher_size'} or
            request['run_id'] != run_id or request['scope'] != 'synthetic_database_only' or
            not isinstance(request['cipher_path'], str) or
            not isinstance(request['cipher_sha256'], str) or
            re.fullmatch(r'[0-9a-f]{64}', request['cipher_sha256']) is None or
            type(request['cipher_size']) is not int or
            not 0 < request['cipher_size'] <= BRIDGE_MAX_CIPHER_BYTES):
        raise RemoteBridgeFailure('invalid_remote_bridge_request')
    deadline = time.monotonic() + timeout
    while True:
        _bridge_directory(bridge)
        for name in ('abort.json', 'download.ready.json', 'downloaded.gpg'):
            if _is_link(bridge / name):
                raise RemoteBridgeFailure('invalid_remote_bridge_file')
        abort = bridge / 'abort.json'
        if abort.exists():
            value = _read_bridge_json(abort, bridge)
            if value != {'run_id': run_id, 'status': 'abort'}:
                raise RemoteBridgeFailure('invalid_remote_bridge_abort')
            raise RemoteBridgeFailure('remote_bridge_aborted')
        ready = bridge / 'download.ready.json'
        if ready.exists():
            value = _read_bridge_json(ready, bridge)
            if (set(value) != {'run_id', 'sha256', 'size', 'source'} or
                    value.get('run_id') != run_id or value.get('source') != 'google_drive' or
                    type(value.get('size')) is not int or value['size'] != request['cipher_size'] or
                    value.get('sha256') != request['cipher_sha256']):
                raise RemoteBridgeFailure('invalid_remote_bridge_ready')
            downloaded = bridge / 'downloaded.gpg'
            info = _bridge_file(downloaded, bridge, BRIDGE_MAX_CIPHER_BYTES)
            if info.st_size != request['cipher_size'] or digest(downloaded) != request['cipher_sha256']:
                raise RemoteBridgeFailure('remote_bridge_ciphertext_mismatch')
            return downloaded
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteBridgeFailure('remote_bridge_timeout')
        time.sleep(min(0.25, remaining))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def system_environment():
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATHEXT', 'COMSPEC', 'SYSTEMDRIVE'}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def msys_path(value):
    """Explicit conversion for Git for Windows' MSYS GPG/OpenSSL, not a shell."""
    text = str(value).replace('\\', '/')
    if re.match(r'^[A-Za-z]:/', text):
        return '/' + text[0].lower() + text[2:]
    return text


def startup_diagnostics(runtime):
    """Publish fixed labels only, never PostgreSQL/pg_ctl raw output or paths."""
    patterns = {
        'empty_or_invalid_command_option': r'(invalid argument|requires an argument|invalid command.line|unrecognized option)',
        'invalid_socket_directory_configuration': r'(invalid|unrecognized).*unix_socket_directories',
        'tls_not_supported_by_binary': r'SSL is not supported by this build',
        'tls_certificate_load_failure': r'could not (load|open).*certificate',
        'tls_private_key_load_failure': r'could not (load|open).*private key',
        'tls_private_key_permissions': r'private key file.*(permissions|owned|access)',
        'tls_key_and_certificate_mismatch': r'(key values mismatch|does not match.*certificate)',
        'address_or_port_in_use': r'(address already in use|only one usage of each socket address)',
        'tcp_listener_failure': r'(could not bind|could not create.*socket|could not listen)',
        'configuration_parse_failure': r'(syntax error|unrecognized configuration parameter|invalid value for parameter)',
        'startup_permission_denied': r'permission denied',
        'missing_runtime_dependency': r'(specified module could not be found|\.dll.*not found|library.*not found)',
        'missing_cluster_or_log_file': r'(could not (open|access).*(file|directory)|does not exist|no such file)',
        'windows_restricted_token_failure': r'(restricted token|restricted process|privileges|CreateProcess|CreateRestrictedToken)',
        'windows_process_token_open_failure': r'could not open process token',
        'windows_token_creation_failure': r'could not create restricted token',
        'windows_token_information_failure': r'could not get token information',
        'windows_privilege_lookup_failure': r'could not get LUIDs for privileges',
        'command_shell_or_program_failure': r'(not recognized|could not execute|could not start.*postgres|program.*not found)',
        'pg_ctl_usage_or_options_error': r'(Try .*help|unrecognized|invalid option|no operation specified)',
        'pg_ctl_reported_start_failure': r'could not start server',
        'postgres_reported_fatal_error': r'\bFATAL:',
    }
    labels, observed, byte_counts, windows_codes = set(), [], {}, set()
    for name in ('server.log', 'pg_ctl-stdout.log', 'pg_ctl-stderr.log'):
        source = runtime / name
        if source.is_file() and not source.is_symlink():
            data = source.read_bytes()[-32768:]
            text = data.decode(errors='replace').replace('\0', '')
            observed.append(name)
            byte_counts[name] = len(data)
            windows_codes.update(int(value) for value in re.findall(r'error code\s+(\d{1,10})', text))
            labels.update(label for label, expression in patterns.items()
                          if re.search(expression, text, flags=re.IGNORECASE))
    return {'log_files_present': observed, 'log_byte_counts': byte_counts,
            'fixed_diagnostic_labels': sorted(labels), 'windows_error_codes': sorted(windows_codes)}


def manifest(connection):
    from psycopg2 import sql

    result = {'tables': {}, 'sequences': {}, 'indexes': [], 'constraints': []}
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        names = [row[0] for row in cursor.fetchall()]
        if tuple(names) != TABLES:
            raise RuntimeError('The exact six fictitious tables are required')
        for name in names:
            cursor.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t').format(sql.Identifier('public', name)))
            rows = sorted(json.dumps(json.loads(row[0]), sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')) for row in cursor.fetchall())
            result['tables'][name] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
        cursor.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        for (name,) in cursor.fetchall():
            cursor.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier('public', name)))
            result['sequences'][name] = list(cursor.fetchone())
        cursor.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname")
        result['indexes'] = cursor.fetchall()
        cursor.execute("SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY conrelid::regclass::text,conname")
        result['constraints'] = cursor.fetchall()
    return json.loads(json.dumps(result))


def seed(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        if cursor.fetchone()[0] != 0:
            raise RuntimeError('Refusing to seed a nonempty database')
        cursor.execute('''
            CREATE TABLE p0a_party (
              id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              name text NOT NULL UNIQUE, optional_note text, metadata jsonb NOT NULL);
            CREATE TABLE p0a_item (
              id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              label text NOT NULL UNIQUE, pieces integer NOT NULL CHECK(pieces >= 0),
              price numeric(16,2) NOT NULL CHECK(price >= 0), tags text[] NOT NULL);
            CREATE TABLE p0a_document (
              id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              party_id bigint REFERENCES p0a_party(id), reference text NOT NULL UNIQUE,
              issued_on date NOT NULL, total numeric(16,2) NOT NULL CHECK(total >= 0), status text NOT NULL);
            CREATE TABLE p0a_line (
              id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              document_id bigint NOT NULL REFERENCES p0a_document(id),
              item_id bigint NOT NULL REFERENCES p0a_item(id),
              quantity integer NOT NULL CHECK(quantity > 0), price numeric(16,2) NOT NULL);
            CREATE TABLE p0a_payment (
              id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              document_id bigint NOT NULL REFERENCES p0a_document(id),
              amount numeric(16,2) NOT NULL CHECK(amount > 0));
            CREATE TABLE p0a_media (
              id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              document_id bigint REFERENCES p0a_document(id), path text NOT NULL UNIQUE,
              sha256 char(64) NOT NULL);
            CREATE INDEX p0a_document_unpaid ON p0a_document(party_id) WHERE status = 'unpaid';
            CREATE INDEX p0a_line_item ON p0a_line(item_id);
        ''')
        cursor.execute('INSERT INTO p0a_party(name,optional_note,metadata) VALUES (%s,NULL,%s)',
                       ('SYNTHETIC العربية éè 漢', json.dumps({'fixture': MARKER, 'real': False})))
        cursor.execute("INSERT INTO p0a_item(label,pieces,price,tags) VALUES ('Invented carton24',118,10.25,ARRAY['carton','piece']),('Invented piece',60,7.00,ARRAY[]::text[])")
        cursor.execute("INSERT INTO p0a_document(party_id,reference,issued_on,total,status) VALUES (1,'SYNTH-001','2020-01-01',20.50,'partial'),(NULL,'SYNTH-002','2020-01-02',7.00,'unpaid')")
        cursor.execute('INSERT INTO p0a_line(document_id,item_id,quantity,price) VALUES (1,1,2,10.25),(2,2,1,7.00)')
        cursor.execute('INSERT INTO p0a_payment(document_id,amount) VALUES (1,5.00)')
        for index in range(2):
            cursor.execute('INSERT INTO p0a_media(document_id,path,sha256) VALUES (%s,%s,%s)',
                           (1 if index == 0 else None, f'invented/not-included-{index}.txt', '0' * 64))
    connection.commit()


class FakeResponse:
    def __init__(self, value):
        self.status = 200
        self.stream = io.BytesIO(json.dumps(value).encode())

    def read1(self, size):
        return self.stream.read(size)


class FakeGoogleTransport:
    """The real DriveClient talks to this in-memory provider, never to Google."""

    def __init__(self, destination, credentials):
        self.destination, self.credentials = destination, credentials
        self.folder_exists = False
        self.stored = None
        self.upload_count = 0
        self.calls = 0
        self.folder_id = 'synthetic_folder_12345'
        self.file_id = 'synthetic_cipher_12345'
        self.access_token = secrets.token_urlsafe(48)

    @staticmethod
    def private_file(**fields):
        return {'trashed': False, 'permissions': [{'type': 'user', 'role': 'owner'}],
                'appProperties': {'elamineBackup': 'database-only-v1'}, **fields}

    def connection(self, host, **kwargs):
        if host not in {'oauth2.googleapis.com', 'www.googleapis.com'}:
            raise RuntimeError('Unexpected Google transport host')
        provider = self

        class Connection:
            sock = None

            def request(self, method, path, body=None, headers=None):
                self.value = provider.handle(host, method, path, body, headers or {})

            def getresponse(self):
                return FakeResponse(self.value)

            def close(self):
                pass

        return Connection()

    def handle(self, host, method, path, body, headers):
        self.calls += 1
        parsed = urlsplit(path)
        if host == 'oauth2.googleapis.com' and method == 'POST' and parsed.path == '/token':
            fields = parse_qs(body.decode(), strict_parsing=True)
            expected = {key: [value] for key, value in self.credentials.items()}
            expected['grant_type'] = ['refresh_token']
            if fields != expected or 'Authorization' in headers:
                raise RuntimeError('Unexpected synthetic token request')
            return {'scope': SCOPE, 'token_type': 'Bearer', 'access_token': self.access_token}
        if host != 'www.googleapis.com' or headers.get('Authorization') != 'Bearer ' + self.access_token:
            raise RuntimeError('Unexpected synthetic authorization')
        if method == 'GET' and parsed.path == '/drive/v3/files':
            return {'files': [{'id': self.folder_id}] if self.folder_exists else []}
        if method == 'POST' and parsed.path == '/drive/v3/files':
            metadata = json.loads(body)
            if (metadata.get('mimeType') != 'application/vnd.google-apps.folder' or
                    metadata.get('appProperties') != {'elamineBackup': 'database-only-v1'} or
                    'permissions' in metadata):
                raise RuntimeError('Unexpected synthetic folder metadata')
            self.folder_exists = True
            return {'id': self.folder_id}
        if method == 'GET' and parsed.path == '/drive/v3/files/' + self.folder_id:
            return self.private_file(id=self.folder_id, mimeType='application/vnd.google-apps.folder')
        if method == 'POST' and parsed.path == '/upload/drive/v3/files':
            prefix = 'multipart/related; boundary='
            if not headers.get('Content-Type', '').startswith(prefix):
                raise RuntimeError('Unexpected synthetic multipart type')
            boundary = headers['Content-Type'][len(prefix):].encode()
            parts = body.split(b'--' + boundary)
            if len(parts) != 4 or parts[0] != b'' or parts[-1] != b'--\r\n':
                raise RuntimeError('Unexpected multipart structure')
            metadata = json.loads(parts[1].split(b'\r\n\r\n', 1)[1][:-2])
            ciphertext = parts[2].split(b'\r\n\r\n', 1)[1][:-2]
            name = metadata.get('name', '')
            if (not re.fullmatch(r'[A-Za-z0-9._-]{1,156}\.gpg', name) or
                    metadata.get('parents') != [self.folder_id] or
                    metadata.get('appProperties') != {'elamineBackup': 'database-only-v1'}):
                raise RuntimeError('Unexpected ciphertext metadata')
            if not ciphertext or not ciphertext[0] & 128:
                raise RuntimeError('The fake provider accepts only ciphertext')
            tag = ciphertext[0] & 63 if ciphertext[0] & 64 else (ciphertext[0] >> 2) & 15
            if tag != 3:
                raise RuntimeError('The fake provider rejects plaintext')
            self.stored = self.destination / name
            with self.stored.open('xb') as output:
                output.write(ciphertext)
            self.upload_count += 1
            return {'id': self.file_id}
        if method == 'GET' and parsed.path == '/drive/v3/files/' + self.file_id and self.stored:
            return self.private_file(id=self.file_id, size=str(self.stored.stat().st_size),
                                     sha256Checksum=digest(self.stored), parents=[self.folder_id])
        raise RuntimeError('Unexpected synthetic Google request')


def run_validation(postgres_bin, gpg_bin, openssl_bin, remote_bridge=None,
                   remote_wait_seconds=BRIDGE_MAX_WAIT_SECONDS):
    if remote_bridge is not None:
        _bridge_timeout(remote_wait_seconds)
    binary = Path(postgres_bin).resolve(strict=True)
    suffix = '.exe' if os.name == 'nt' else ''
    tools = {name: binary / (name + suffix) for name in ('initdb', 'pg_ctl', 'postgres', 'pg_dump', 'pg_restore')}
    gpg, openssl = Path(gpg_bin).resolve(strict=True), Path(openssl_bin).resolve(strict=True)
    gpgconf = gpg.with_name('gpgconf' + suffix)
    if not all(item.is_file() for item in (*tools.values(), gpg, openssl, gpgconf)):
        raise RuntimeError('Required PostgreSQL, GPG, gpgconf or OpenSSL binary is missing')
    lexical_base = ROOT / 'tmp'
    if lexical_base.is_symlink() or (hasattr(lexical_base, 'is_junction') and lexical_base.is_junction()):
        raise RuntimeError('A linked temporary base is forbidden')
    base = lexical_base.resolve()
    base.relative_to(ROOT.resolve())
    base.mkdir(exist_ok=True)
    bridge = prepare_remote_bridge(remote_bridge) if remote_bridge is not None else None
    runtime = Path(tempfile.mkdtemp(prefix='p0a-drive-local-', dir=base)).resolve()
    identity = secrets.token_hex(8)
    user, source, target = 'p0a_' + identity, 'p0a_source_' + identity, 'p0a_restore_' + identity
    password, passphrase = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    credentials = {'client_id': 'synthetic.apps.googleusercontent.com',
                   'client_secret': secrets.token_urlsafe(32), 'refresh_token': secrets.token_urlsafe(48)}
    secret_values = (password, passphrase, credentials['client_secret'], credentials['refresh_token'])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment = system_environment()
    environment.update(LC_ALL='C', LANG='C')
    environment['PATH'] = str(binary) + os.pathsep + str(gpg.parent) + os.pathsep + environment.get('PATH', '')
    certificate = runtime / 'cluster' / 'server.crt'
    pg_env = environment | {'PGHOST': '127.0.0.1', 'PGPORT': str(port), 'PGUSER': user,
                            'PGPASSWORD': password, 'PGDATABASE': source,
                            'PGSSLMODE': 'verify-full', 'PGSSLROOTCERT': str(certificate),
                            'PGCONNECT_TIMEOUT': '5'}
    allowed_tools = {str(item).casefold() for item in (*tools.values(), gpg, openssl, gpgconf)}
    original_run = subprocess.run
    started = False
    checks = {}
    result = {'scope': 'LOCAL SYNTHETIC DATABASE ONLY', 'checks': checks,
              'production_access': False, 'gcp_access': False, 'real_google_drive_access': False,
              'remote_drive_upload': 'NOT RUN', 'media_backup': 'NOT INCLUDED',
              'postgresql_18_validation': 'NOT RUN unless the explicitly supplied binaries are version 18'}
    stage, start = 'prerequisites', time.monotonic()

    def native_run(command, **kwargs):
        arguments = [str(item) for item in command]
        executable = str(Path(arguments[0]).resolve()).casefold()
        if executable not in allowed_tools or kwargs.get('shell'):
            raise RuntimeError('Unexpected native executable')
        if any(secret in argument for secret in secret_values for argument in arguments):
            raise RuntimeError('A synthetic secret was put in a process argument')
        child_env = dict(kwargs.get('env', environment))
        if any(child_env.get(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE',
                                             'GOOGLE_APPLICATION_CREDENTIALS', 'AWS_ACCESS_KEY_ID')):
            raise RuntimeError('Unexpected inherited connection configuration')
        if Path(arguments[0]).stem in {'pg_dump', 'pg_restore'}:
            listing = '--list' in arguments or '--version' in arguments
            if not listing and (child_env.get('PGHOST') != '127.0.0.1' or
                                child_env.get('PGPORT') != str(port) or
                                child_env.get('PGDATABASE') not in {source, target} or
                                child_env.get('PGUSER') != user or child_env.get('PGSSLMODE') != 'verify-full'):
                raise RuntimeError('Native PostgreSQL connection must remain on the fresh TLS loopback cluster')
        if Path(arguments[0]).resolve() == gpg:
            # Even --version must not consult the operator's personal keyring
            # or options. The service's encryption still uses its own fresh home.
            if not child_env.get('GNUPGHOME'):
                probe_home = runtime / 'probe-gnupg'
                probe_home.mkdir(mode=0o700, exist_ok=True)
                child_env['GNUPGHOME'] = str(probe_home)
            if '--no-options' not in arguments:
                arguments.insert(1, '--no-options')
        # Git's MSYS programs receive explicit Unix paths. Other native tools
        # keep normal Windows paths. No shell or generated command string is used.
        if os.name == 'nt' and 'Git' in gpg.parts and Path(arguments[0]).parent == gpg.parent:
            arguments[1:] = [msys_path(value) if re.match(r'^[A-Za-z]:[\\/]', value) else value
                              for value in arguments[1:]]
            if child_env.get('GNUPGHOME'):
                child_env['GNUPGHOME'] = msys_path(child_env['GNUPGHOME'])
            child_env['MSYS_NO_PATHCONV'] = '1'
        kwargs['env'] = child_env
        kwargs.setdefault('timeout', 180)
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        # File handles, not captured pipes: postgres can inherit pg_ctl's handles
        # on Windows. The files belong only to this fresh synthetic runtime.
        with ExitStack() as output_files:
            if Path(arguments[0]).stem == 'pg_ctl':
                kwargs.pop('capture_output', None)
                kwargs['stdout'] = output_files.enter_context((runtime / 'pg_ctl-stdout.log').open('ab'))
                kwargs['stderr'] = output_files.enter_context((runtime / 'pg_ctl-stderr.log').open('ab'))
            completed = original_run(arguments, **kwargs)
        captured = (completed.stdout or b'') + (completed.stderr or b'')
        if isinstance(captured, str):
            captured = captured.encode()
        if any(secret.encode() in captured for secret in secret_values):
            raise RuntimeError('A synthetic secret appeared in native output')
        return completed

    def command(arguments, env=None, *, input_bytes=None, check=True):
        completed = subprocess.run(arguments, env=env or environment, input=input_bytes,
                                   capture_output=True, timeout=180)
        if check and completed.returncode:
            raise NativeFailure(Path(arguments[0]).stem, completed.returncode)
        return completed

    with ExitStack() as guards:
        guards.enter_context(patch.dict(os.environ, environment, clear=True))
        guards.enter_context(patch('dotenv.load_dotenv', side_effect=RuntimeError('Loading .env is forbidden')))
        guards.enter_context(patch.object(subprocess, 'run', new=native_run))
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        original_resolve = socket.getaddrinfo

        def require_loopback(address):
            if not isinstance(address, tuple) or address[:2] != ('127.0.0.1', port):
                raise RuntimeError('External network access is forbidden')

        def connect(sock, address):
            require_loopback(address)
            return original_connect(sock, address)

        def connect_ex(sock, address):
            require_loopback(address)
            return original_connect_ex(sock, address)

        def resolve(host, requested_port, *args, **kwargs):
            require_loopback((host, int(requested_port)))
            return original_resolve(host, requested_port, *args, **kwargs)

        guards.enter_context(patch.object(socket.socket, 'connect', connect))
        guards.enter_context(patch.object(socket.socket, 'connect_ex', connect_ex))
        guards.enter_context(patch.object(socket, 'getaddrinfo', resolve))
        try:
            result['postgresql_version'] = command([tools['postgres'], '--version']).stdout.decode().strip()
            result['gpg_version'] = command([gpg, '--version']).stdout.decode().splitlines()[0]
            stage = 'initialize_cluster'
            password_file = runtime / 'init-password'
            password_file.write_text(password, encoding='utf-8')
            try:
                command([tools['initdb'], '-D', runtime / 'cluster', '-U', user,
                         '--pwfile', password_file, '--auth-local=scram-sha-256',
                         '--auth-host=scram-sha-256', '--encoding=UTF8', '--locale=C'])
            finally:
                password_file.unlink(missing_ok=True)
            stage = 'create_disposable_tls_certificate'
            command([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                     '-keyout', runtime / 'cluster' / 'server.key', '-out', certificate,
                     '-days', '1', '-subj', '/CN=localhost',
                     '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'])
            (runtime / 'cluster' / 'pg_hba.conf').write_text(
                'hostnossl all all 127.0.0.1/32 reject\n'
                'hostssl all all 127.0.0.1/32 scram-sha-256\n', encoding='ascii')
            stage = 'start_tls_cluster'
            server_options = f'-h 127.0.0.1 -p {port} -c ssl=on'
            if os.name != 'nt':
                # Windows has no Unix-domain socket directory to override.
                server_options += ' -c unix_socket_directories='
            command([tools['pg_ctl'], '-D', runtime / 'cluster', '-l', runtime / 'server.log',
                     '-o', server_options, '-w', 'start'])
            started = True
            checks['fresh_loopback_tls_cluster'] = 'PASS'
            import psycopg2
            from psycopg2 import sql

            def database(name):
                if name not in {'postgres', source, target}:
                    raise RuntimeError('Unexpected disposable database identity')
                return psycopg2.connect(host='127.0.0.1', port=port, user=user, password=password,
                                        dbname=name, sslmode='verify-full', sslrootcert=str(certificate), connect_timeout=5)

            stage = 'create_synthetic_databases'
            admin = database('postgres')
            try:
                admin.autocommit = True
                with admin.cursor() as cursor:
                    cursor.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
                    if cursor.fetchone() != (True,):
                        raise RuntimeError('The synthetic connection must use TLS')
                    for name in (source, target):
                        cursor.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0 ENCODING \'UTF8\'').format(sql.Identifier(name)))
            finally:
                admin.close()
            checks['verified_tls_connection'] = 'PASS'
            stage = 'seed_six_fictitious_tables'
            source_connection = database(source)
            try:
                seed(source_connection)
                before = manifest(source_connection)
            finally:
                source_connection.close()
            checks['six_table_synthetic_source'] = 'PASS'
            source_media = runtime / 'media-not-included'
            source_media.mkdir()
            (source_media / 'synthetic-media-sentinel.txt').write_text(MARKER, encoding='utf-8')
            destination = runtime / 'fake-drive'
            destination.mkdir()
            fake_google = FakeGoogleTransport(destination, credentials)
            from django.conf import settings
            if settings.configured:
                raise RuntimeError('Run this harness in a fresh Python process')
            settings.configure(
                SECRET_KEY=secrets.token_urlsafe(64), INSTALLED_APPS=[], USE_TZ=True,
                DATABASES={'default': {'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1',
                          'PORT': str(port), 'NAME': source, 'USER': user, 'PASSWORD': password,
                          'OPTIONS': {'sslmode': 'verify-full', 'sslrootcert': str(certificate), 'connect_timeout': 5}}},
                MEDIA_ROOT=source_media, MEDIA_STORAGE_BACKEND='filesystem',
                DRIVE_BACKUP_ENABLED=True, DRIVE_BACKUP_CLIENT_ID=credentials['client_id'],
                DRIVE_BACKUP_CLIENT_SECRET=credentials['client_secret'],
                DRIVE_BACKUP_REFRESH_TOKEN=credentials['refresh_token'], DRIVE_BACKUP_PASSPHRASE=passphrase,
                DRIVE_BACKUP_PG_DUMP_BIN=str(tools['pg_dump']),
                DRIVE_BACKUP_PG_RESTORE_BIN=str(tools['pg_restore']), DRIVE_BACKUP_GPG_BIN=str(gpg),
            )
            import django
            django.setup()
            from apps.core import drive_backup as service
            from django.db import connections
            service_directories = []
            original_temporary_directory = tempfile.TemporaryDirectory

            def owned_directory(*args, **kwargs):
                kwargs['dir'] = runtime
                handle = original_temporary_directory(*args, **kwargs)
                service_directories.append(Path(handle.name))

                class OwnedDirectory:
                    name = handle.name

                    def __enter__(self):
                        return handle.__enter__()

                    def __exit__(self, *exc):
                        home = Path(handle.name) / 'gnupg'
                        try:
                            if home.is_dir():
                                command([gpgconf, '--homedir', home, '--kill', 'gpg-agent'], environment)
                        except Exception:
                            checks['owned_gpg_agent_cleanup'] = 'FAIL'
                        return handle.__exit__(*exc)

                return OwnedDirectory()

            guards.enter_context(patch.object(http.client, 'HTTPSConnection', side_effect=fake_google.connection))
            guards.enter_context(patch.object(service.tempfile, 'TemporaryDirectory', side_effect=owned_directory))
            stage = 'real_service_create_drive_backup'
            backup_result = service.create_drive_backup()
            if (fake_google.upload_count != 1 or not fake_google.stored or
                    backup_result.get('scope') != 'database_only' or backup_result.get('restore_verified') is not False):
                raise RuntimeError('Unexpected service backup result')
            if any(directory.exists() for directory in service_directories):
                raise RuntimeError('The service left temporary plaintext files')
            if set(destination.iterdir()) != {fake_google.stored} or fake_google.stored.suffix != '.gpg':
                raise RuntimeError('Only one encrypted object may enter the fake Drive')
            result['ciphertext_sha256'] = digest(fake_google.stored)
            result['ciphertext_size'] = fake_google.stored.stat().st_size
            if backup_result.get('sha256') != result['ciphertext_sha256']:
                raise RuntimeError('Fake-provider ciphertext checksum mismatch')
            checks.update(real_pg_dump_and_gpg_service='PASS', ciphertext_only_fake_upload='PASS',
                          service_temporary_files_removed='PASS', encrypted_transfer_integrity='PASS')
            recovery_ciphertext = fake_google.stored
            if bridge is not None:
                stage = 'wait_for_authorized_remote_download'
                request = publish_bridge_request(bridge, runtime, fake_google.stored)
                print('SYNTHETIC_CIPHERTEXT_READY', flush=True)
                recovery_ciphertext = wait_for_remote_download(bridge, request, timeout=remote_wait_seconds)
                checks['remote_download_sha256_match'] = 'PASS'
                result.update(real_google_drive_access=True, google_drive_access_executor='authorized_parent_bridge',
                              worker_external_google_access=False, remote_drive_upload='PASS via parent bridge',
                              remote_drive_download='PASS via parent bridge')
            stage = 'decrypt_retrieved_ciphertext'
            recovery_home = runtime / 'recovery-gnupg'
            recovery_home.mkdir(mode=0o700)
            crypto_env = environment | {'GNUPGHOME': str(recovery_home)}
            gpg_command = [gpg, '--no-options', '--batch', '--yes', '--no-symkey-cache',
                           '--pinentry-mode', 'loopback', '--passphrase-fd', '0', '--output', '-', '--decrypt']
            decrypted = command(gpg_command + [recovery_ciphertext], crypto_env,
                                input_bytes=(passphrase + '\n').encode()).stdout
            recovered = runtime / 'recovered'
            recovered.mkdir()
            with tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:') as archive:
                members = archive.getmembers()
                if (len(members) != 2 or {item.name for item in members} != {'database.dump', 'manifest.json'} or
                        any(not item.isfile() or item.issparse() or item.linkname or item.size > service.MAX_PAYLOAD_BYTES
                            for item in members)):
                    raise RuntimeError('Unexpected database-only archive inventory')
                for member in members:
                    with archive.extractfile(member) as stream:
                        (recovered / member.name).write_bytes(stream.read(service.MAX_PAYLOAD_BYTES + 1))
            metadata = json.loads((recovered / 'manifest.json').read_text(encoding='utf-8'))
            if (metadata.get('scope') != 'database_only' or metadata.get('media_included') is not False or
                    metadata.get('restore_verified') is not False or
                    metadata.get('database_sha256') != digest(recovered / 'database.dump')):
                raise RuntimeError('Unexpected authenticated database-only manifest')
            checks.update(real_ciphertext_decryption='PASS', exact_two_file_inventory='PASS',
                          manifest_database_checksum='PASS', media_explicitly_not_included='PASS')
            stage = 'negative_cryptographic_checks'
            wrong = command(gpg_command + [recovery_ciphertext], crypto_env,
                            input_bytes=(secrets.token_urlsafe(48) + '\n').encode(), check=False)
            if wrong.returncode == 0:
                raise RuntimeError('Wrong passphrase was accepted')
            tampered = runtime / 'tampered.gpg'
            content = bytearray(recovery_ciphertext.read_bytes())
            content[len(content) // 2] ^= 1
            tampered.write_bytes(content)
            bad = command(gpg_command + [tampered], crypto_env,
                          input_bytes=(passphrase + '\n').encode(), check=False)
            if bad.returncode == 0:
                raise RuntimeError('Tampered ciphertext was accepted')
            checks.update(wrong_passphrase_rejected='PASS', tampered_ciphertext_rejected='PASS')
            stage = 'restore_new_empty_synthetic_database'
            restored = database(target)
            try:
                with restored.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                    if cursor.fetchone() != (0,):
                        raise RuntimeError('Restoration target is not empty')
            finally:
                restored.close()
            restore_env = pg_env | {'PGDATABASE': target}
            command([tools['pg_restore'], '--no-password', '--dbname', target, '--exit-on-error',
                     '--single-transaction', '--no-owner', '--no-acl', recovered / 'database.dump'], restore_env)
            restored = database(target)
            try:
                after = manifest(restored)
            finally:
                restored.close()
            if before != after:
                raise RuntimeError('Restored tables/values/indexes/constraints/sequences differ')
            checks.update(real_pg_restore='PASS', all_table_values_match='PASS',
                          all_indexes_constraints_sequences_match='PASS')
            if bridge is not None:
                checks['remote_download_used_for_restoration'] = 'PASS'
            result['database_objects_compared'] = {key: len(value) for key, value in after.items()}
            result['synthetic_row_count'] = sum(item['count'] for item in after['tables'].values())
            result['native_postgresql_major'] = metadata.get('postgresql_major')
            result['postgresql_18_validation'] = 'PASS' if metadata.get('postgresql_major') == 18 else 'NOT RUN'
            checks['external_network_and_credentials_excluded'] = 'PASS'
            serialized = json.dumps(result, sort_keys=True)
            if any(value in serialized for value in (*secret_values, fake_google.access_token)):
                raise RuntimeError('Secret found in public proof')
            checks['no_secret_in_public_proof'] = 'PASS'
            result['status'] = 'PASS'
            connections.close_all()
        except Exception as error:
            result['status'] = 'FAIL'
            result['failed_stage'] = stage
            result['error_type'] = type(error).__name__
            if isinstance(error, NativeFailure):
                result['native_failure'] = {'tool': error.tool, 'returncode': error.returncode}
            if stage == 'start_tls_cluster':
                result['startup_diagnostics'] = startup_diagnostics(runtime)
            if hasattr(error, 'code') and re.fullmatch('[a-z_]+', str(error.code)):
                result['service_error_code'] = error.code
        finally:
            try:
                from django.conf import settings
                if settings.configured:
                    from django.db import connections
                    connections.close_all()
            except Exception:
                checks['django_connections_closed'] = 'FAIL'
            for home in runtime.glob('*gnupg'):
                if home.is_dir() and not home.is_symlink():
                    try:
                        command([gpgconf, '--homedir', home, '--kill', 'gpg-agent'], environment)
                    except Exception:
                        checks['owned_gpg_agent_cleanup'] = 'FAIL'
            if started or (runtime / 'cluster' / 'postmaster.pid').exists():
                try:
                    command([tools['pg_ctl'], '-D', runtime / 'cluster', '-m', 'immediate', '-w', 'stop'])
                    state = command([tools['pg_ctl'], '-D', runtime / 'cluster', 'status'], check=False)
                    if state.returncode != 3 or (runtime / 'cluster' / 'postmaster.pid').exists():
                        raise RuntimeError('The disposable cluster has not stopped')
                    checks['cluster_stopped_and_verified'] = 'PASS'
                except Exception:
                    checks['cluster_stopped_and_verified'] = 'FAIL'
            runtime.relative_to(base)
            resolved_runtime = runtime.resolve()
            resolved_runtime.relative_to(base)
            linked_runtime = runtime.is_symlink() or (hasattr(runtime, 'is_junction') and runtime.is_junction())
            if (runtime.name.startswith('p0a-drive-local-') and resolved_runtime == runtime and not linked_runtime and
                    checks.get('cluster_stopped_and_verified') != 'FAIL'):
                try:
                    shutil.rmtree(runtime)
                except OSError:
                    checks['temporary_runtime_removed'] = 'FAIL'
            checks['temporary_runtime_removed'] = 'PASS' if not runtime.exists() else 'FAIL'
            checks['no_plaintext_archive_left'] = checks['temporary_runtime_removed']
            if any(value == 'FAIL' for value in checks.values()):
                result['status'] = 'FAIL'
            result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True, help='Existing native PostgreSQL binary directory')
    parser.add_argument('--gpg-bin', required=True, help='Existing GPG executable, not a credential file')
    parser.add_argument('--openssl-bin', default='C:/Program Files/Git/usr/bin/openssl.exe' if os.name == 'nt' else shutil.which('openssl'),
                        help='Existing OpenSSL executable, for a disposable self-signed TLS certificate')
    parser.add_argument('--remote-bridge', help='New ROOT/tmp/p0a-drive-remote-bridge-<32hex> directory')
    parser.add_argument('--remote-wait-seconds', type=int, default=BRIDGE_MAX_WAIT_SECONDS,
                        help='Maximum parent-bridge wait, from 1 to 1200 seconds')
    args = parser.parse_args()
    if not 1 <= args.remote_wait_seconds <= BRIDGE_MAX_WAIT_SECONDS:
        parser.error('--remote-wait-seconds must be from 1 to 1200')
    result = run_validation(args.postgres_bin, args.gpg_bin, args.openssl_bin,
                            remote_bridge=args.remote_bridge, remote_wait_seconds=args.remote_wait_seconds)
    report = ROOT / 'tmp' / 'p0a-drive-native-validation' / 'validation.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.remote_bridge is not None:
        bridge = _bridge_directory(args.remote_bridge)
        write_bridge_json(bridge, 'native-result.json',
                          {'run_id': bridge.name[len(BRIDGE_PREFIX):], 'result': result})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('status') == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
