import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin


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
ALLOWED_IMAGE_MIME_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
ALLOWED_EXCEL_MIME_TYPES = {
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/octet-stream',
}


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_security_event(request, action, user=None, level='info', status_code=None):
    current_user = user if user is not None else getattr(request, 'user', None)
    if not getattr(current_user, 'is_authenticated', False):
        current_user = None

    try:
        from apps.core.models import AuditLog

        AuditLog.objects.create(
            user=current_user,
            action=action[:255],
            level=level,
            ip_address=get_client_ip(request),
            path=request.path[:255],
            status_code=status_code,
        )
    except Exception:
        security_logger.exception('Unable to write security audit log')

    log_method = getattr(security_logger, level, security_logger.info)
    log_method('%s path=%s user=%s status=%s ip=%s', action, request.path, current_user, status_code, get_client_ip(request))


def _secure_upload_name(directory, filename):
    suffix = Path(filename).suffix.lower()
    return f'{directory}/{uuid.uuid4().hex}{suffix}'


def product_photo_upload_to(instance, filename):
    return _secure_upload_name('products', filename)


def company_logo_upload_to(instance, filename):
    return _secure_upload_name('logos', filename)


def _validate_upload(uploaded_file, allowed_extensions, allowed_mime_types, max_size):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix in BLOCKED_UPLOAD_EXTENSIONS or suffix not in allowed_extensions:
        raise ValidationError('Type de fichier non autorisé.')

    if uploaded_file.size > max_size:
        max_mb = max_size // (1024 * 1024)
        raise ValidationError(f'Fichier trop volumineux. Taille maximale : {max_mb} Mo.')

    content_type = getattr(uploaded_file, 'content_type', '')
    if content_type and content_type not in allowed_mime_types:
        raise ValidationError('Type MIME non autorisé.')


def validate_image_upload(uploaded_file):
    _validate_upload(
        uploaded_file,
        allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
        allowed_mime_types=ALLOWED_IMAGE_MIME_TYPES,
        max_size=getattr(settings, 'MAX_IMAGE_UPLOAD_SIZE', 5 * 1024 * 1024),
    )


def validate_excel_upload(uploaded_file):
    _validate_upload(
        uploaded_file,
        allowed_extensions=ALLOWED_EXCEL_EXTENSIONS,
        allowed_mime_types=ALLOWED_EXCEL_MIME_TYPES,
        max_size=getattr(settings, 'MAX_EXCEL_UPLOAD_SIZE', 10 * 1024 * 1024),
    )


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
            log_security_event(request, 'Session expirée pour inactivité', level='warning', status_code=440)
            logout(request)
            request.session.flush()
            return None

        request.session['last_activity'] = now
        return None


class SecurityAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code in {403, 404}:
            level = 'warning' if response.status_code == 403 else 'info'
            log_security_event(request, f'Erreur HTTP {response.status_code}', level=level, status_code=response.status_code)
        elif response.status_code >= 500:
            log_security_event(request, f'Erreur HTTP {response.status_code}', level='error', status_code=response.status_code)
        return response
