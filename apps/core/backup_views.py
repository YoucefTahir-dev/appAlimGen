import re
from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import has_permission, permission_required
from . import drive_backup, drive_oauth
from .backup_forms import BackupDriveForm, DriveConnectionForm
from .security import is_event_rate_limited, log_security_event


SESSION_RESULT_KEY = 'last_drive_backup_result'
GOOGLE_FORM_ORIGIN = 'https://accounts.google.com'


def _drive_form_response(response, *, form_page=False):
    """Allow the official OAuth form redirect on these responses only.

    SecurityHeadersMiddleware uses setdefault after the view, so a targeted
    policy here retains every configured non-form directive without changing
    the global policy or granting another page an external form destination.
    """
    headers = getattr(settings, 'SECURITY_RESPONSE_HEADERS', {})
    policy = response.get('Content-Security-Policy', headers.get('Content-Security-Policy', ''))
    policies = []
    for original in policy.split(','):
        directives, found = [], False
        for directive in original.split(';'):
            values = directive.split()
            if values and values[0].lower() == 'form-action':
                found = True
                # 'none' cannot be combined meaningfully with an allowed source.
                sources = [value for value in values[1:] if value != "'none'"]
                if GOOGLE_FORM_ORIGIN not in sources:
                    sources.append(GOOGLE_FORM_ORIGIN)
                leading = directive[:len(directive) - len(directive.lstrip())]
                directive = leading + values[0] + ' ' + ' '.join(sources)
            directives.append(directive)
        if not found:
            directives.append(" form-action 'self' " + GOOGLE_FORM_ORIGIN)
        policies.append(';'.join(directives))
    response['Content-Security-Policy'] = ','.join(policies)
    # A no-referrer form page can make navigation POSTs send Origin:null.
    # Callback/outbound redirects still suppress their referrer completely.
    response['Referrer-Policy'] = 'same-origin' if form_page else 'no-referrer'
    return response


