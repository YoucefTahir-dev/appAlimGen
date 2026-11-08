"""Opt-in Google Drive connection bound to one authenticated DB session.

No OAuth secret is sent to the browser. Pending authorization is consumed from a
separate encrypted django_session row, rather than relying on a racy session pop.
Disconnecting is local only: it neither revokes Google's grant nor deletes files.
"""
from dataclasses import dataclass, replace
from datetime import timedelta
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from . import drive_backup


PENDING_KEY = 'elamine_drive_oauth_pending'
CONNECTION_KEY = 'elamine_drive_oauth_connection'
CALLBACK_PATH = '/core/backups/drive/callback/'
PENDING_SECONDS = 10 * 60
CONNECTION_SECONDS = 8 * 60 * 60
AUTHORIZATION_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
ERRORS = {
    'disabled': 'La connexion Google Drive depuis l’ERP n’est pas activée.',
    'configuration': 'La configuration sécurisée de la connexion Google Drive est incomplète ou invalide.',
    'session': 'Cette connexion nécessite une session utilisateur active stockée dans la base de données.',
    'state': 'Cette demande de connexion est invalide, expirée ou a déjà été utilisée. Recommencez depuis la page Sauvegardes.',
    'exchange': 'Google Drive n’a pas confirmé la connexion. Recommencez le consentement depuis la page Sauvegardes.',
    'identity': 'L’identité du compte Google Drive n’a pas pu être vérifiée.',
    'connection': 'Connectez à nouveau votre compte Google Drive dans cette session avant de lancer une archive.',
}


class DriveOAuthError(RuntimeError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in ERRORS else 'exchange'
        super().__init__(ERRORS[self.code])


def _setting(name, default=''):
    return getattr(settings, name, os.environ.get(name, default))


def enabled():
    value = _setting('DRIVE_BACKUP_OAUTH_ENABLED', False)
    return value is True or (isinstance(value, str) and value.strip().lower() in {'1', 'true'})


@dataclass(frozen=True, repr=False)
class _Setup:
    base: object
    redirect_uri: str
    cipher: object


@sensitive_variables()
def _configuration():
    if not enabled():
        raise DriveOAuthError('disabled')
    try:
        base = drive_backup.Config.from_settings(require_refresh=False)
        if not base.enabled:
            raise DriveOAuthError('configuration')
        uri = _setting('DRIVE_BACKUP_OAUTH_REDIRECT_URI', '')
        if (not isinstance(uri, str) or not 1 <= len(uri) <= 2048 or
                uri != uri.strip() or any(ord(c) < 33 for c in uri)):
            raise DriveOAuthError('configuration')
        parts = urlsplit(uri)
        port = parts.port  # Validate malformed ports also for loopback HTTP.
        if (not parts.hostname or parts.username is not None or parts.password is not None or
                parts.path != CALLBACK_PATH or parts.query or parts.fragment):
            raise DriveOAuthError('configuration')
        if parts.scheme == 'https':
            if port not in (None, 443):
                raise DriveOAuthError('configuration')
        elif not (parts.scheme == 'http' and (
                (settings.DEBUG and parts.hostname in {'localhost', '127.0.0.1', '::1'}) or
                (drive_backup.connection_test_only() and
                 uri in {'http://127.0.0.1:8000' + CALLBACK_PATH,
                         'http://127.0.0.1:8001' + CALLBACK_PATH}))):
            raise DriveOAuthError('configuration')
        key = _setting('DRIVE_BACKUP_TOKEN_KEY', '')
        if (not isinstance(key, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}=', key) is None or
                key in {settings.SECRET_KEY, base.passphrase}):
            raise DriveOAuthError('configuration')
        # Optional dependency: importing this module must work in legacy installs.
        from cryptography.fernet import Fernet
        cipher = Fernet(key.encode('ascii'))
        return _Setup(base, uri, cipher)
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError('configuration') from None


@sensitive_variables()
def _binding(request, *, create=False):
    if (settings.SESSION_ENGINE != 'django.contrib.sessions.backends.db' or
            not isinstance(getattr(request, 'session', None), SessionStore)):
        raise DriveOAuthError('session')
    user = getattr(request, 'user', None)
    if (not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False) or
            getattr(user, 'pk', None) is None):
        raise DriveOAuthError('session')
    if not request.session.session_key and create:
        request.session.save()
    session_key = request.session.session_key
    if not isinstance(session_key, str) or not session_key:
        raise DriveOAuthError('session')
    try:
        auth_hash = user.get_session_auth_hash()
    except Exception:
        raise DriveOAuthError('session') from None
    if not isinstance(auth_hash, str) or not auth_hash:
        raise DriveOAuthError('session')
    return {
        'user_id': str(user.pk),
        'session_hash': hashlib.sha256(session_key.encode()).hexdigest(),
        'auth_hash': auth_hash,
    }


