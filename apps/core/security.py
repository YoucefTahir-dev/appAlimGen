import logging
import uuid
import warnings
from ipaddress import ip_address
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin
from django.utils.translation import gettext as _
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError


security_logger = logging.getLogger('security')

BLOCKED_UPLOAD_EXTENSIONS = {
    '.bat',
    '.cmd',
    '.com',
    '.exe',
    '.js',
    '.msi',
    '.php',
    '.ps1',
    '.sh',
    '.vbs',
}
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
ALLOWED_EXCEL_EXTENSIONS = {'.xlsx'}
ALLOWED_RECEIPT_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf'}
ALLOWED_IMAGE_MIME_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
ALLOWED_EXCEL_MIME_TYPES = {
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/octet-stream',
}
ALLOWED_RECEIPT_MIME_TYPES = ALLOWED_IMAGE_MIME_TYPES | {'application/pdf'}
MAX_XLSX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_XLSX_MEMBERS = 1000
LOGIN_FAILURE_EVENT = 'auth.login.failed'
ADMIN_LOGIN_FAILURE_EVENT = 'auth.admin_login.failed'
PASSWORD_RESET_EVENT = 'auth.password_reset.request'


def get_client_ip(request):
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
        # Render appends the address observed by its trusted edge proxy. Only
        # that right-most value is trusted: scanning earlier values would let a
        # client inject an arbitrary address and bypass IP-based throttling.
        forwarded_values = forwarded_for.split(',')
        if forwarded_values:
            try:
                return str(ip_address(forwarded_values[-1].strip()))
            except ValueError:
                pass

    try:
        return str(ip_address(request.META.get('REMOTE_ADDR', '').strip()))
    except ValueError:
        return None


def sanitize_log_value(value, max_length=80):
    text = ''.join(character if character.isprintable() else ' ' for character in str(value or ''))
    return ' '.join(text.split())[:max_length]


def rate_limit_action(event, identifier=''):
    identifier = sanitize_log_value(identifier)
    return f'{event} identifier={identifier}' if identifier else event


def is_rate_limited(request, action, limit, window_seconds):
    if limit <= 0 or window_seconds <= 0:
        return False

    from apps.core.models import AuditLog

    client_ip = get_client_ip(request)
    if client_ip is None:
        return False

    since = timezone.now() - timezone.timedelta(seconds=window_seconds)
    queryset = AuditLog.objects.filter(
        action=action,
        ip_address=client_ip,
        created_at__gte=since,
    ).order_by()
    return queryset.values('pk')[limit - 1:limit].exists()


def is_event_rate_limited(request, event, limit, window_seconds):
    """Limit an event for an IP, independently of attacker-controlled identifiers."""
    if limit <= 0 or window_seconds <= 0:
        return False

    from apps.core.models import AuditLog

    client_ip = get_client_ip(request)
    if client_ip is None:
        return False

    since = timezone.now() - timezone.timedelta(seconds=window_seconds)
    queryset = AuditLog.objects.filter(
        Q(action=event) | Q(action__startswith=f'{event} identifier='),
        ip_address=client_ip,
        created_at__gte=since,
    ).order_by()
    return queryset.values('pk')[limit - 1:limit].exists()


def is_authentication_rate_limited(request, event, identifier=''):
    """Apply both per-account and global per-IP authentication limits."""
    window_seconds = getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 900)
    return is_rate_limited(
        request,
        rate_limit_action(event, identifier),
        getattr(settings, 'LOGIN_FAILURE_LIMIT', 5),
        window_seconds,
    ) or is_event_rate_limited(
        request,
        event,
        getattr(settings, 'LOGIN_FAILURE_IP_LIMIT', 20),
        window_seconds,
    )


def log_security_event(request, action, user=None, level='info', status_code=None):
    current_user = user if user is not None else getattr(request, 'user', None)
    if not getattr(current_user, 'is_authenticated', False):
        current_user = None

    safe_action = sanitize_log_value(action, max_length=255)
    safe_path = sanitize_log_value(request.path, max_length=255)

    try:
        from apps.core.models import AuditLog

        AuditLog.objects.create(
            user=current_user,
            action=safe_action,
            level=level,
            ip_address=get_client_ip(request),
            path=safe_path,
            status_code=status_code,
        )
    except Exception:
        security_logger.exception('Unable to write security audit log')

    log_method = getattr(security_logger, level, security_logger.info)
    log_method('%s path=%s user=%s status=%s ip=%s', safe_action, safe_path, current_user, status_code, get_client_ip(request))


def _secure_upload_name(directory, filename):
    suffix = Path(filename).suffix.lower()
    return f'{directory}/{uuid.uuid4().hex}{suffix}'


def product_photo_upload_to(instance, filename):
    return _secure_upload_name('products', filename)


def company_logo_upload_to(instance, filename):
    return _secure_upload_name('logos', filename)


def expense_receipt_upload_to(instance, filename):
    return _secure_upload_name('expense-receipts', filename)


def _validate_upload(uploaded_file, allowed_extensions, allowed_mime_types, max_size):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix in BLOCKED_UPLOAD_EXTENSIONS or suffix not in allowed_extensions:
        raise ValidationError(_('Type de fichier non autorisé.'))

    if uploaded_file.size > max_size:
        max_mb = max_size // (1024 * 1024)
        raise ValidationError(
            _('Fichier trop volumineux. Taille maximale : %(max_mb)s Mo.')
            % {'max_mb': max_mb}
        )

    content_type = getattr(uploaded_file, 'content_type', '')
    if content_type and content_type not in allowed_mime_types:
        raise ValidationError(_('Type MIME non autorisé.'))


