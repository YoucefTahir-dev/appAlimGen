"""Shared, fail-closed budgets for the public API authentication routes."""

import math
import unicodedata

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException
from rest_framework.settings import api_settings as rest_settings
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import UntypedToken

from apps.accounts.email_policy import normalize_user_email
from apps.core.auth_rate_limit import consume_limit, prune_expired_buckets
from apps.core.security import get_client_ip


class AuthenticationRateLimitUnavailable(APIException):
    status_code = 503
    business_code = 'AUTH_RATE_LIMIT_UNAVAILABLE'
    business_message = _('Le service d’authentification est temporairement indisponible.')
    default_detail = business_message


class SharedAuthRateThrottle(ScopedRateThrottle):
    """Atomically consume persistent IP and account quotas before authentication.

    Budgets include successful requests and are deliberately independent for
    login, refresh, reset requests and reset confirmations. The IP budget is
    committed before parsing untrusted data or creating an account bucket.
    Existing Web audit-log limits are not a substitute for these reservations.
    """

    operation_scopes = {
        'login': 'api.login',
        'refresh': 'api.refresh',
        'password_reset_request': 'api.password_reset.request',
        'password_reset_confirm': 'api.password_reset.confirm',
    }

    def get_rate(self):
        # Read the settings proxy, not SimpleRateThrottle.THROTTLE_RATES: the
        # latter is captured at import time and ignores later settings reloads.
        try:
            return rest_settings.DEFAULT_THROTTLE_RATES[self.scope]
        except (AttributeError, KeyError, TypeError) as exc:
            raise AuthenticationRateLimitUnavailable() from exc

    @staticmethod
    def _positive_setting(name, maximum=2_147_483_647):
        value = getattr(settings, name, None)
        if type(value) is not int or not 1 <= value <= maximum:
            raise AuthenticationRateLimitUnavailable()
        return value

    @staticmethod
    def _canonical_positive_integer(value):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            number = int(value)
        except (ValueError, TypeError, OverflowError):
            return None
        return str(number) if number > 0 else None

    @classmethod
    def _account_identifier(cls, operation, data):
        # Invalid shapes and fields remain the serializer's responsibility.
        # They have already consumed the unavoidable IP quota.
        if not hasattr(data, 'get'):
            return None
        if operation == 'login':
            username = data.get('username')
            # DRF CharField accepts numeric JSON values and coerces them to
            # strings. Match it so username=123 cannot bypass the account
            # budget for an existing user named "123".
            if not isinstance(username, bool) and isinstance(username, (str, int, float)):
                return unicodedata.normalize('NFKC', str(username).strip()) or None
            return None
        if operation == 'refresh':
            raw_token = data.get('refresh')
            if not isinstance(raw_token, str) or not raw_token:
                return None
            try:
                token = UntypedToken(raw_token.strip())
            except (TokenError, ValueError, TypeError, UnicodeError):
                return None
            if token.get(jwt_settings.TOKEN_TYPE_CLAIM) != 'refresh':
                return None
            return cls._canonical_positive_integer(token.get(jwt_settings.USER_ID_CLAIM))
        if operation == 'password_reset_request':
            email = data.get('email')
            if isinstance(email, str):
                return normalize_user_email(email) or None
            return None
        if operation == 'password_reset_confirm':
            uid = data.get('uid')
            if not isinstance(uid, str) or not uid:
                return None
            try:
                decoded = urlsafe_base64_decode(uid.strip()).decode('utf-8')
            except (ValueError, TypeError, OverflowError, UnicodeError):
                return None
            return cls._canonical_positive_integer(decoded)
        return None

    def _consume(self, scope, dimension, identifier, limit, window_seconds):
        decision = consume_limit(
            scope=scope,
            dimension=dimension,
            identifier=identifier,
            limit=limit,
            window_seconds=window_seconds,
        )
        if not decision.allowed:
            self.retry_after = max(1, math.ceil(decision.retry_after))
        return decision.allowed

    def allow_request(self, request, view):
        self.retry_after = None
        if request.method != 'POST':
            return True

        self.scope = getattr(view, self.scope_attr, None)
        operation = getattr(view, 'auth_rate_limit_operation', None)
        scope = self.operation_scopes.get(operation)
        if scope is None:
            raise AuthenticationRateLimitUnavailable()
        try:
            rate = self.get_rate()
            if not isinstance(rate, str) or not rate:
                raise AuthenticationRateLimitUnavailable()
            self.num_requests, self.duration = self.parse_rate(rate)
            if not 1 <= self.num_requests <= 2_147_483_647 or not 1 <= self.duration <= 31_536_000:
                raise AuthenticationRateLimitUnavailable()
        except (ImproperlyConfigured, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise AuthenticationRateLimitUnavailable() from exc

        reset_limits = None
        if operation == 'password_reset_request':
            reset_limits = (
                self._positive_setting('PASSWORD_RESET_LIMIT'),
                self._positive_setting('PASSWORD_RESET_IP_LIMIT'),
                self._positive_setting('PASSWORD_RESET_WINDOW_SECONDS', maximum=31_536_000),
            )

        try:
            prune_expired_buckets(limit=100)
            client_ip = get_client_ip(request) or 'unknown'
            if not self._consume(scope, 'ip', client_ip, self.num_requests, self.duration):
                return False
            if reset_limits is not None:
                account_limit, ip_limit, window = reset_limits
                if not self._consume(scope, 'ip_legacy', client_ip, ip_limit, window):
                    return False
            else:
                account_limit, window = self.num_requests, self.duration

            identifier = self._account_identifier(operation, request.data)
            if identifier is not None:
                return self._consume(scope, 'account', identifier, account_limit, window)
        except DatabaseError as exc:
            raise AuthenticationRateLimitUnavailable() from exc
        return True

    def wait(self):
        return self.retry_after
