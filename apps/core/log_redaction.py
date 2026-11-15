"""Reset-link privacy for log copies only; never alter a live request.

This module is importable before Django setup (Gunicorn's logger startup).
Routing, CSRF, session state and the actual reset token remain untouched.
"""
import logging
import re
from urllib.parse import unquote


# Match independently of a locale/prefix and include malformed trailing paths,
# queries and fragments. They are not useful audit data on a reset-link route.
_RESET_URL = re.compile(r'/reset/[^\s]+', re.IGNORECASE)
RESET_PATH_MARKER = '/reset/[redacted]/'
RESET_VALUE_MARKER = '[redacted-reset-log-value]'
ENCODED_VALUE_MARKER = '[redacted-recursively-encoded-log-value]'


def redact_reset_urls(value):
    """Mask the whole reset-link suffix, before a caller truncates the value.

    WSGI path atoms may already be decoded while RAW_URI/messages may not be.
    Decode only a log copy, with a conservative ceiling for nested encodings.
    Ordinary paths/messages are returned exactly as supplied.
    """
    text = str(value)
    decoded = text
    for _ in range(16):
        previous = decoded
        decoded = unquote(decoded, errors='replace')
        if decoded == previous:
            break
    else:
        # Excessive recursive encodings have no diagnostic value and must not
        # escape redaction by placing the route beyond our decoding ceiling.
        return ENCODED_VALUE_MARKER
    if '/reset/' in decoded.casefold() and (
        any((not char.isprintable()) or (char.isspace() and char != ' ') for char in decoded)
        or decoded.count(' ') > text.count(' ')
    ):
        # Decoding must neither introduce log separators nor split a malformed
        # token at newly decoded whitespace and leave its tail in the log.
        return RESET_VALUE_MARKER
    if not _RESET_URL.search(decoded):
        return text
    return _RESET_URL.sub(RESET_PATH_MARKER, decoded)


def _reset_path_values(context):
    """Find URL segment spellings so a bare token in an error is also masked."""
    values = set()
    decoded = str(context)
    for _ in range(16):
        for link in _RESET_URL.findall(decoded):
            link = link.split('?', 1)[0].split('#', 1)[0]
            for segment in re.split(r'[/\s?#&]+', link[len('/reset/'):]):
                values.add(segment)
                # Delimiters from a quoted log value or malformed URL must not
                # keep the same bare token visible in an exception message.
                values.update(re.findall(r'[A-Za-z0-9_-]+', segment))
        previous, decoded = decoded, unquote(decoded, errors='replace')
        if decoded == previous:
            break
    values = {value for value in values if len(value) >= 2 and value not in {'[redacted]', 'redacted'}}
    return tuple(sorted(values, key=len, reverse=True))


def _redact_known_values(value, secrets):
    text = redact_reset_urls(value)
    for secret in secrets:
        text = text.replace(secret, '[redacted]')
    return text


def redact_reset_context(value, *contexts):
    """Mask URL and bare path-derived token spellings in one diagnostic field."""
    if any(redact_reset_urls(item) in {RESET_VALUE_MARKER, ENCODED_VALUE_MARKER} for item in contexts):
        return RESET_VALUE_MARKER
    secrets = sorted({secret for item in contexts for secret in _reset_path_values(item)}, key=len, reverse=True)
    return _redact_known_values(value, secrets)