def _rewind(uploaded_file):
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass


def _validate_image_content(uploaded_file):
    _rewind(uploaded_file)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(uploaded_file)
            image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError(_('Le contenu du fichier image est invalide.')) from exc
    finally:
        _rewind(uploaded_file)


def _validate_xlsx_content(uploaded_file):
    _rewind(uploaded_file)
    try:
        with ZipFile(uploaded_file) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if '[Content_Types].xml' not in names or 'xl/workbook.xml' not in names:
                raise ValidationError(_('Le contenu du fichier Excel est invalide.'))
            if len(members) > MAX_XLSX_MEMBERS:
                raise ValidationError(_('Le fichier Excel contient trop d’éléments.'))
            if any(member.flag_bits & 0x1 for member in members):
                raise ValidationError(_('Les fichiers Excel chiffrés ne sont pas autorisés.'))
            if sum(member.file_size for member in members) > MAX_XLSX_UNCOMPRESSED_SIZE:
                raise ValidationError(_('Le fichier Excel décompressé est trop volumineux.'))
    except (BadZipFile, OSError, ValueError) as exc:
        raise ValidationError(_('Le contenu du fichier Excel est invalide.')) from exc
    finally:
        _rewind(uploaded_file)

    try:
        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
        workbook.close()
    except Exception as exc:
        raise ValidationError(_('Le contenu du fichier Excel est invalide.')) from exc
    finally:
        _rewind(uploaded_file)


def _validate_pdf_content(uploaded_file):
    _rewind(uploaded_file)
    try:
        header = uploaded_file.read(1024)
    finally:
        _rewind(uploaded_file)
    if not header.lstrip().startswith(b'%PDF-'):
        raise ValidationError(_('Le contenu du fichier PDF est invalide.'))


def validate_image_upload(uploaded_file):
    _validate_upload(
        uploaded_file,
        allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
        allowed_mime_types=ALLOWED_IMAGE_MIME_TYPES,
        max_size=getattr(settings, 'MAX_IMAGE_UPLOAD_SIZE', 5 * 1024 * 1024),
    )
    _validate_image_content(uploaded_file)


def validate_excel_upload(uploaded_file):
    _validate_upload(
        uploaded_file,
        allowed_extensions=ALLOWED_EXCEL_EXTENSIONS,
        allowed_mime_types=ALLOWED_EXCEL_MIME_TYPES,
        max_size=getattr(settings, 'MAX_EXCEL_UPLOAD_SIZE', 10 * 1024 * 1024),
    )
    _validate_xlsx_content(uploaded_file)


def validate_receipt_upload(uploaded_file):
    _validate_upload(
        uploaded_file,
        allowed_extensions=ALLOWED_RECEIPT_EXTENSIONS,
        allowed_mime_types=ALLOWED_RECEIPT_MIME_TYPES,
        max_size=getattr(settings, 'MAX_RECEIPT_UPLOAD_SIZE', 10 * 1024 * 1024),
    )
    if Path(uploaded_file.name).suffix.lower() == '.pdf':
        _validate_pdf_content(uploaded_file)
    else:
        _validate_image_content(uploaded_file)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        headers = getattr(settings, 'SECURITY_RESPONSE_HEADERS', {})
        for name, value in headers.items():
            response.setdefault(name, value)
        return response


class SessionIdleTimeoutMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return None

        timeout_seconds = getattr(settings, 'SESSION_IDLE_TIMEOUT_SECONDS', 0)
        if timeout_seconds <= 0:
            return None

        now = int(timezone.now().timestamp())
        last_activity = request.session.get('last_activity')
        if last_activity and now - int(last_activity) > timeout_seconds:
            log_security_event(request, _('Session expirée pour inactivité'), level='warning', status_code=440)
            logout(request)
            request.session.flush()
            return None

        request.session['last_activity'] = now
        return None


class ForcePasswordChangeMiddleware(MiddlewareMixin):
    allowed_url_names = {
        'logout',
        'password_change_done',
        'profile',
        'set_language',
    }

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False) or not getattr(user, 'force_password_change', False):
            return None
        if getattr(request.resolver_match, 'url_name', None) in self.allowed_url_names:
            return None
        return redirect('profile')


class SecurityAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_admin_login = request.method == 'POST' and request.path.rstrip('/') == '/admin/login'
        admin_action = None
        if is_admin_login:
            admin_action = rate_limit_action(ADMIN_LOGIN_FAILURE_EVENT, request.POST.get('username', ''))
            if is_authentication_rate_limited(
                request,
                ADMIN_LOGIN_FAILURE_EVENT,
                request.POST.get('username', ''),
            ):
                log_security_event(request, 'auth.admin_login.blocked', level='warning', status_code=429)
                response = HttpResponse(_('Trop de tentatives d’authentification.'), status=429)
                response['Retry-After'] = str(getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 900))
                return response

        response = self.get_response(request)
        if is_admin_login and response.status_code == 200:
            log_security_event(request, admin_action, level='warning', status_code=401)
        if response.status_code in {403, 404}:
            level = 'warning' if response.status_code == 403 else 'info'
            log_security_event(
                request,
                _('Erreur HTTP %(status)s') % {'status': response.status_code},
                level=level,
                status_code=response.status_code,
            )
        elif response.status_code >= 500:
            log_security_event(
                request,
                _('Erreur HTTP %(status)s') % {'status': response.status_code},
                level='error',
                status_code=response.status_code,
            )
        return response
