"""Database-backed admission for public authentication APIs.

Each call reserves one budget before authentication. Callers check IP first,
before parsing attacker-controlled identities, then check the account budget.
A denied account attempt still consumes its IP budget. No process-local cache
participates, and a database failure must never be converted to an admission.
"""

import json
import math
from dataclasses import dataclass
from datetime import timedelta

from django.db import DatabaseError, models, transaction
from django.db.models import Case, F, Q, Value, When
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import AuthRateLimitBucket


@dataclass(frozen=True)
class Admission:
    allowed: bool
    retry_after: int = 0


def _positive_integer(value, name, maximum=2_147_483_647):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be a positive bounded integer')


def _key(scope, dimension, identifier):
    for value in (scope, dimension, identifier):
        if not isinstance(value, str) or not value:
            raise ValueError('Authentication budget components must be nonempty strings')
    if len(scope) > 100 or len(dimension) > 100:
        raise ValueError('Invalid authentication budget namespace')
    # Structured encoding prevents separator collisions. HMAC prevents offline
    # account/email/IP dictionaries from being matched to the technical table.
    value = json.dumps([scope, dimension, identifier], ensure_ascii=True, separators=(',', ':'))
    return salted_hmac('elamine.auth-rate-limit.v1', value, algorithm='sha256').hexdigest()


def consume_limit(*, scope, dimension, identifier, limit, window_seconds):
    """Atomically admit up to ``limit`` attempts in a fixed, non-extending window.

    PostgreSQL row locks serialize creation, renewal and admission. The UPDATE
    also has an atomic quota predicate: SQLite cannot silently lose increments
    and over-admit when its select_for_update is a no-op. SQLite busy/lock errors
    still propagate and must produce 503, never a cache fallback.
    """
    _positive_integer(limit, 'limit')
    _positive_integer(window_seconds, 'window_seconds', maximum=31_536_000)
    key = _key(scope, dimension, identifier)
    with transaction.atomic():
        initial_now = timezone.now()
        AuthRateLimitBucket.objects.select_for_update().get_or_create(
            key=key,
            defaults={'hits': 0, 'expires_at': initial_now + timedelta(seconds=window_seconds)},
        )
        # Take time after acquiring the row lock, not before a possible wait.
        now = timezone.now()
        expired = Q(expires_at__lte=now)
        updated = AuthRateLimitBucket.objects.filter(pk=key).filter(
            expired | Q(hits__lt=limit),
        ).update(
            hits=Case(
                When(expires_at__lte=now, then=Value(1)),
                default=F('hits') + 1,
                output_field=models.PositiveIntegerField(),
            ),
            expires_at=Case(
                When(expires_at__lte=now, then=Value(now + timedelta(seconds=window_seconds))),
                default=F('expires_at'),
                output_field=models.DateTimeField(),
            ),
        )
        if updated == 1:
            return Admission(True)
        try:
            expires_at = AuthRateLimitBucket.objects.values_list('expires_at', flat=True).get(pk=key)
        except AuthRateLimitBucket.DoesNotExist as exc:
            raise DatabaseError('Authentication admission state unavailable') from exc
        return Admission(False, max(1, math.ceil((expires_at - now).total_seconds())))


def prune_expired_buckets(*, limit=100):
    """Delete a bounded batch of expired technical rows, never active budgets.

    Run in a separate short transaction before admission, so cleanup does not
    hold unrelated locks while an account/IP budget is reserved. PostgreSQL
    skips rows currently being renewed by another worker. The DELETE repeats
    the expiry condition rather than relying on an earlier list of identities.
    """
    _positive_integer(limit, 'limit', maximum=10_000)
    now = timezone.now()
    with transaction.atomic():
        keys = list(
            AuthRateLimitBucket.objects.select_for_update(skip_locked=True)
            .filter(expires_at__lte=now).order_by('expires_at', 'pk')
            .values_list('pk', flat=True)[:limit]
        )
        if not keys:
            return 0
        deleted, _details = AuthRateLimitBucket.objects.filter(
            pk__in=keys, expires_at__lte=now,
        ).delete()
        return deleted
