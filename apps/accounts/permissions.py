from collections import OrderedDict
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


# Only business permissions are exposed in the role editor. This avoids granting
# internal Django permissions (sessions, content types, migrations) by mistake.
PERMISSION_MATRIX = OrderedDict(
    [
        (_('Tableau de bord'), [('accounts.view_dashboard', _('Voir'))]),
        (
            _('Produits'),
            [
                ('inventory.view_product', _('Voir')),
                ('inventory.add_product', _('Ajouter')),
                ('inventory.change_product', _('Modifier')),
                ('inventory.delete_product', _('Supprimer')),
            ],
        ),
        (
            _('Stock'),
            [
                ('accounts.view_stock', _('Voir')),
                ('accounts.manage_stock', _('Enregistrer un mouvement')),
            ],
        ),
        (
            _('Clients'),
            [
                ('inventory.view_client', _('Voir')),
                ('inventory.add_client', _('Ajouter')),
                ('inventory.change_client', _('Modifier')),
                ('inventory.delete_client', _('Supprimer')),
            ],
        ),
        (
            _('Fournisseurs'),
            [
                ('inventory.view_supplier', _('Voir')),
                ('inventory.add_supplier', _('Ajouter')),
                ('inventory.change_supplier', _('Modifier')),
                ('inventory.delete_supplier', _('Supprimer')),
            ],
        ),
        (
            _('Achats'),
            [
                ('commerce.view_purchase', _('Voir')),
                ('commerce.add_purchase', _('Ajouter')),
                ('commerce.change_purchase', _('Modifier')),
                ('commerce.delete_purchase', _('Supprimer')),
            ],
        ),
        (
            _('Ventes'),
            [
                ('commerce.view_sale', _('Voir')),
                ('commerce.add_sale', _('Ajouter')),
                ('commerce.change_sale', _('Modifier')),
                ('commerce.delete_sale', _('Supprimer')),
            ],
        ),
        (
            _('Factures'),
            [
                ('accounts.view_invoices', _('Voir')),
                ('accounts.download_invoice_pdf', _('Télécharger PDF')),
                ('accounts.print_invoice', _('Imprimer')),
            ],
        ),
        (
            _('Charges'),
            [
                ('expenses.view_expense', _('Voir')),
                ('expenses.add_expense', _('Ajouter')),
                ('expenses.change_expense', _('Modifier')),
                ('expenses.delete_expense', _('Supprimer')),
                ('expenses.add_expensecategory', _('Gérer les catégories')),
            ],
        ),
        (
            _('Rapports'),
            [
                ('accounts.view_reports', _('Voir')),
                ('accounts.export_reports_pdf', _('Export PDF')),
                ('accounts.export_reports_excel', _('Export Excel')),
            ],
        ),
        (
            _('Utilisateurs'),
            [
                ('accounts.view_user', _('Voir')),
                ('accounts.add_user', _('Ajouter')),
                ('accounts.change_user', _('Modifier')),
                ('accounts.delete_user', _('Supprimer')),
            ],
        ),
        (
            _('Rôles'),
            [
                ('auth.view_group', _('Voir')),
                ('auth.add_group', _('Ajouter')),
                ('auth.change_group', _('Modifier')),
                ('auth.delete_group', _('Supprimer')),
            ],
        ),
        (
            _('Paramètres'),
            [
                ('core.view_companysettings', _('Voir')),
                ('core.change_companysettings', _('Modifier')),
            ],
        ),
        (
            _('Sauvegardes'),
            [
                ('accounts.view_backups', _('Voir')),
                ('accounts.create_backups', _('Créer')),
                ('accounts.restore_backups', _('Restaurer')),
            ],
        ),
    ]
)

ALL_MANAGED_PERMISSION_NAMES = frozenset(
    permission_name
    for permissions in PERMISSION_MATRIX.values()
    for permission_name, _label in permissions
)

LEGACY_MANAGER_PERMISSIONS = frozenset(
    permission
    for permission in ALL_MANAGED_PERMISSION_NAMES
    if permission
    not in {
        'accounts.view_user',
        'accounts.add_user',
        'accounts.change_user',
        'accounts.delete_user',
        'auth.view_group',
        'auth.add_group',
        'auth.change_group',
        'auth.delete_group',
        'accounts.view_backups',
        'accounts.create_backups',
        'accounts.restore_backups',
        'core.change_companysettings',
    }
)

