import re
from collections.abc import Mapping

from django.utils.translation import gettext as _
from rest_framework.permissions import BasePermission

from apps.accounts.permissions import has_permissions


class BusinessPermission(BasePermission):
    """Require an explicit action policy, then reuse the HTML business rules.

    Empty tuples deliberately mean authentication only (or a documented
    contextual check in the handler). Missing actions and wildcards never do.
    Tuples normally require all permissions; ``any_permission_actions`` opts
    specific actions into the existing business helper's OR semantics.
    """

    message = _('Vous ne disposez pas de la permission requise.')

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if getattr(user, 'force_password_change', False):
            self.message = _('Vous devez modifier votre mot de passe avant de continuer.')
            return False

        required = getattr(view, 'required_permissions', None)
        if not isinstance(required, Mapping):
            return False
        action = getattr(view, 'action', None)
        if action == 'metadata' and request.method != 'OPTIONS':
            # DRF metadata clones PUT/POST requests to check which writable
            # fields may be advertised, without updating the ViewSet action.
            # Evaluate the real operation, never the authentication-only OPTIONS.
            action_map = getattr(view, 'action_map', None)
            if not isinstance(action_map, Mapping):
                return False
            action = action_map.get(request.method.lower())
            if not isinstance(action, str) or not action or action == 'metadata':
                return False
        if action is None:
            action = request.method.lower()
            # APIView aliases HEAD to GET; ViewSets already resolve HEAD to
            # the corresponding list/retrieve/custom action in their router.
            if action == 'head' and action not in required:
                action = 'get'
        if (
            not isinstance(action, str)
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', action)
            or action not in required
        ):
            return False
        permission_names = required[action]
        if isinstance(permission_names, str):
            permission_names = (permission_names,)
        if not isinstance(permission_names, (tuple, list)) or not all(
            isinstance(name, str)
            and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*', name)
            for name in permission_names
        ):
            return False
        any_actions = getattr(view, 'any_permission_actions', ())
        if not isinstance(any_actions, tuple) or not all(
            isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
            for name in any_actions
        ):
            return False
        if action in any_actions:
            return bool(permission_names) and has_permissions(
                user, permission_names, any_permission=True,
            )
        return not permission_names or has_permissions(user, permission_names)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
