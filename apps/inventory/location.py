"""Shared location validation and audit for web forms and the Android API."""
import math

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

LOCATION_FIELDS = ('latitude', 'longitude', 'location_accuracy', 'formatted_address', 'place_id')


def validate_finite(value):
    if not math.isfinite(value):
        raise ValidationError(_('La valeur doit être un nombre fini.'))


def validate_location(latitude, longitude, location_accuracy=None):
    errors = {}
    for field, value, low, high in (
        ('latitude', latitude, -90, 90), ('longitude', longitude, -180, 180),
        ('location_accuracy', location_accuracy, 0, float('inf')),
    ):
        if value is not None and (not math.isfinite(value) or not low <= value <= high):
            errors[field] = _('Coordonnée ou précision GPS invalide.')
    if (latitude is None) != (longitude is None):
        errors['__all__'] = _('Renseignez ensemble la latitude et la longitude.')
    if latitude is None and location_accuracy is not None:
        errors['location_accuracy'] = _('La précision nécessite une position GPS.')
    if errors:
        raise ValidationError(errors)


def location_snapshot(client):
    return tuple(getattr(client, field) for field in LOCATION_FIELDS)


def audit_location(request, client, before):
    from apps.core.security import log_security_event

    after = location_snapshot(client)
    if before != after and (before is not None or client.latitude is not None):
        old = before[:2] if before else (None, None)
        log_security_event(request, f'client.location.updated id={client.pk} old={old} new={after[:2]}')
