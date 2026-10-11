from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models.functions import Lower, Trim
from django.utils.translation import gettext_lazy as _


EMAIL_IN_USE_MESSAGE = _(
    'Cette adresse e-mail est déjà utilisée par un autre compte.'
)


def normalize_user_email(value):
    """Return the canonical representation used by account recovery."""
    return (value or '').strip().lower()


def users_with_normalized_email(queryset, email):
    """Match legacy values case-insensitively and ignoring surrounding spaces."""
    normalized = normalize_user_email(email)
    if not normalized:
        return queryset.none()
    return queryset.annotate(
        _normalized_email=Lower(Trim('email')),
    ).filter(_normalized_email=normalized)


def validate_user_email(value, *, queryset, exclude_pk=None):
    """Validate an optional e-mail and enforce the application-level policy."""
    normalized = normalize_user_email(value)
    if not normalized:
        return ''
    validate_email(normalized)
    matches = users_with_normalized_email(queryset, normalized)
    if exclude_pk is not None:
        matches = matches.exclude(pk=exclude_pk)
    if matches.exists():
        raise ValidationError(EMAIL_IN_USE_MESSAGE, code='unique')
    return normalized