def _same_binding(payload, binding):
    return all(
        isinstance(payload.get(key), str) and hmac.compare_digest(payload[key], value)
        for key, value in binding.items()
    )


@sensitive_variables()
def _vault_owner(binding):
    # Ownership remains verifiable when the independent Fernet key is rotated.
    raw = json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(settings.SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()


def _vault_id(value):
    return isinstance(value, str) and re.fullmatch(r'drivec_[0-9a-f]{32}', value) is not None


@sensitive_variables()
def _destination_token(identifier, binding):
    """Bind an archive confirmation to the exact account connection displayed."""
    raw = json.dumps({'vault': identifier, 'binding': binding}, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(settings.SECRET_KEY.encode(), b'elamine-drive-destination-v1\0' + raw,
                    hashlib.sha256).hexdigest()


@sensitive_variables()
def _vault_envelope(row, binding):
    try:
        if not isinstance(row.session_data, str) or len(row.session_data) > 20000:
            raise DriveOAuthError('connection')
        envelope = json.loads(row.session_data)
        if (not isinstance(envelope, dict) or set(envelope) != {'owner', 'cipher'} or
                not isinstance(envelope['owner'], str) or
                not hmac.compare_digest(envelope['owner'], _vault_owner(binding)) or
                not isinstance(envelope['cipher'], str)):
            raise DriveOAuthError('connection')
        return envelope
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError('connection') from None


@sensitive_variables()
def _discard_connection(request, binding):
    identifier = request.session.pop(CONNECTION_KEY, None)
    if not _vault_id(identifier):
        return
    try:
        with transaction.atomic():
            row = Session.objects.select_for_update().filter(session_key=identifier).first()
            if row is not None:
                try:
                    _vault_envelope(row, binding)
                except DriveOAuthError:
                    return
                row.delete()
    except Exception:
        raise DriveOAuthError('session') from None


@sensitive_variables()
def _seal(setup, payload):
    try:
        raw = json.dumps(payload, separators=(',', ':'), sort_keys=True, allow_nan=False).encode('utf-8')
        if len(raw) > 12000:
            raise DriveOAuthError('configuration')
        return setup.cipher.encrypt(raw).decode('ascii')
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError('configuration') from None


@sensitive_variables()
def _open(setup, value, code):
    if (not isinstance(value, str) or not 1 <= len(value) <= 18000 or
            re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value) is None):
        raise DriveOAuthError(code)
    try:
        raw = setup.cipher.decrypt(value.encode('ascii'))
        if len(raw) > 12000:
            raise DriveOAuthError(code)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DriveOAuthError(code)
        return payload
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError(code) from None


def _now():
    return int(timezone.now().timestamp())


def _valid_common(payload, setup, binding, kind, age):
    issued, expires = payload.get('issued_at'), payload.get('expires_at')
    return (
        payload.get('kind') == kind and _same_binding(payload, binding) and
        payload.get('client_id') == setup.base.client_id and
        type(issued) is int and type(expires) is int and
        0 <= issued <= _now() + 60 and issued < expires <= issued + age and _now() < expires
    )


@sensitive_variables()
def _connection(request, setup, binding):
    identifier = request.session.get(CONNECTION_KEY)
    if not _vault_id(identifier):
        raise DriveOAuthError('connection')
    try:
        row = Session.objects.filter(session_key=identifier).first()
        if row is None or row.expire_date <= timezone.now():
            raise DriveOAuthError('connection')
        envelope = _vault_envelope(row, binding)
        payload = _open(setup, envelope['cipher'], 'connection')
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError('connection') from None
    if not _valid_common(payload, setup, binding, 'drive-connection-v1', CONNECTION_SECONDS):
        raise DriveOAuthError('connection')
    if not _token(payload.get('refresh_token')) or not _valid_account(payload.get('account')):
        raise DriveOAuthError('connection')
    return payload


def _token(value):
    return (isinstance(value, str) and 20 <= len(value) <= 4096 and
            re.fullmatch(r'[A-Za-z0-9._~+/-]{20,4096}=*', value) is not None)


def _valid_account(account):
    if not isinstance(account, dict) or set(account) != {'email', 'display_name', 'permission_id'}:
        return False
    email, name, permission = account['email'], account['display_name'], account['permission_id']
    return (
        isinstance(email, str) and 3 <= len(email) <= 254 and email.count('@') == 1 and
        all(c.isprintable() and not c.isspace() for c in email) and
        isinstance(name, str) and 1 <= len(name) <= 254 and all(c.isprintable() for c in name) and
        isinstance(permission, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', permission) is not None
    )


@sensitive_variables()
def status(request):
    result = {'mode_enabled': enabled(), 'available': False, 'connected': False, 'account': None, 'issues': []}
    if not result['mode_enabled']:
        return result
    try:
        setup = _configuration()
        binding = _binding(request)
        result['available'] = True
        if request.session.get(CONNECTION_KEY):
            payload = _connection(request, setup, binding)
            result['connected'] = True
            result['account'] = {key: payload['account'][key] for key in ('email', 'display_name')}
            result['destination_token'] = _destination_token(request.session[CONNECTION_KEY], binding)
    except DriveOAuthError as error:
        result['issues'] = [ERRORS[error.code]]
    return result


def _pending_id(value):
    return isinstance(value, str) and re.fullmatch(r'drive_[0-9a-f]{32}', value) is not None


@sensitive_variables()
def _discard_pending(request, setup, binding):
    identifier = request.session.pop(PENDING_KEY, None)
    if not _pending_id(identifier):
        return
    with transaction.atomic():
        row = Session.objects.select_for_update().filter(session_key=identifier).first()
        if row is None:
            return
        try:
            payload = _open(setup, row.session_data, 'state')
        except DriveOAuthError:
            return  # Key rotation: the inaccessible pending row expires independently.
        if _same_binding(payload, binding):
            row.delete()


@sensitive_variables()
def begin(request):
    setup = _configuration()
    binding = _binding(request, create=True)
    _discard_pending(request, setup, binding)
    # Starting a new account selection must not silently retain the old account.
    _discard_connection(request, binding)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    issued = _now()
    payload = binding | {
        'kind': 'drive-pending-v1', 'client_id': setup.base.client_id,
        'issued_at': issued, 'expires_at': issued + PENDING_SECONDS,
        'state': state, 'verifier': verifier,
    }
    identifier = 'drive_' + secrets.token_hex(16)
    Session.objects.create(
        session_key=identifier, session_data=_seal(setup, payload),
        expire_date=timezone.now() + timedelta(seconds=PENDING_SECONDS),
    )
    request.session[PENDING_KEY] = identifier
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode()
    return AUTHORIZATION_ENDPOINT + '?' + urlencode({
        'client_id': setup.base.client_id, 'redirect_uri': setup.redirect_uri,
        'response_type': 'code', 'scope': drive_backup.SCOPE,
        'access_type': 'offline', 'prompt': 'consent select_account',
        'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
        'include_granted_scopes': 'false',
    })


@sensitive_variables()
def _callback_values(request):
    query = request.GET
    allowed = {'state', 'code', 'error', 'error_description', 'error_uri', 'scope', 'authuser', 'prompt', 'hd', 'iss'}
    if (set(query) - allowed or any(len(query.getlist(key)) != 1 for key in query) or
            any(not isinstance(value, str) or len(value) > 4096 or
                any(ord(c) < 32 for c in value) for value in query.values())):
        raise DriveOAuthError('state')
    state, code, error = query.get('state', ''), query.get('code', ''), query.get('error', '')
    if (re.fullmatch(r'[A-Za-z0-9_-]{43}', state) is None or
            bool(code) == bool(error) or
            ('iss' in query and query['iss'] != 'https://accounts.google.com') or
            (code and (not 10 <= len(code) <= 4096 or any(ord(c) > 126 or c.isspace() for c in code)))):
        raise DriveOAuthError('state')
    return state, code, error


@sensitive_variables()
def _consume_pending(request, setup, binding, state):
    identifier = request.session.get(PENDING_KEY)
    if not _pending_id(identifier):
        raise DriveOAuthError('state')
    try:
        with transaction.atomic(durable=True):
            row = Session.objects.select_for_update().filter(session_key=identifier).first()
            if row is None or row.expire_date <= timezone.now():
                raise DriveOAuthError('state')
            payload = _open(setup, row.session_data, 'state')
            if (not _valid_common(payload, setup, binding, 'drive-pending-v1', PENDING_SECONDS) or
                    not isinstance(payload.get('state'), str) or
                    not hmac.compare_digest(payload['state'], state) or
                    not isinstance(payload.get('verifier'), str) or
                    re.fullmatch(r'[A-Za-z0-9_-]{43,128}', payload['verifier']) is None):
                raise DriveOAuthError('state')
            # Commit deletion before any Google request. A stale authentication
            # session can resurrect only an opaque pointer, never this nonce row.
            # durable=True refuses a surrounding application transaction.
            row.delete()
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError('state') from None
    request.session.pop(PENDING_KEY, None)
    return payload


@sensitive_variables()
def complete(request):
    setup = _configuration()
    binding = _binding(request)
    state, code, error = _callback_values(request)
    pending = _consume_pending(request, setup, binding, state)
    if error:
        raise DriveOAuthError('exchange')
    try:
        client = drive_backup.DriveClient(setup.base, drive_backup.Deadline())
        result = client._request('POST', 'oauth2.googleapis.com', '/token', auth=False,
                                 content_type='application/x-www-form-urlencoded', body=urlencode({
                                     'client_id': setup.base.client_id, 'client_secret': setup.base.client_secret,
                                     'grant_type': 'authorization_code', 'code': code,
                                     'redirect_uri': setup.redirect_uri, 'code_verifier': pending['verifier'],
                                 }).encode())
        if (not isinstance(result, dict) or not _token(result.get('access_token')) or
                not _token(result.get('refresh_token')) or
                not isinstance(result.get('token_type'), str) or result['token_type'].lower() != 'bearer' or
                not isinstance(result.get('scope'), str) or result['scope'].split() != [drive_backup.SCOPE]):
            raise DriveOAuthError('exchange')
        client.token = result['access_token']
        identity = client._request('GET', 'www.googleapis.com', '/drive/v3/about?' + urlencode({
            'fields': 'user(emailAddress,displayName,permissionId)',
        }))
        user = identity.get('user') if isinstance(identity, dict) else None
        if not isinstance(user, dict):
            raise DriveOAuthError('identity')
        account = {
            'email': user.get('emailAddress'),
            'display_name': user.get('displayName') or user.get('emailAddress'),
            'permission_id': user.get('permissionId'),
        }
        if not _valid_account(account):
            raise DriveOAuthError('identity')
        issued = _now()
        age = request.session.get_expiry_age()
        if type(age) is not int or age <= 0:
            raise DriveOAuthError('session')
        payload = binding | {
            'kind': 'drive-connection-v1', 'client_id': setup.base.client_id,
            'issued_at': issued, 'expires_at': issued + min(age, CONNECTION_SECONDS),
            'refresh_token': result['refresh_token'], 'account': account,
        }
        identifier = 'drivec_' + secrets.token_hex(16)
        Session.objects.create(
            session_key=identifier,
            session_data=json.dumps({'owner': _vault_owner(binding), 'cipher': _seal(setup, payload)}, separators=(',', ':')),
            expire_date=timezone.now() + timedelta(seconds=min(age, CONNECTION_SECONDS)),
        )
        request.session[CONNECTION_KEY] = identifier
    except DriveOAuthError:
        raise
    except Exception:
        raise DriveOAuthError('exchange') from None


@sensitive_variables()
def disconnect(request):
    # No Google revocation: it could revoke the grant in other browser sessions.
    # No remote file deletion. Clearing tokens works even after key rotation.
    try:
        binding = _binding(request)
    except DriveOAuthError:
        request.session.pop(CONNECTION_KEY, None)
        request.session.pop(PENDING_KEY, None)
        return
    # A DB failure here must not be reported as a successful disconnection:
    # an older in-flight request could otherwise restore its opaque pointer.
    _discard_connection(request, binding)
    if not enabled():
        request.session.pop(PENDING_KEY, None)
        return
    try:
        setup = _configuration()
        _discard_pending(request, setup, binding)
    except DriveOAuthError:
        request.session.pop(PENDING_KEY, None)


@sensitive_variables()
def runtime_config(request, *, expected_destination=None):
    setup = _configuration()
    binding = _binding(request)
    payload = _connection(request, setup, binding)
    if expected_destination is not None:
        expected = _destination_token(request.session[CONNECTION_KEY], binding)
        if (not isinstance(expected_destination, str) or
                re.fullmatch(r'[0-9a-f]{64}', expected_destination) is None or
                not hmac.compare_digest(expected_destination, expected)):
            raise DriveOAuthError('connection')
    try:
        return replace(
            drive_backup.Config.from_settings(refresh_token=payload['refresh_token']),
            expected_permission_id=payload['account']['permission_id'],
        )
    except drive_backup.DriveBackupError:
        raise DriveOAuthError('configuration') from None