def _safe_request_copy(request, secrets):
    """Give Django's error handlers a minimal request, not secret-bearing state.

    A shallow copy would retain resolver kwargs, POST/body, cookies and other
    cached attributes. Create an independent HttpRequest instead. This is only
    called for a Django request once settings have already been initialized.
    """
    from django.http import HttpRequest

    safe = HttpRequest()
    safe.method = getattr(request, 'method', '')
    safe.path = redact_reset_urls(getattr(request, 'path', ''))
    safe.path_info = redact_reset_urls(getattr(request, 'path_info', safe.path))
    original_meta = getattr(request, 'META', {})
    safe.META = {
        key: _redact_known_values(original_meta[key], secrets)
        for key in ('REMOTE_ADDR', 'SERVER_NAME', 'SERVER_PORT')
        if key in original_meta
    }
    safe.META.update(REQUEST_METHOD=safe.method, PATH_INFO=safe.path_info)
    safe.META.setdefault('SERVER_NAME', 'redacted.invalid')
    safe.META.setdefault('SERVER_PORT', '80')
    safe.user = getattr(request, 'user', None)
    safe.resolver_match = None
    safe._body = b''
    safe.sensitive_post_parameters = '__ALL__'
    return safe


class ResetLinkLogFilter(logging.Filter):
    """Sanitize a LogRecord, not its live request or application state.

    Sensitive-route errors retain event/status/user and exception type, but
    intentionally omit traceback payloads: exception values/locals can contain
    the bare token even after the URL in the message has been masked.
    """
    def filter(self, record):
        if getattr(record, '_reset_link_log_sanitized', False):
            return True
        message = record.getMessage()
        request = getattr(record, 'request', None)
        path = getattr(request, 'path', '') if request is not None else ''
        path_fields = ('path', 'path_info', 'raw_uri', 'request_uri', 'url')
        contexts = [path, message, record.exc_text or '', record.stack_info or '']
        contexts.extend(getattr(record, key, '') for key in path_fields)
        sensitive = any(redact_reset_urls(value) != value for value in contexts)
        if not sensitive and record.exc_info:
            # A URL may occur only in the exception value, not the log message.
            error_text = str(record.exc_info[1])
            sensitive = redact_reset_urls(error_text) != error_text
            contexts.append(error_text)
        if sensitive:
            secrets = set(value for context in contexts for value in _reset_path_values(context))
            secrets = sorted(secrets, key=len, reverse=True)
            record.msg, record.args = _redact_known_values(message, secrets), ()
            if any(redact_reset_urls(value) in {RESET_VALUE_MARKER, ENCODED_VALUE_MARKER} for value in contexts):
                record.msg = 'Reset-link event [details withheld]'
                if hasattr(record, 'status_code'):
                    record.msg += f' status={record.status_code}'
            for key in path_fields:
                value = getattr(record, key, None)
                if isinstance(value, str):
                    setattr(record, key, _redact_known_values(value, secrets))
            if request is not None and hasattr(request, 'META'):
                record.request = _safe_request_copy(request, secrets)
            if record.exc_info:
                error_type = record.exc_info[0].__name__
                record.msg += f' [exception={error_type}; reset traceback withheld]'
                record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            if hasattr(record, 'message'):
                record.message = record.getMessage()
            record._reset_link_log_sanitized = True
        return True


class ResetSafeAtomsMixin:
    """Protect access-log atom values even if a local format includes RAW_URI.

    The original environ/HTTP request are never mutated. Explicit custom logger
    classes/handlers outside this configuration require their own qualification.
    """
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        contexts = [value for value in atoms.values() if isinstance(value, str)]
        sensitive = any(redact_reset_urls(value) != value for value in contexts)
        malformed = any(redact_reset_urls(value) in {RESET_VALUE_MARKER, ENCODED_VALUE_MARKER} for value in contexts)
        secrets = sorted({secret for value in contexts for secret in _reset_path_values(value)}, key=len, reverse=True)
        # These atoms are server-derived structure, not request URL/header
        # payloads. A UID/query value equal to "403" must never mask status.
        structural = {'s', 'm', 'H', 'h', 'l', 'p', 't', 'T', 'M', 'D', 'L', 'B', 'b'}
        return {
            key: (
                value if key in structural
                else '' if sensitive and key.lower() in {'q', '{query_string}e'}
                else RESET_VALUE_MARKER if malformed
                else _redact_known_values(value, secrets)
            ) if isinstance(value, str) else value
            for key, value in atoms.items()
        }
