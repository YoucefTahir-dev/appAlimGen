"""Opt-in durable reset delivery. At-least-once, bounded retry, no background thread."""
import base64
import hashlib
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.accounts.email_policy import normalize_user_email
from apps.accounts.models import PasswordResetDispatch
from .password_reset import PasswordResetDeliveryError, PasswordResetService
from .reset_wakeup import notify_reset, wake_directory

logger = logging.getLogger('security')
MAX_ATTEMPTS = 3


@sensitive_variables()
def cipher():
    # Optional dependency: flag-off installations must remain importable.
    from cryptography.fernet import Fernet
    key = hashlib.sha256(b'password-reset-outbox-v1\x00' + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


@sensitive_variables()
def enqueue_reset(email):
    if not settings.PASSWORD_RESET_PUBLIC_BASE_URL:
        raise ImproperlyConfigured('Async reset requires a trusted public base URL.')
    now = timezone.now()
    try:
        # Colocated notification must precede the OUTERMOST commit. Public
        # endpoints are not ATOMIC_REQUESTS; refuse unsupported nesting rather
        # than risk an invisible commit after a discarded wake hint.
        with transaction.atomic(durable=wake_directory() is not None):
            item = PasswordResetDispatch.objects.create(
                encrypted_email=cipher().encrypt(normalize_user_email(email).encode()).decode('ascii'),
                expires_at=now + timedelta(minutes=10),
            )
            notify_reset(item.pk)
    except (DatabaseError, OSError, RuntimeError):
        logger.error('Password reset queue unavailable')
        raise PasswordResetDeliveryError from None


@sensitive_variables()
def process_one(*, service=None):
    """Claim by compare-and-swap; no DB transaction/lock held over mail I/O.

    A crashed worker's lease expires. Provider acceptance followed by a crash can
    cause another email: SMTP does not offer an exactly-once delivery contract.
    """
    now = timezone.now()
    ready = Q(state='pending', available_at__lte=now) | Q(state='processing', lease_until__lte=now)
    candidate = PasswordResetDispatch.objects.filter(ready).order_by('available_at', 'pk').first()
    if candidate is None:
        return False
    lease = uuid.uuid4()
    claimed = PasswordResetDispatch.objects.filter(pk=candidate.pk).filter(ready).update(
        state='processing', lease=lease,
        lease_until=now + timedelta(seconds=max(120, int(settings.EMAIL_TIMEOUT) * 2 + 30)),
        attempts=candidate.attempts + 1,
    )
    if not claimed:
        return False
    state = 'expired' if candidate.expires_at <= now else (
        'failed' if candidate.attempts >= MAX_ATTEMPTS else 'sent'
    )
    retry = False
    try:
        if state == 'sent':
            email = cipher().decrypt(candidate.encrypted_email.encode('ascii'), ttl=600).decode()
            (service or PasswordResetService()).dispatch_request(email)
    except PasswordResetDeliveryError:
        retry = candidate.attempts + 1 < MAX_ATTEMPTS
        state = 'pending' if retry else 'failed'
        logger.error('Password reset queue delivery failed retry=%s', str(retry).lower())
    except Exception as exc:
        state = 'failed'
        logger.error('Password reset queue item rejected exception=%s', type(exc).__name__)
    updates = dict(state=state, lease=None, lease_until=None)
    if retry:
        updates['available_at'] = now + timedelta(seconds=30 * (2 ** candidate.attempts))
    else:
        updates['encrypted_email'] = ''
    PasswordResetDispatch.objects.filter(pk=candidate.pk, lease=lease).update(**updates)
    return True