LEGACY_SELLER_PERMISSIONS = frozenset(
    {
        'accounts.view_dashboard',
        'accounts.view_stock',
        'accounts.view_invoices',
        'accounts.download_invoice_pdf',
        'accounts.print_invoice',
        'inventory.view_product',
        'inventory.add_client',
        'commerce.view_sale',
        'commerce.add_sale',
    }
)


def managed_permission_query():
    query = Q(pk__in=[])
    for permission_name in ALL_MANAGED_PERMISSION_NAMES:
        app_label, codename = permission_name.split('.', 1)
        query |= Q(content_type__app_label=app_label, codename=codename)
    return query


def get_managed_permissions():
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(managed_permission_query()).select_related(
        'content_type'
    ).order_by('content_type__app_label', 'codename')


def permission_sections(selected_ids=()):
    """Build labelled permission sections for the role/user HTML matrix."""
    permissions = {
        f'{permission.content_type.app_label}.{permission.codename}': permission
        for permission in get_managed_permissions()
    }
    selected_ids = {int(value) for value in selected_ids if str(value).isdigit()}
    sections = []
    for module, entries in PERMISSION_MATRIX.items():
        rows = []
        seen = set()
        for permission_name, label in entries:
            permission = permissions.get(permission_name)
            if permission is None or permission.pk in seen:
                continue
            seen.add(permission.pk)
            rows.append(
                {
                    'permission': permission,
                    'label': label,
                    'checked': permission.pk in selected_ids,
                }
            )
        if rows:
            sections.append({'module': module, 'permissions': rows})
    return sections


def _has_dynamic_role(user):
    if not hasattr(user, '_has_dynamic_role_cache'):
        user._has_dynamic_role_cache = user.groups.exists()
    return user._has_dynamic_role_cache


def _legacy_permission(user, permission_name):
    """Compatibility for accounts created before dynamic roles.

    Once a user has a Group, only official Django permissions apply. This keeps
    the legacy ``role`` column from broadening a custom role silently.
    """
    if _has_dynamic_role(user):
        return False
    role = getattr(user, 'role', None)
    if role == getattr(user, 'ADMIN', 'admin'):
        return permission_name in ALL_MANAGED_PERMISSION_NAMES
    if role == getattr(user, 'MANAGER', 'manager'):
        return permission_name in LEGACY_MANAGER_PERMISSIONS
    if role == getattr(user, 'SELLER', 'seller'):
        return permission_name in LEGACY_SELLER_PERMISSIONS
    return False


