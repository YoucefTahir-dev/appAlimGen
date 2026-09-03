from django.utils.translation import gettext as _
from rest_framework.permissions import BasePermission

from apps.accounts.permissions import has_permissions


class BusinessPermission(BasePermission):
    """Apply the same Django business permissions used by the HTML application."""

    message = _('Vous ne disposez pas de la permission requise.')

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if getattr(user, 'force_password_change', False):
            self.message = _('Vous devez modifier votre mot de passe avant de continuer.')
            return False

        required = getattr(view, 'required_permissions', {})
        action = getattr(view, 'action', request.method.lower())
        permission_names = required.get(action, required.get('*', ()))
        if isinstance(permission_names, str):
            permission_names = (permission_names,)
        return not permission_names or has_permissions(user, permission_names)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
