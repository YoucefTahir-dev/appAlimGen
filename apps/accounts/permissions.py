from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def is_admin(user):
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, 'role', None) == user.ADMIN)
    )


def is_manager(user):
    return bool(
        user.is_authenticated
        and (is_admin(user) or getattr(user, 'role', None) == user.MANAGER)
    )


def is_seller(user):
    return bool(
        user.is_authenticated
        and (is_manager(user) or getattr(user, 'role', None) == user.SELLER)
    )


def role_required(check):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if check(request.user):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied

        return wrapped

    return decorator


admin_required = role_required(is_admin)
manager_required = role_required(is_manager)
seller_required = role_required(is_seller)
