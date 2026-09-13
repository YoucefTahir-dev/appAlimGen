import hashlib
import json
import re

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.response import Response

from .exceptions import BusinessAPIException
from .models import IdempotencyRecord


IDEMPOTENCY_HEADER = 'Idempotency-Key'
VALID_KEY = re.compile(r'^[A-Za-z0-9._:-]{8,128}$')


def _request_hash(request):
    raw = json.dumps(request.data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def idempotent(request, operation, callback, *, required=True):
    key = (request.headers.get(IDEMPOTENCY_HEADER) or '').strip()
    if not key:
        if required:
            raise BusinessAPIException('IDEMPOTENCY_KEY_REQUIRED', f'L’en-tête {IDEMPOTENCY_HEADER} est obligatoire.')
        return callback()
    if not VALID_KEY.fullmatch(key):
        raise BusinessAPIException('INVALID_IDEMPOTENCY_KEY', 'La clé d’idempotence doit contenir entre 8 et 128 caractères sûrs.')

    digest = _request_hash(request)
    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                user=request.user, operation=operation, key=key, request_hash=digest,
            )
            response = callback()
            if response.status_code >= 400:
                transaction.set_rollback(True)
                return response
            record.response_status = response.status_code
            record.response_body = response.data
            record.completed = True
            record.completed_at = timezone.now()
            data = response.data
            if isinstance(data, dict):
                record.resource_id = str(data.get('id', ''))[:80]
            record.save(update_fields=('response_status', 'response_body', 'completed', 'completed_at', 'resource_id'))
            response['Idempotency-Replayed'] = 'false'
            return response
    except IntegrityError:
        try:
            record = IdempotencyRecord.objects.get(user=request.user, operation=operation, key=key)
        except IdempotencyRecord.DoesNotExist:
            raise
        if record.request_hash != digest:
            raise BusinessAPIException('IDEMPOTENCY_KEY_REUSED', 'Cette clé a déjà été utilisée avec une requête différente.')
        if not record.completed:
            raise BusinessAPIException('IDEMPOTENCY_REQUEST_IN_PROGRESS', 'Une requête portant cette clé est déjà en cours.')
        response = Response(record.response_body, status=record.response_status)
        response['Idempotency-Replayed'] = 'true'
        return response