def has_permission(user, permission_name):
    if not getattr(user, 'is_authenticated', False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    if getattr(user, 'is_permission_denied', lambda _name: False)(permission_name):
        return False
    if user.has_perm(permission_name):
        return True
    return _legacy_permission(user, permission_name)


def has_permissions(user, permission_names, *, any_permission=False):
    checks = [has_permission(user, name) for name in permission_names]
    return any(checks) if any_permission else all(checks)


def permission_required(*permission_names, any_permission=False):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if has_permissions(
                request.user,
                permission_names,
                any_permission=any_permission,
            ):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied

        return wrapped

    return decorator


class PermissionRequiredMixin(AccessMixin):
    permission_required = ()
    any_permission = False

    def dispatch(self, request, *args, **kwargs):
        names = self.permission_required
        if isinstance(names, str):
            names = (names,)
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_permissions(request.user, names, any_permission=self.any_permission):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# Central safety net for every existing function view, including direct URLs.
ROUTE_PERMISSIONS = {
    'dashboard': ('accounts.view_dashboard',),
    'dashboard_export_excel': ('accounts.view_reports', 'accounts.export_reports_excel'),
    'dashboard_export_pdf': ('accounts.view_reports', 'accounts.export_reports_pdf'),
    'product_list': ('inventory.view_product',),
    'product_detail': ('inventory.view_product',),
    'product_qr_download': ('inventory.view_product',),
    'product_barcode_download': ('inventory.view_product',),
    'product_create': ('inventory.add_product',),
    'product_update': ('inventory.change_product',),
    'product_delete': ('inventory.delete_product',),
    'product_import': ('inventory.add_product',),
    'product_export': ('inventory.change_product',),
    'client_list': ('inventory.view_client',),
    'client_create': ('inventory.add_client',),
    'client_update': ('inventory.change_client',),
    'client_delete': ('inventory.delete_client',),
    'supplier_list': ('inventory.view_supplier',),
    'supplier_create': ('inventory.add_supplier',),
    'supplier_update': ('inventory.change_supplier',),
    'supplier_delete': ('inventory.delete_supplier',),
    'stock_movement_list': ('accounts.view_stock',),
    'stock_movement_create': ('accounts.manage_stock',),
    'stock_movement_delete': ('accounts.manage_stock',),
    'sale_list': ('commerce.view_sale',),
    'sale_create': ('commerce.add_sale',),
    'sale_update': ('commerce.change_sale',),
    'sale_delete': ('commerce.delete_sale',),
    'sale_invoice_preview': ('accounts.view_invoices',),
    'sale_invoice_pdf': ('accounts.download_invoice_pdf',),
    'sale_ticket_preview': ('accounts.print_invoice',),
    'purchase_list': ('commerce.view_purchase',),
    'purchase_create': ('commerce.add_purchase',),
    'purchase_update': ('commerce.change_purchase',),
    'purchase_delete': ('commerce.delete_purchase',),
    'expense_list': ('expenses.view_expense',),
    'expense_create': ('expenses.add_expense',),
    'expense_update': ('expenses.change_expense',),
    'expense_delete': ('expenses.delete_expense',),
    'expense_category_create': ('expenses.add_expensecategory',),
    'expense_export_excel': ('accounts.view_reports', 'accounts.export_reports_excel'),
    'expense_export_pdf': ('accounts.view_reports', 'accounts.export_reports_pdf'),
    'expense_print': ('accounts.view_reports',),
    'user_list': ('accounts.view_user',),
    'user_create': ('accounts.add_user',),
    'user_update': ('accounts.change_user',),
    'user_toggle_active': ('accounts.change_user',),
    'user_password_reset_admin': ('accounts.change_user',),
    'user_delete': ('accounts.delete_user',),
    'role_list': ('auth.view_group',),
    'role_create': ('auth.add_group',),
    'role_update': ('auth.change_group',),
    'role_delete': ('auth.delete_group',),
    'company_settings': ('core.view_companysettings',),
    'company_settings_update': ('core.change_companysettings',),
    'report_list': ('accounts.view_reports',),
    'report_export_pdf': ('accounts.view_reports', 'accounts.export_reports_pdf'),
    'report_export_excel': ('accounts.view_reports', 'accounts.export_reports_excel'),
    'backup_list': ('accounts.view_backups',),
    'backup_create': ('accounts.create_backups',),
    'backup_restore': ('accounts.restore_backups',),
}

AUDITED_MUTATIONS = {
    'product_create': _('Création produit'),
    'product_update': _('Modification produit'),
    'product_delete': _('Suppression produit'),
    'sale_create': _('Création vente'),
    'sale_update': _('Modification vente'),
    'sale_delete': _('Suppression vente'),
}


class RoutePermissionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        route_name = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
        action = AUDITED_MUTATIONS.get(route_name)
        if (
            action
            and request.method == 'POST'
            and response.status_code in {302, 303}
            and getattr(request.user, 'is_authenticated', False)
        ):
            from apps.core.security import log_security_event

            log_security_event(request, action, status_code=response.status_code)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        route_name = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
        required = ROUTE_PERMISSIONS.get(route_name)
        if not required:
            return None
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if has_permissions(request.user, required):
            return None

        from apps.core.security import log_security_event

        log_security_event(
            request,
            _('Accès refusé : %(route)s') % {'route': route_name},
            level='warning',
            status_code=403,
        )
        raise PermissionDenied


def is_admin(user):
    return bool(
        getattr(user, 'is_authenticated', False)
        and (
            user.is_superuser
            or user.groups.filter(name='Administrateur').exists()
            or (not _has_dynamic_role(user) and getattr(user, 'role', None) == user.ADMIN)
        )
    )


def is_manager(user):
    return bool(
        getattr(user, 'is_authenticated', False)
        and (
            is_admin(user)
            or has_permission(user, 'inventory.change_product')
            or has_permission(user, 'commerce.change_sale')
        )
    )


def is_seller(user):
    return bool(
        getattr(user, 'is_authenticated', False)
        and (is_manager(user) or has_permission(user, 'commerce.add_sale'))
    )


def role_required(legacy_check):
    """Keep legacy decorators while enforcing the route's granular rule."""
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            route_name = getattr(
                getattr(request, 'resolver_match', None), 'url_name', None
            )
            required = ROUTE_PERMISSIONS.get(route_name)
            if required:
                if has_permissions(request.user, required):
                    return view_func(request, *args, **kwargs)
            elif legacy_check(request.user):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied

        return wrapped

    return decorator


# Backwards-compatible decorators used by the existing modules. Their decisions
# are now route/permission based; the role check is only a fallback for unnamed
# legacy endpoints.
admin_required = role_required(is_admin)
manager_required = role_required(is_manager)
seller_required = role_required(is_seller)
