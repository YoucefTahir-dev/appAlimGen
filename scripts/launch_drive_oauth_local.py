"""Loopback-only, disposable Google OAuth connection test. Never a backup server.

Reads one operator-selected Web-client JSON outside Git, without copying or
printing its values. Only an explicit browser consent can contact Google. This
is not the ERP server and does not load its settings, .env, users or database.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://127.0.0.1:8000'
PAGE = '/core/backups/'
CALLBACK = PAGE + 'drive/callback/'
PROJECT = 'el-amine-drive-validation'
LIFETIME_SECONDS = 20 * 60
ABOUT_FIELDS = 'user(emailAddress,displayName,permissionId)'


class LocalTestError(RuntimeError):
    """Only fixed public error codes may escape the launcher."""


@dataclass(frozen=True, repr=False)
class Client:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    downloaded_callback_present: bool = False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocalTestError('invalid_client_file')
        result[key] = value
    return result


def read_client(path):
    """Do not trust endpoint URLs in the downloaded JSON."""
    try:
        path = Path(path)
        if path.is_symlink() or path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise LocalTestError('client_file_must_be_outside_repository')
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if not 0 < len(raw) <= 65536:
            raise LocalTestError('invalid_client_file')
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
        web = data.get('web') if isinstance(data, dict) else None
        if not isinstance(web, dict) or web.get('project_id') != PROJECT:
            raise LocalTestError('wrong_project_or_client_type')
        identifier, secret = web.get('client_id'), web.get('client_secret')
        if (not isinstance(identifier, str) or
                re.fullmatch(r'[A-Za-z0-9_-]{1,256}\.apps\.googleusercontent\.com', identifier) is None or
                not isinstance(secret, str) or not 12 <= len(secret) <= 4096 or
                secret != secret.strip() or any(ord(c) < 33 or ord(c) > 126 for c in secret)):
            raise LocalTestError('invalid_client_file')
        redirects = web.get('redirect_uris', [])
        if not isinstance(redirects, list) or any(not isinstance(uri, str) for uri in redirects):
            raise LocalTestError('invalid_client_file')
        return Client(identifier, secret, ORIGIN + CALLBACK in redirects)
    except LocalTestError:
        raise
    except Exception:
        raise LocalTestError('unreadable_or_invalid_client_file') from None


def permitted_google_call(method, host, path, kwargs):
    """Exact protocol allowlist: no file access, upload, folder, refresh or revoke."""
    if (method == 'POST' and host == 'oauth2.googleapis.com' and path == '/token' and
            set(kwargs) == {'auth', 'content_type', 'body'} and kwargs['auth'] is False and
            kwargs['content_type'] == 'application/x-www-form-urlencoded' and
            isinstance(kwargs['body'], bytes) and len(kwargs['body']) <= 20000):
        try:
            values = parse_qs(kwargs['body'].decode('ascii'), strict_parsing=True, keep_blank_values=True)
            return (set(values) == {'client_id', 'client_secret', 'grant_type', 'code',
                                    'redirect_uri', 'code_verifier'} and
                    all(len(value) == 1 and bool(value[0]) for value in values.values()) and
                    values['grant_type'] == ['authorization_code'] and
                    values['redirect_uri'] == [ORIGIN + CALLBACK])
        except Exception:
            return False
    if method == 'GET' and host == 'www.googleapis.com' and not kwargs:
        parts = urlsplit(path)
        return (parts.path == '/drive/v3/about' and not parts.scheme and not parts.netloc and
                not parts.fragment and parse_qs(parts.query, keep_blank_values=True) == {'fields': [ABOUT_FIELDS]})
    return False


def forbidden_operation(*_args, **_kwargs):
    raise LocalTestError('operation_forbidden_in_connection_test')


def sandbox_settings(directory, client, token_key):
    return dict(
        DEBUG=False, SECRET_KEY=secrets.token_urlsafe(64), USE_TZ=True, USE_I18N=False,
        ALLOWED_HOSTS=['127.0.0.1'], ROOT_URLCONF=__name__, LOGGING_CONFIG=None,
        INSTALLED_APPS=['django.contrib.sessions'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3',
                               'NAME': str(directory / 'sessions.sqlite3')}},
        SESSION_ENGINE='django.contrib.sessions.backends.db',
        SESSION_COOKIE_NAME='elamine_drive_connection_test_' + str(urlsplit(ORIGIN).port),
        SESSION_COOKIE_PATH=PAGE, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_AGE=LIFETIME_SECONDS, CSRF_USE_SESSIONS=True,
        SECURE_SSL_REDIRECT=False, SECURE_CONTENT_TYPE_NOSNIFF=True,
        SECURE_REFERRER_POLICY='no-referrer', DATA_UPLOAD_MAX_MEMORY_SIZE=8192,
        MIDDLEWARE=['django.middleware.security.SecurityMiddleware',
                    'django.contrib.sessions.middleware.SessionMiddleware',
                    'django.middleware.csrf.CsrfViewMiddleware'],
        DRIVE_BACKUP_ENABLED=True, DRIVE_BACKUP_OAUTH_ENABLED=True,
        DRIVE_BACKUP_LOCAL_CONNECTION_TEST=True,
        DRIVE_BACKUP_CLIENT_ID=client.client_id, DRIVE_BACKUP_CLIENT_SECRET=client.client_secret,
        DRIVE_BACKUP_OAUTH_REDIRECT_URI=ORIGIN + CALLBACK,
        DRIVE_BACKUP_TOKEN_KEY=token_key,
        # Required by the shared config validator, but no archive can be created.
        # Ephemeral and deliberately not usable as a recovery passphrase.
        DRIVE_BACKUP_PASSPHRASE=secrets.token_urlsafe(64), DRIVE_BACKUP_REFRESH_TOKEN='',
        DRIVE_BACKUP_PG_DUMP_BIN='', DRIVE_BACKUP_PG_RESTORE_BIN='', DRIVE_BACKUP_GPG_BIN='',
        PG_DUMP_BIN='', PG_RESTORE_BIN='', GPG_BIN='',
    )


def make_application(counters, *, synthetic_test=None):
    """Uses the real OAuth connector with a synthetic browser-bound identity."""
    from django.conf import settings
    from django.core.wsgi import get_wsgi_application
    from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
    from django.middleware.csrf import get_token
    from django.urls import clear_url_caches, path
    from django.utils.html import escape
    from django.views.decorators.http import require_GET, require_POST
    from apps.core import drive_oauth as oauth

    cleanup_only = synthetic_test is not None and getattr(synthetic_test, 'cleanup_only', False) is True
    test_action = 'synthetic-cleanup/' if cleanup_only else 'synthetic-roundtrip/'
    test_mode = ('connection_only' if synthetic_test is None else
                 'synthetic_cleanup' if cleanup_only else 'synthetic_roundtrip')

    # Fixed categories only: never retain an Origin value, URL, cookie or body.
    refusals = counters.setdefault('refused', {
        'request': 0, 'origin_null': 0, 'origin_missing': 0, 'origin_mismatch': 0,
    })

    class SyntheticIdentity:
        is_authenticated = True
        is_active = True

        def __init__(self, identifier):
            self.pk = identifier

        def get_session_auth_hash(self):
            return hmac.new(settings.SECRET_KEY.encode(), self.pk.encode(), hashlib.sha256).hexdigest()

    def bind(request, *, create=False):
        identifier = request.session.get('local_test_identity')
        if create and identifier is None:
            identifier = secrets.token_hex(16)
            request.session['local_test_identity'] = identifier
            request.session.save()
        if not isinstance(identifier, str) or re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
            raise LocalTestError('local_session_required')
        request.user = SyntheticIdentity(identifier)

    @require_GET
    def landing(request):
        bind(request, create=True)
        result = oauth.status(request)
        notice = request.session.pop('notice', '')
        token = escape(get_token(request))
        if result['connected']:
            account = result['account']
            message = 'Connexion Google confirmée : ' + str(escape(account['email']))
        else:
            message = ('Connexion non confirmée. Recommencez depuis cette page.' if notice == 'failed'
                       else 'Choisissez votre compte Google pour tester la connexion.')
        if not result['available']:
            message = 'Configuration locale indisponible. Aucun accès Google effectué.'
        body = '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Test local Drive</title>'
        if synthetic_test is None:
            body += '<h1>Test local : connexion Google uniquement</h1><p>' + message + '</p>'
            body += '<p>Ce serveur est indépendant de l’ERP. Aucune sauvegarde, aucun fichier ou média '
            body += 'ne peut être lu ou envoyé. Ce test ne valide pas une restauration.</p>'
        elif cleanup_only:
            body += '<h1>Test Drive : nettoyage du seul fichier fictif</h1><p>' + message + '</p>'
            body += '<p>Vérification et suppression uniquement du fichier du précédent essai, identifié '
            body += 'par son ID et ses marqueurs exacts. Aucun envoi, téléchargement, autre fichier, '
            body += 'média ou accès production.</p>'
            body += '<p>Résultat du nettoyage : ' + str(escape(synthetic_test.public_status())) + '</p>'
        else:
            body += '<h1>Test Drive : archive entièrement fictive</h1><p>' + message + '</p>'
            body += '<p>Un seul petit fichier .gpg issu de six tables fictives sera envoyé, récupéré, '
            body += 'restauré dans une base jetable, puis supprimé de Drive. Aucun média ni accès production.</p>'
            body += '<p>Résultat du test : ' + str(escape(synthetic_test.public_status())) + '</p>'
        if result['available']:
            body += '<form method="post" action="' + PAGE + 'drive/connect/">'
            body += '<input type="hidden" name="csrfmiddlewaretoken" value="' + str(token) + '">'
            body += '<button>Choisir mon compte Google et tester la connexion</button></form>'
        if result['connected']:
            if synthetic_test is not None and not synthetic_test.used:
                body += '<form method="post" action="' + PAGE + 'drive/' + test_action + '">'
                body += '<input type="hidden" name="csrfmiddlewaretoken" value="' + str(token) + '">'
                body += '<input type="hidden" name="destination" value="' + str(escape(result['destination_token'])) + '">'
                body += ('<button>Vérifier et supprimer uniquement le fichier fictif de l’essai précédent</button></form>'
                         if cleanup_only else '<button>Tester l’archive fictive chiffrée et sa restauration</button></form>')
            body += '<form method="post" action="' + PAGE + 'drive/disconnect/">'
            body += '<input type="hidden" name="csrfmiddlewaretoken" value="' + str(token) + '">'
            body += '<button>Effacer la connexion locale</button></form>'
        body += '<form method="post" action="' + PAGE + 'stop/">'
        body += '<input type="hidden" name="csrfmiddlewaretoken" value="' + str(token) + '">'
        body += '<button>Arrêter le test et nettoyer les sessions locales</button></form>'
        body += '<p>Arrêt automatique après vingt minutes. La suppression locale ne révoque pas '
        body += 'le consentement dans le compte Google.</p></html>'
        return HttpResponse(body)

    @require_POST
    def connect(request):
        bind(request)
        if set(request.POST) - {'csrfmiddlewaretoken'} or counters['begins'] >= 5:
            return HttpResponse('Demande refusée.', status=400)
        counters['begins'] += 1
        return HttpResponseRedirect(oauth.begin(request))

    @require_GET
    def callback(request):
        try:
            bind(request)
            oauth.complete(request)
        except Exception:
            counters['callbacks_failed'] += 1
            request.session['notice'] = 'failed'
        else:
            counters['connections_confirmed'] += 1
            request.session['notice'] = 'connected'
        return HttpResponseRedirect(PAGE)

    @require_POST
    def disconnect(request):
        bind(request)
        oauth.disconnect(request)
        return HttpResponseRedirect(PAGE)

    @require_POST
    def stop(request):
        bind(request)
        oauth.disconnect(request)
        counters['stop'] = True
        return HttpResponse('Test terminé. Nettoyage local en cours.')

    @require_POST
    def synthetic_roundtrip(request):
        bind(request)
        if synthetic_test is None or set(request.POST) - {'csrfmiddlewaretoken', 'destination'}:
            return HttpResponse('Demande refusée.', status=400)
        if synthetic_test.used:
            return HttpResponse('Cet essai a déjà été lancé.', status=409)
        try:
            config = oauth.runtime_config(request, expected_destination=request.POST.get('destination', ''))
        except oauth.DriveOAuthError:
            return HttpResponse('Reconnectez le compte et rechargez la page avant cet essai.', status=403)
        synthetic_test.run(config)
        return HttpResponseRedirect(PAGE)

    @require_GET
    def health(_request):
        # No account, token, callback parameters, settings or filesystem paths.
        return JsonResponse({'mode': test_mode,
                             'production_access': False,
                             **({'archive_operations': 0} if synthetic_test is None else {}),
                             'connections_confirmed': counters['connections_confirmed'],
                             'callbacks_failed': counters['callbacks_failed'], 'refused': refusals.copy(),
                             **({'synthetic_result': synthetic_test.public_result()} if synthetic_test is not None else {})})

    global urlpatterns
    urlpatterns = [path(PAGE.lstrip('/'), landing),
                   path((PAGE + 'drive/connect/').lstrip('/'), connect),
                   path(CALLBACK.lstrip('/'), callback),
                   path((PAGE + 'drive/disconnect/').lstrip('/'), disconnect),
                   path((PAGE + 'stop/').lstrip('/'), stop), path('healthz', health)]
    if synthetic_test is not None:
        urlpatterns.append(path((PAGE + 'drive/' + test_action).lstrip('/'), synthetic_roundtrip))
    clear_url_caches()
    django_app = get_wsgi_application()

    def application(environ, start_response):
        # no-referrer makes basic browser form POSTs send Origin:null (Fetch
        # standard). Preserve the origin only on our form page. The OAuth
        # callback and outbound redirect must still suppress their referrer.
        is_form_page = environ.get('PATH_INFO') == PAGE and environ.get('REQUEST_METHOD') == 'GET'
        policy = 'same-origin' if is_form_page else 'no-referrer'
        headers = [('Cache-Control', 'no-store'), ('Referrer-Policy', policy),
                   ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
                   ('Content-Security-Policy', "default-src 'none'; form-action 'self' https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'")]

        def safe_start(status, original, exc_info=None):
            names = {name.lower() for name, _value in headers}
            return start_response(status, [(name, value) for name, value in original
                                           if name.lower() not in names] + headers, exc_info)

        # Block DNS rebinding, proxying and unsolicited cross-origin POSTs.
        valid = (environ.get('HTTP_HOST') == urlsplit(ORIGIN).netloc and
                 environ.get('REMOTE_ADDR') == '127.0.0.1' and
                 not environ.get('HTTP_X_FORWARDED_HOST') and
                 not environ.get('HTTP_X_FORWARDED_PROTO'))
        rejection = 'request'
        if environ.get('REQUEST_METHOD') == 'POST':
            origin = environ.get('HTTP_ORIGIN')
            if valid and origin != ORIGIN:
                rejection = ('origin_null' if origin == 'null' else
                             'origin_missing' if not origin else 'origin_mismatch')
            valid = valid and origin == ORIGIN
        length = environ.get('CONTENT_LENGTH', '')
        if not valid or (length and (not length.isdigit() or int(length) > 8192)):
            refusals[rejection] += 1
            safe_start('400 Bad Request', [('Content-Type', 'text/plain; charset=utf-8')])
            return [b'Request refused.']
        try:
            return django_app(environ, safe_start)
        except Exception:
            safe_start('500 Internal Server Error', [('Content-Type', 'text/plain; charset=utf-8')])
            return [b'Local test failed. No success confirmed.']

    return application


def serve(client, *, synthetic_test=None):
    from cryptography.fernet import Fernet
    import django
    from django.conf import settings
    from django.core.management import call_command
    from django.core.servers.basehttp import WSGIServer, WSGIRequestHandler
    from django.db import connections
    from django.db.backends.base.base import BaseDatabaseWrapper
    from wsgiref.simple_server import make_server

    if settings.configured:
        raise LocalTestError('settings_already_configured')

    class QuietHandler(WSGIRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *_args):
            pass  # Never record callback query strings, cookies or request data.

        def get_stderr(self):
            class NullLog:
                def write(self, value):
                    return len(value)

                def flush(self):
                    pass
            return NullLog()

    class QuietServer(WSGIServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, allow_reuse_address=False, **kwargs)

        def handle_error(self, *_args):
            pass  # No raw exception/traceback from a live OAuth request.

    with tempfile.TemporaryDirectory(prefix='elamine-oauth-connection-only-') as temporary:
        directory = Path(temporary).resolve()
        environment = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP')
                       if name in os.environ}
        counters = {'begins': 0, 'connections_confirmed': 0, 'callbacks_failed': 0,
                    'google_token_requests': 0, 'google_identity_requests': 0, 'stop': False}
        original_ensure = BaseDatabaseWrapper.ensure_connection
        original_connect = socket.create_connection

        def isolated_sqlite(wrapper):
            if (wrapper.vendor != 'sqlite' or
                    Path(str(wrapper.settings_dict['NAME'])).resolve() != directory / 'sessions.sqlite3'):
                raise LocalTestError('database_outside_sandbox')
            return original_ensure(wrapper)

        def google_socket(address, *args, **kwargs):
            if address not in {('oauth2.googleapis.com', 443), ('www.googleapis.com', 443)}:
                raise LocalTestError('network_outside_google_oauth')
            return original_connect(address, *args, **kwargs)

        old_logging_disable = logging.root.manager.disable
        try:
            logging.disable(logging.CRITICAL)
            with ExitStack() as guards:
                guards.enter_context(patch.dict(os.environ, environment, clear=True))
                guards.enter_context(patch.object(subprocess, 'Popen', forbidden_operation))
                guards.enter_context(patch.object(BaseDatabaseWrapper, 'ensure_connection', isolated_sqlite))
                guards.enter_context(patch('socket.create_connection', google_socket))
                settings.configure(**sandbox_settings(directory, client, Fernet.generate_key().decode('ascii')))
                django.setup()
                from apps.core import drive_backup
                original_request = drive_backup.DriveClient._request

                def google_request(instance, method, host, path, **kwargs):
                    if not permitted_google_call(method, host, path, kwargs):
                        raise LocalTestError('google_operation_outside_connection_test')
                    counter = 'google_token_requests' if method == 'POST' else 'google_identity_requests'
                    counters[counter] += 1
                    return original_request(instance, method, host, path, **kwargs)

                guards.enter_context(patch.object(drive_backup.DriveClient, '_request', google_request))
                for target, name in ((drive_backup, 'create_drive_backup'), (drive_backup, '_run'),
                                     (drive_backup.DriveClient, 'folder'), (drive_backup.DriveClient, 'upload')):
                    guards.enter_context(patch.object(target, name, forbidden_operation))
                call_command('migrate', 'sessions', interactive=False, verbosity=0)
                app = make_application(counters, synthetic_test=synthetic_test)
                # make_server binds once, without killing/reusing an ERP listener.
                with make_server('127.0.0.1', urlsplit(ORIGIN).port, app, QuietServer, QuietHandler) as server:
                    server.timeout = 1
                    print('LOCAL_OAUTH_READY ' + ORIGIN + PAGE, flush=True)
                    until = time.monotonic() + LIFETIME_SECONDS
                    while not counters['stop'] and time.monotonic() < until:
                        server.handle_request()
        finally:
            if synthetic_test is not None:
                synthetic_test.cancel_if_incomplete()
            connections.close_all()
            logging.disable(old_logging_disable)
    # TemporaryDirectory cleanup has completed before this fixed, PII-free summary.
    mode = ('connection_only' if synthetic_test is None else
            'synthetic_cleanup' if getattr(synthetic_test, 'cleanup_only', False) is True else 'synthetic_roundtrip')
    print(json.dumps({'mode': mode,
                      'connections_confirmed': counters['connections_confirmed'],
                      'google_token_requests': counters['google_token_requests'],
                      'google_identity_requests': counters['google_identity_requests'],
                      **({'archive_operations': 0} if synthetic_test is None else {}),
                      **({'synthetic_result': synthetic_test.public_result()} if synthetic_test is not None else {}),
                      'cleanup': 'PASS'}), flush=True)


def main(argv=None):
    global ORIGIN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', required=True)
    parser.add_argument('--port', type=int, choices=(8000, 8001), default=8000,
                        help='Explicit loopback test port; register its exact callback with Google.')
    parser.add_argument('--serve', action='store_true', help='Start the isolated 20-minute browser test.')
    args = parser.parse_args(argv)
    ORIGIN = 'http://127.0.0.1:' + str(args.port)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    optional = ROOT / 'tmp' / 'p0a-drive-oauth-dependencies-20261108'
    if optional.is_dir():
        sys.path.insert(0, str(optional))
    try:
        client = read_client(args.client_file)
        from cryptography.fernet import Fernet
        Fernet.generate_key()  # Dependency/import check only; no external requests.
        if args.serve:
            serve(client)
        else:
            print(json.dumps({'client_file': 'PASS', 'project': PROJECT,
                              'downloaded_callback_present': client.downloaded_callback_present,
                              'mode': 'check_only', 'network_requests': 0}))
        return 0
    except KeyboardInterrupt:
        print('LOCAL_OAUTH_STOPPED', flush=True)
        return 0
    except Exception:
        # No exception repr, path contents, JSON values, tokens or traceback.
        print('LOCAL_OAUTH_FAILED: configuration, dependency, port or local test unavailable.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
