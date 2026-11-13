"""Bounded, transactional replay protection for the commercial Web forms.

Web operations have their own registry namespace. Historical payment tokens may
be explicitly admitted by payment routes while they migrate to signed forms.
"""

from functools import wraps
import hashlib
import json
import re
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.api.models import IdempotencyRecord


WEB_TOKEN_MAX_AGE = 12 * 60 * 60
WEB_TOKEN_SALT = 'commerce.web-mutation.v1'
_NONCE = re.compile(r'^[0-9a-f]{32}$')
_TOKEN = re.compile(r'^[0-9a-f]{32}:[A-Za-z0-9]+:[A-Za-z0-9_-]+$')
_LEGACY_PAYMENT_TOKEN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_HASH_EXCLUDED_FIELDS = frozenset({'csrfmiddlewaretoken', 'request_token'})


def new_web_token():
    """Issue a short-lived form intention, not a replacement for CSRF/auth."""
    return TimestampSigner(salt=WEB_TOKEN_SALT).sign(uuid4().hex)


def is_legacy_payment_token(token):
    """Recognize bounded historical payment tokens, never malformed signatures."""
    return isinstance(token, str) and _LEGACY_PAYMENT_TOKEN.fullmatch(token) is not None


def _bad_token(*, expired=False):
    if expired:
        message = _(
            'Ce formulaire a expiré. Vérifiez si l’opération a déjà été '
            'enregistrée, puis rouvrez le formulaire avant de recommencer.'
        )
    else:
        message = _(
            'Le jeton du formulaire est invalide. Vérifiez si l’opération a '
            'déjà été enregistrée, puis rouvrez le formulaire.'
        )
    return HttpResponseBadRequest(message, content_type='text/plain; charset=utf-8')


def _request_hash(post):
    """Keep every submitted value, including duplicate non-token fields."""
    values = [
        (name, post.getlist(name))
        for name in sorted(post)
        if name not in _HASH_EXCLUDED_FIELDS
    ]
    raw = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _replay(request, record, digest, resource_type, replay_check, args, kwargs):
    if record.request_hash != digest:
        return HttpResponseBadRequest(
            _('Ce formulaire a déjà été utilisé avec un contenu différent. '
              'Vérifiez l’opération enregistrée, puis rouvrez le formulaire.'),
            content_type='text/plain; charset=utf-8',
        )
    if not record.completed:
        return HttpResponse(
            _('Une opération portant ce jeton est déjà en cours. '
              'Vérifiez son résultat avant de réessayer.'),
            status=409,
            content_type='text/plain; charset=utf-8',
        )
    if (
        not record.resource_id
        or not record.resource_id.isascii()
        or not record.resource_id.isdecimal()
        or int(record.resource_id) <= 0
    ):
        raise Http404
    body = record.response_body
    if (
        record.resource_type != resource_type
        or record.response_status not in {302, 303}
        or not isinstance(body, dict)
        or not isinstance(body.get('location'), str)
        or not body['location'].startswith('/')
        or body['location'].startswith('//')
    ):
        return HttpResponse(
            _('Le résultat de ce formulaire ne peut pas être repris. '
              'Vérifiez l’opération enregistrée avant de recommencer.'),
            status=409,
            content_type='text/plain; charset=utf-8',
        )
    # This checks current rights/scope, never current stock, totals or balance.
    replay_check(request, record, *args, **kwargs)
    response = HttpResponseRedirect(body['location'])
    response.status_code = record.response_status
    response['Idempotency-Replayed'] = 'true'
    request._web_idempotency_replayed = True
    return response


def web_idempotent(operation, resource_type, replay_check, *, allow_legacy_token=False):
    """Replay successful POST redirects only after a fresh access check.

    Permission decorators must surround this decorator. ``operation`` is a Web
    namespace string or a callable with the view's arguments. On success the
    view sets ``response._web_idempotency_resource_id`` to its saved resource.
    ``allow_legacy_token`` is opt-in for the historical payment-form migration;
    it must not relax signed-token checks on other commercial forms.
    """
    if not isinstance(resource_type, str) or not resource_type or len(resource_type) > 80:
        raise ImproperlyConfigured('A valid Web idempotency resource type is required.')
    if not callable(replay_check):
        raise ImproperlyConfigured('A Web idempotency replay access check is required.')

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if request.method != 'POST':
                return view_func(request, *args, **kwargs)
            tokens = request.POST.getlist('request_token')
            if len(tokens) > 1:
                return _bad_token()
            token = tokens[0] if tokens else ''
            if not token or not token.strip():
                # An already-open legacy form remains compatible. Only newly
                # rendered forms carry the transactional protection.
                return view_func(request, *args, **kwargs)
            if not (allow_legacy_token and is_legacy_payment_token(token)):
                if len(token) > 128 or not _TOKEN.fullmatch(token):
                    return _bad_token()
                try:
                    nonce = TimestampSigner(salt=WEB_TOKEN_SALT).unsign(
                        token, max_age=WEB_TOKEN_MAX_AGE,
                    )
                except SignatureExpired:
                    return _bad_token(expired=True)
                except BadSignature:
                    return _bad_token()
                if not _NONCE.fullmatch(nonce):
                    return _bad_token()

            operation_name = operation(request, *args, **kwargs) if callable(operation) else operation
            if (
                not isinstance(operation_name, str)
                or not operation_name.startswith('web.')
                or len(operation_name) > 120
            ):
                raise ImproperlyConfigured('A valid Web idempotency operation is required.')
            digest = _request_hash(request.POST)
            collision_error = None
            with transaction.atomic():
                try:
                    # Limit the caught IntegrityError to this reservation. A
                    # business integrity failure must roll back and propagate.
                    with transaction.atomic():
                        record = IdempotencyRecord.objects.create(
                            user=request.user, operation=operation_name, key=token,
                            request_hash=digest, resource_type=resource_type,
                        )
                except IntegrityError as exc:
                    collision_error = exc
                if collision_error is None:
                    response = view_func(request, *args, **kwargs)
                    if response.status_code not in {302, 303}:
                        transaction.set_rollback(True)
                        return response
                    resource_id = getattr(response, '_web_idempotency_resource_id', None)
                    resource_id = str(resource_id) if resource_id is not None else ''
                    if (
                        not resource_id
                        or len(resource_id) > 80
                        or not resource_id.isascii()
                        or not resource_id.isdecimal()
                        or int(resource_id) <= 0
                    ):
                        raise ImproperlyConfigured('A successful Web mutation must identify its resource.')
                    location = response['Location']
                    if not location.startswith('/') or location.startswith('//'):
                        raise ImproperlyConfigured('A successful Web mutation must use a local redirect.')
                    record.resource_id = resource_id
                    record.response_status = response.status_code
                    record.response_body = {'location': location}
                    record.completed = True
                    record.completed_at = timezone.now()
                    record.save(update_fields=(
                        'resource_id', 'response_status', 'response_body',
                        'completed', 'completed_at',
                    ))
                    # Adopting a verified historical payment is already a
                    # replay, even though its scoped registry entry is new.
                    response['Idempotency-Replayed'] = (
                        'true' if getattr(request, '_web_idempotency_replayed', False) else 'false'
                    )
                    return response

            # Leave the reservation transaction before reading the winner or
            # checking access. A concurrent insert waits for that winner first.
            try:
                record = IdempotencyRecord.objects.get(
                    user=request.user, operation=operation_name, key=token,
                )
            except IdempotencyRecord.DoesNotExist:
                raise collision_error
            return _replay(
                request, record, digest, resource_type, replay_check, args, kwargs,
            )

        return wrapped

    return decorator
