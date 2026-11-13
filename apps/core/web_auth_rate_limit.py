"""Persistent admission budgets for Web authentication, independent of logs.

Every admitted POST, including a successful login, consumes a budget. Existing
configured ceilings are preserved; this deliberately changes the old failure-
only audit-log counters into exact admission limits shared between workers.
"""

import unicodedata

from django.conf import settings
from django.db import DatabaseError
from django.utils.http import urlsafe_base64_decode

from apps.accounts.email_policy import normalize_user_email

from .auth_rate_limit import Admission, consume_limit, prune_expired_buckets
from .security import get_client_ip


class WebAuthenticationUnavailable(RuntimeError):
    """Admission cannot be safely evaluated; never fall back to an allowance."""


_SCOPES = {
    'login': 'web.login',
    'admin_login': 'admin.login',
    'password_reset_request': 'web.password_reset.request',
    'admin_password_reset_request': 'admin.password_reset.request',
    'password_reset_confirm': 'web.password_reset.confirm',
}


def _positive_setting(name, *, maximum=2_147_483_647):
    value = getattr(settings, name, None)
    if type(value) is not int or not 1 <= value <= maximum:
        raise WebAuthenticationUnavailable()
    return value


def _identifier(request, operation, explicit_identifier):
    if operation in {'login', 'admin_login'}:
        # Match AuthenticationForm's strip and Django's username normalization,
        # without the truncation used only for human-readable audit messages.
        return unicodedata.normalize('NFKC', request.POST.get('username', '').strip()) or None
    if operation == 'password_reset_confirm':
        uid = request.resolver_match.kwargs.get('uidb64', '')
        try:
            user_id = int(urlsafe_base64_decode(uid).decode('utf-8'))
        except (ValueError, TypeError, OverflowError, UnicodeError):
            return None
        return str(user_id) if user_id > 0 else None
    email = explicit_identifier if explicit_identifier is not None else request.POST.get('email', '')
    return normalize_user_email(email) or None


def admit_web_authentication(request, operation, *, identifier=None):
    """Reserve IP first, then a global account quota, in short transactions.

    Unknown IPs share one fail-closed bucket. No account lookup, local cache,
    password check, email delivery, or untrusted POST parsing precedes IP
    admission. Quotas from Web, Django admin, and API use distinct namespaces.
    """
    if request.method != 'POST':
        return Admission(True)
    scope = _SCOPES.get(operation)
    if scope is None:
        raise WebAuthenticationUnavailable()
    prefix = 'LOGIN_FAILURE' if operation in {'login', 'admin_login'} else 'PASSWORD_RESET'
    account_limit = _positive_setting(f'{prefix}_LIMIT')
    ip_limit = _positive_setting(f'{prefix}_IP_LIMIT')
    window = _positive_setting(f'{prefix}_WINDOW_SECONDS', maximum=31_536_000)
    try:
        prune_expired_buckets(limit=100)
        ip_admission = consume_limit(
            scope=scope, dimension='ip', identifier=get_client_ip(request) or 'unknown',
            limit=ip_limit, window_seconds=window,
        )
        if not ip_admission.allowed:
            return ip_admission
        account_identifier = _identifier(request, operation, identifier)
        if account_identifier is not None:
            return consume_limit(
                scope=scope, dimension='account', identifier=account_identifier,
                limit=account_limit, window_seconds=window,
            )
    except DatabaseError as exc:
        raise WebAuthenticationUnavailable() from exc
    return Admission(True)
