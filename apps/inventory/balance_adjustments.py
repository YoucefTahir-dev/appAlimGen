"""Journalled corrections of remaining opening debt, without historical repricing."""

from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden
from django.utils.translation import gettext as _

from apps.accounts.permissions import has_permission

from .models import Client, ClientBalanceAdjustment


def client_balance_corrections_enabled():
    """A reversible correction switch, never a switch for initial debt traces."""
    return getattr(settings, 'CLIENT_BALANCE_CORRECTIONS_ENABLED', False) is True


def require_client_balance_corrections_enabled():
    if not client_balance_corrections_enabled():
        raise PermissionDenied(_('La correction de dette antérieure est désactivée.'))


def client_balance_corrections_required(view_func):
    """Gate the Web entry before its idempotency registry, including replays."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            require_client_balance_corrections_enabled()
        except PermissionDenied as exc:
            return HttpResponseForbidden(str(exc), content_type='text/plain; charset=utf-8')
        return view_func(request, *args, **kwargs)

    return wrapped


def _balance(value):
    # DecimalField validation rejects NaN/infinity, excessive precision and range.
    return Client._meta.get_field('balance').clean(value, None)


def _record(*, client, before, after, reason, user, kind):
    if not getattr(user, 'is_authenticated', False) or not user.is_active or not user.pk:
        raise PermissionDenied
    record = ClientBalanceAdjustment(
        client=client, kind=kind, balance_before=before, balance_after=after,
        delta=after - before, reason=reason, created_by=user,
        actor_id_snapshot=user.pk, actor_name_snapshot=user.get_username(),
    )
    record.full_clean()
    record._ledger_write_allowed = True
    record.save(force_insert=True)
    return record


@transaction.atomic
def adjust_client_balance(*, client_id, expected_balance, new_balance, reason, user):
    """Compare and set one remaining debt under the same client lock as receipts."""
    if not has_permission(user, 'inventory.adjust_client_balance'):
        raise PermissionDenied
    require_client_balance_corrections_enabled()
    expected = _balance(expected_balance)
    target = _balance(new_balance)
    reason = str(reason or '').strip()
    if not reason:
        raise ValidationError({'reason': _('Le motif de la correction est obligatoire.')})
    if len(reason) > 1000:
        raise ValidationError({'reason': _('Le motif ne peut pas dépasser 1000 caractères.')})
    client = Client.objects.select_for_update().get(pk=client_id)
    before = client.balance
    if before != expected:
        raise ValidationError({'expected_balance': _('La dette a changé. Rechargez le compte client avant de corriger.')})
    if before == target:
        raise ValidationError({'new_balance': _('La correction doit modifier la dette antérieure.')})
    if Client.objects.filter(pk=client.pk, balance=before).update(balance=target) != 1:
        raise ValidationError({'expected_balance': _('La dette a changé. Rechargez le compte client avant de corriger.')})
    client.balance = target
    return _record(
        client=client, before=before, after=target, reason=reason,
        user=user, kind=ClientBalanceAdjustment.CORRECTION,
    )


@transaction.atomic
def save_new_client_with_balance_audit(*, client, user):
    """Always journal new non-zero opening debt, including bridge/rollback mode."""
    if not has_permission(user, 'inventory.add_client'):
        raise PermissionDenied
    if client.pk or not client._state.adding:
        raise ValidationError(_('Utilisez la correction dédiée pour un client existant.'))
    client.balance = _balance(client.balance)
    client.save(force_insert=True)
    if client.balance:
        _record(
            client=client, before=Decimal('0.00'), after=client.balance,
            reason=_('Dette antérieure saisie à la création du client.'),
            user=user, kind=ClientBalanceAdjustment.INITIAL,
        )
    return client


def create_client_with_balance_audit(*, user, **client_fields):
    return save_new_client_with_balance_audit(client=Client(**client_fields), user=user)


def save_client_profile(*, client):
    """A stale profile object must never overwrite a payment's current balance."""
    if not client.pk or client._state.adding:
        raise ValidationError(_('Le client doit déjà exister.'))
    fields = tuple(
        field.name for field in Client._meta.concrete_fields
        if not field.primary_key and field.name not in {'balance', 'created_at'}
    )
    client.save(update_fields=fields)
    return client