def _safe_drive_callback(view):
    """Never put an OAuth query into login's next URL or a denied response."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if request.method != 'GET':
            response = HttpResponseNotAllowed(['GET'])
        elif not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
            response = redirect_to_login(reverse('backup_list'))
        else:
            try:
                response = view(request, *args, **kwargs)
            except PermissionDenied:
                response = HttpResponseForbidden(_('Connexion Google non autorisée.'))
        response['Referrer-Policy'] = 'no-referrer'
        add_never_cache_headers(response)
        return response
    return wrapped


def _public_result(result):
    """Retain only non-sensitive, bounded backup metadata in the session."""
    if not isinstance(result, dict):
        raise ValueError('Unexpected backup result.')
    name = result.get('name')
    digest = result.get('sha256')
    size = result.get('size')
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 160
        or not name.endswith('.gpg')
        or any(character in name for character in '/\\')
        or any(not character.isprintable() for character in name)
        or not isinstance(digest, str)
        or re.fullmatch(r'[0-9a-f]{64}', digest) is None
        or type(size) is not int
        or size <= 0
        or result.get('scope') != 'database_only'
        or result.get('restore_verified') is not False
    ):
        raise ValueError('Unexpected backup result.')
    return {
        'name': name,
        'sha256': digest,
        'size': size,
        'scope': 'database_only',
        'restore_verified': False,
    }


def _backup_state(request):
    oauth = drive_oauth.status(request)
    return oauth, {
        'enabled': oauth['available'], 'configured': oauth['available'],
        'ready': (oauth['mode_enabled'] and oauth['available'] and oauth['connected'] and
                  not drive_backup.connection_test_only()),
        'issues': oauth['issues'] or ([] if oauth['mode_enabled'] else [drive_oauth.ERRORS['disabled']]),
    }


def _render_backup_page(request, *, form=None, connection_form=None, error=None, status=200, last_backup=None, connection_notice=None):
    oauth, availability = _backup_state(request)
    response = render(request, 'core/backup_list.html', {
        'backup_availability': availability,
        'drive_connection': oauth,
        'connection_form': connection_form or DriveConnectionForm(user=request.user, prefix='drive'),
        'connection_notice': connection_notice,
        'can_create_backups': has_permission(request.user, 'accounts.create_backups'),
        'form': form or BackupDriveForm(user=request.user, initial={
            'drive_destination': oauth.get('destination_token', ''),
        }),
        'backup_error': error,
        'last_backup': last_backup,
    }, status=status)
    return _drive_form_response(response, form_page=True)


def _failed_backup_response(request, code=None):
    message = _("L’opération n’a pas abouti ou sa vérification a échoué. Aucun succès n’est confirmé. Contactez l’administrateur avant de réessayer.")
    if isinstance(code, str) and code in drive_backup.ERRORS:
        message = _(drive_backup.ERRORS[code])
    log_security_event(request, 'backup.drive.failed', level='error', status_code=503)
    return _render_backup_page(request, error=message, status=503)


@never_cache
@permission_required('accounts.view_backups')
@require_GET
def backup_list(request):
    notice = request.session.pop('drive_connection_notice', None)
    if notice not in {'connected', 'failed'}:
        notice = None
    stored_result = request.session.pop(SESSION_RESULT_KEY, None)
    try:
        last_backup = _public_result(stored_result) if stored_result is not None else None
    except ValueError:
        last_backup = None
    return _render_backup_page(request, last_backup=last_backup, connection_notice=notice)


@transaction.non_atomic_requests
@sensitive_post_parameters('current_password')
@never_cache
@permission_required('accounts.view_backups', 'accounts.create_backups')
@require_POST
def backup_create(request):
    request.session.pop(SESSION_RESULT_KEY, None)
    if is_event_rate_limited(request, 'backup.drive.request_rejected', 5, 15 * 60):
        log_security_event(request, 'backup.drive.throttled', level='warning', status_code=429)
        return _render_backup_page(
            request,
            error=_("Trop de confirmations incorrectes. Patientez quinze minutes avant de réessayer."),
            status=429,
        )
    form = BackupDriveForm(request.POST, user=request.user)
    if not form.is_valid():
        log_security_event(request, 'backup.drive.request_rejected', level='warning', status_code=400)
        return _render_backup_page(request, form=form, status=400)
    oauth, availability = _backup_state(request)
    if not availability.get('ready', False):
        log_security_event(request, 'backup.drive.unavailable', level='warning', status_code=503)
        return _render_backup_page(
            request,
            error=_("La sauvegarde Drive n’est pas disponible. Aucune sauvegarde n’a été lancée."),
            status=503,
        )

    log_security_event(request, 'backup.drive.started')
    try:
        # The Web interface never falls back to a globally configured Drive.
        config = drive_oauth.runtime_config(request, expected_destination=form.cleaned_data['drive_destination'])
        result = _public_result(drive_backup.create_drive_backup(config=config))
    except drive_oauth.DriveOAuthError:
        return _failed_backup_response(request, 'drive_auth')
    except drive_backup.DriveBackupError as error:
        # Only the service's fixed allowlist can supply an operational message.
        return _failed_backup_response(request, error.code)
    except Exception:
        # Neither provider exceptions nor subprocess diagnostics are safe for
        # the response, session or application audit trail.
        return _failed_backup_response(request)
    request.session[SESSION_RESULT_KEY] = result
    log_security_event(request, 'backup.drive.upload_verified')
    return redirect('backup_list')


def _connection_confirmation(request):
    if is_event_rate_limited(request, 'backup.drive.request_rejected', 5, 15 * 60):
        return _render_backup_page(request, error=_("Trop de confirmations incorrectes. Patientez quinze minutes."), status=429)
    form = DriveConnectionForm(request.POST, user=request.user, prefix='drive')
    if not form.is_valid():
        log_security_event(request, 'backup.drive.request_rejected', level='warning', status_code=400)
        return _render_backup_page(request, connection_form=form, status=400)
    return None


@sensitive_post_parameters('drive-current_password')
@never_cache
@permission_required('accounts.view_backups', 'accounts.create_backups')
@require_POST
def backup_drive_connect(request):
    rejected = _connection_confirmation(request)
    if rejected is not None:
        return rejected
    request.session.pop(SESSION_RESULT_KEY, None)
    request.session.pop('drive_connection_notice', None)
    try:
        target = drive_oauth.begin(request)
    except Exception:
        return _render_backup_page(request, error=_("La connexion Google Drive n’est pas configurée ou doit être renouvelée."), status=503)
    log_security_event(request, 'backup.drive.oauth_started')
    response = redirect(target)
    return _drive_form_response(response)


@_safe_drive_callback
@transaction.non_atomic_requests
@never_cache
@permission_required('accounts.view_backups', 'accounts.create_backups')
@require_GET
def backup_drive_callback(request):
    # No raw OAuth error, authorization code or state reaches the page/audit.
    request.session.pop(SESSION_RESULT_KEY, None)
    try:
        drive_oauth.complete(request)
    except Exception:
        log_security_event(request, 'backup.drive.oauth_rejected', level='warning', status_code=400)
        request.session['drive_connection_notice'] = 'failed'
    else:
        log_security_event(request, 'backup.drive.oauth_connected')
        request.session['drive_connection_notice'] = 'connected'
    response = redirect('backup_list')
    response['Referrer-Policy'] = 'no-referrer'
    return response


@sensitive_post_parameters('drive-current_password')
@never_cache
@permission_required('accounts.view_backups', 'accounts.create_backups')
@require_POST
def backup_drive_disconnect(request):
    rejected = _connection_confirmation(request)
    if rejected is not None:
        return rejected
    request.session.pop(SESSION_RESULT_KEY, None)
    request.session.pop('drive_connection_notice', None)
    try:
        drive_oauth.disconnect(request)
    except Exception:
        return _render_backup_page(request, error=_("La déconnexion locale n’a pas pu être confirmée. Déconnectez-vous de l’ERP."), status=503)
    log_security_event(request, 'backup.drive.oauth_disconnected')
    return redirect('backup_list')
