from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import Group
from django.contrib.auth.views import (
    LoginView,
    PasswordChangeDoneView,
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)
from django.core.exceptions import PermissionDenied
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q
from django.urls import reverse, reverse_lazy
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from .forms import (
    LoginForm,
    AdminPasswordResetForm,
    ProfileForm,
    RoleForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import User
from .permissions import (
    ALL_MANAGED_PERMISSION_NAMES,
    LEGACY_MANAGER_PERMISSIONS,
    LEGACY_SELLER_PERMISSIONS,
    has_permission,
    permission_required,
    permission_sections,
)
from apps.core.security import (
    LOGIN_FAILURE_EVENT,
    PASSWORD_RESET_EVENT,
    is_authentication_rate_limited,
    is_rate_limited,
    log_security_event,
    rate_limit_action,
    sanitize_log_value,
)

class UserLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url
        destinations = (
            ('accounts.view_dashboard', 'dashboard'),
            ('inventory.view_product', 'product_list'),
            ('inventory.view_client', 'client_list'),
            ('inventory.view_supplier', 'supplier_list'),
            ('accounts.view_stock', 'stock_movement_list'),
            ('commerce.view_sale', 'sale_list'),
            ('commerce.view_purchase', 'purchase_list'),
            ('expenses.view_expense', 'expense_list'),
            ('accounts.view_user', 'user_list'),
            ('auth.view_group', 'role_list'),
        )
        for permission_name, route_name in destinations:
            if has_permission(self.request.user, permission_name):
                return reverse(route_name)
        return reverse('profile')

    def post(self, request, *args, **kwargs):
        username = sanitize_log_value(request.POST.get('username', ''))
        if is_authentication_rate_limited(
            request,
            LOGIN_FAILURE_EVENT,
            username,
        ):
            log_security_event(request, 'auth.login.blocked', level='warning', status_code=429)
            form = self.get_form()
            form.add_error(None, _('Trop de tentatives. Réessayez plus tard.'))
            response = self.render_to_response(self.get_context_data(form=form), status=429)
            response['Retry-After'] = str(getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 900))
            return response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        log_security_event(self.request, _('Connexion réussie'), user=form.get_user())
        return response

    def form_invalid(self, form):
        username = sanitize_log_value(self.request.POST.get('username', ''))
        log_security_event(
            self.request,
            rate_limit_action(LOGIN_FAILURE_EVENT, username),
            level='warning',
            status_code=401,
        )
        return super().form_invalid(form)

@login_required
@require_POST
def user_logout(request):
    log_security_event(request, _('Déconnexion'))
    logout(request)
    return redirect('login')

class UserProfileView(LoginRequiredMixin, View):
    template_name = 'accounts/profile.html'

    def get(self, request):
        password_form = StyledPasswordChangeForm(user=request.user)
        profile_form = ProfileForm(instance=request.user)
        return render(request, self.template_name, {'password_form': password_form, 'profile_form': profile_form})

    def post(self, request):
        password_form = StyledPasswordChangeForm(user=request.user, data=request.POST)
        profile_form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if 'change_password' in request.POST:
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                log_security_event(request, _('Changement de mot de passe'))
                return redirect('password_change_done')
        elif 'update_profile' in request.POST:
            if profile_form.is_valid():
                profile_form.save()
                log_security_event(request, _('Modification du profil'))
                return redirect('profile')
        return render(request, self.template_name, {'password_form': password_form, 'profile_form': profile_form})

class UserPasswordChangeDoneView(LoginRequiredMixin, PasswordChangeDoneView):
    template_name = 'accounts/password_change_done.html'

class UserPasswordResetView(PasswordResetView):
    template_name = 'accounts/password_reset.html'
    email_template_name = 'accounts/password_reset_email.html'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')
    form_class = StyledPasswordResetForm

    def post(self, request, *args, **kwargs):
        action = rate_limit_action(PASSWORD_RESET_EVENT)
        if is_rate_limited(
            request,
            action,
            getattr(settings, 'PASSWORD_RESET_LIMIT', 5),
            getattr(settings, 'PASSWORD_RESET_WINDOW_SECONDS', 3600),
        ):
            log_security_event(request, 'auth.password_reset.blocked', level='warning', status_code=429)
            form = self.get_form()
            form.add_error(None, _('Trop de demandes. Réessayez plus tard.'))
            response = self.render_to_response(self.get_context_data(form=form), status=429)
            response['Retry-After'] = str(getattr(settings, 'PASSWORD_RESET_WINDOW_SECONDS', 3600))
            return response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        log_security_event(self.request, rate_limit_action(PASSWORD_RESET_EVENT))
        return super().form_valid(form)

class UserPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'

class UserPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy('password_reset_complete')

class UserPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'

class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser

    def handle_no_permission(self):
        return redirect('dashboard')


def _selected_permissions(form, field_name, current_permissions=()):
    if form.is_bound:
        selected = form.data.getlist(field_name)
    else:
        selected = [permission.pk for permission in current_permissions]
    return permission_sections(selected)


def _can_delegate(actor, role, direct_permissions):
    if actor.is_superuser:
        return True
    role_permissions = role.permissions.all() if role and role.pk else ()
    requested = set(role_permissions) | set(direct_permissions)
    return all(
        has_permission(
            actor,
            f'{permission.content_type.app_label}.{permission.codename}',
        )
        for permission in requested
    )


def _may_manage_target(actor, target):
    if actor.is_superuser:
        return True
    if target.is_superuser:
        return False
    target_grants = set(target.get_all_permissions()) & set(
        ALL_MANAGED_PERMISSION_NAMES
    )
    if not target.groups.exists():
        if target.role == target.ADMIN:
            target_grants.update(ALL_MANAGED_PERMISSION_NAMES)
        elif target.role == target.MANAGER:
            target_grants.update(LEGACY_MANAGER_PERMISSIONS)
        elif target.role == target.SELLER:
            target_grants.update(LEGACY_SELLER_PERMISSIONS)
    actor_effective = {
        permission_name
        for permission_name in ALL_MANAGED_PERMISSION_NAMES
        if has_permission(actor, permission_name)
    }
    return target_grants <= actor_effective


def _may_manage_role(actor, role):
    if actor.is_superuser:
        return True
    return all(
        has_permission(
            actor,
            f'{permission.content_type.app_label}.{permission.codename}',
        )
        for permission in role.permissions.all()
    )


@permission_required('accounts.view_user')
def user_list(request):
    query = request.GET.get('q', '').strip()
    users = User.objects.prefetch_related('groups').order_by('username')
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
    users = list(users)
    manageable_user_ids = {
        user.pk for user in users if _may_manage_target(request.user, user)
    }
    return render(
        request,
        'accounts/user_list.html',
        {
            'users': users,
            'query': query,
            'manageable_user_ids': manageable_user_ids,
        },
    )


@permission_required('accounts.add_user')
def user_create(request):
    form = UserCreateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        role = form.cleaned_data['assigned_role']
        direct_permissions = form.cleaned_data['individual_permissions']
        if not _can_delegate(request.user, role, direct_permissions):
            form.add_error(
                'assigned_role',
                _("Vous ne pouvez pas attribuer des permissions que vous ne possédez pas."),
            )
        else:
            user = form.save()
            log_security_event(
                request,
                _('Création utilisateur: %(username)s') % {'username': user.username},
            )
            messages.success(request, _('Utilisateur créé avec succès.'))
            return redirect('user_list')
    return render(
        request,
        'accounts/user_form.html',
        {
            'form': form,
            'permission_sections': _selected_permissions(
                form, 'individual_permissions'
            ),
            'denied_permission_sections': _selected_permissions(
                form, 'denied_permissions'
            ),
            'is_create': True,
        },
    )


@permission_required('accounts.change_user')
def user_update(request, pk):
    target = get_object_or_404(User, pk=pk)
    if not _may_manage_target(request.user, target):
        raise PermissionDenied
    form = UserUpdateForm(
        request.POST or None,
        request.FILES or None,
        instance=target,
    )
    if request.method == 'POST' and form.is_valid():
        role = form.cleaned_data['assigned_role']
        direct_permissions = form.cleaned_data['individual_permissions']
        if target == request.user and not form.cleaned_data['is_active']:
            form.add_error('is_active', _('Vous ne pouvez pas désactiver votre propre compte.'))
        elif not _can_delegate(request.user, role, direct_permissions):
            form.add_error(
                'assigned_role',
                _("Vous ne pouvez pas attribuer des permissions que vous ne possédez pas."),
            )
        else:
            user = form.save()
            log_security_event(
                request,
                _('Modification utilisateur: %(username)s') % {'username': user.username},
            )
            messages.success(request, _('Utilisateur modifié avec succès.'))
            return redirect('user_list')
    return render(
        request,
        'accounts/user_form.html',
        {
            'form': form,
            'target_user': target,
            'permission_sections': _selected_permissions(
                form, 'individual_permissions', target.user_permissions.all()
            ),
            'denied_permission_sections': _selected_permissions(
                form, 'denied_permissions', target.denied_permissions.all()
            ),
            'is_create': False,
        },
    )


@require_POST
@permission_required('accounts.change_user')
def user_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        messages.error(request, _('Vous ne pouvez pas désactiver votre propre compte.'))
    elif not _may_manage_target(request.user, target):
        raise PermissionDenied
    else:
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        action = (
            _('Réactivation utilisateur : %(username)s')
            if target.is_active
            else _('Désactivation utilisateur : %(username)s')
        )
        log_security_event(request, action % {'username': target.username})
        message = (
            _('Utilisateur réactivé avec succès.')
            if target.is_active
            else _('Utilisateur désactivé avec succès.')
        )
        messages.success(request, message)
    return redirect('user_list')


@permission_required('accounts.delete_user')
def user_delete(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        messages.error(request, _('Vous ne pouvez pas supprimer votre propre compte.'))
        return redirect('user_list')
    if not _may_manage_target(request.user, target):
        raise PermissionDenied
    if request.method == 'POST':
        username = target.username
        try:
            target.delete()
        except ProtectedError:
            messages.error(
                request,
                _(
                    "Cet utilisateur possède des données métier protégées. "
                    "Désactivez son compte pour conserver l'historique."
                ),
            )
            return redirect('user_list')
        log_security_event(
            request,
            _('Suppression utilisateur: %(username)s') % {'username': username},
        )
        messages.success(request, _('Utilisateur supprimé avec succès.'))
        return redirect('user_list')
    return render(request, 'accounts/user_confirm_delete.html', {'target_user': target})


@permission_required('accounts.change_user')
def user_password_reset_admin(request, pk):
    target = get_object_or_404(User, pk=pk)
    if not _may_manage_target(request.user, target):
        raise PermissionDenied
    form = AdminPasswordResetForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save(target)
        log_security_event(
            request,
            _('Réinitialisation du mot de passe: %(username)s')
            % {'username': target.username},
        )
        messages.success(request, _('Mot de passe réinitialisé avec succès.'))
        return redirect('user_list')
    return render(
        request,
        'accounts/user_password_reset_admin.html',
        {'form': form, 'target_user': target},
    )


@permission_required('auth.view_group')
def role_list(request):
    roles = list(
        Group.objects.annotate(user_count=Count('user'))
        .prefetch_related('permissions__content_type')
        .order_by('name')
    )
    manageable_role_ids = {
        role.pk for role in roles if _may_manage_role(request.user, role)
    }
    return render(
        request,
        'accounts/role_list.html',
        {'roles': roles, 'manageable_role_ids': manageable_role_ids},
    )


@permission_required('auth.add_group')
def role_create(request):
    form = RoleForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        permissions = form.cleaned_data['permissions']
        if not _can_delegate(request.user, Group(), permissions):
            form.add_error(
                'permissions',
                _("Vous ne pouvez pas attribuer des permissions que vous ne possédez pas."),
            )
        else:
            role = form.save()
            log_security_event(
                request,
                _('Création rôle: %(role)s') % {'role': role.name},
            )
            messages.success(request, _('Rôle créé avec succès.'))
            return redirect('role_list')
    return render(
        request,
        'accounts/role_form.html',
        {
            'form': form,
            'permission_sections': _selected_permissions(form, 'permissions'),
            'is_create': True,
        },
    )


@permission_required('auth.change_group')
def role_update(request, pk):
    role = get_object_or_404(Group, pk=pk)
    if not _may_manage_role(request.user, role):
        raise PermissionDenied
    form = RoleForm(request.POST or None, instance=role)
    if request.method == 'POST' and form.is_valid():
        permissions = form.cleaned_data['permissions']
        if not _can_delegate(request.user, Group(), permissions):
            form.add_error(
                'permissions',
                _("Vous ne pouvez pas attribuer des permissions que vous ne possédez pas."),
            )
        else:
            role = form.save()
            log_security_event(
                request,
                _('Modification rôle: %(role)s') % {'role': role.name},
            )
            messages.success(request, _('Rôle modifié avec succès.'))
            return redirect('role_list')
    return render(
        request,
        'accounts/role_form.html',
        {
            'form': form,
            'role': role,
            'permission_sections': _selected_permissions(
                form, 'permissions', role.permissions.all()
            ),
            'is_create': False,
        },
    )


@permission_required('auth.delete_group')
def role_delete(request, pk):
    role = get_object_or_404(Group.objects.annotate(user_count=Count('user')), pk=pk)
    if not _may_manage_role(request.user, role):
        raise PermissionDenied
    if role.user_count:
        messages.error(
            request,
            _('Réattribuez les utilisateurs de ce rôle avant de le supprimer.'),
        )
        return redirect('role_list')
    if request.method == 'POST':
        name = role.name
        role.delete()
        log_security_event(
            request,
            _('Suppression rôle: %(role)s') % {'role': name},
        )
        messages.success(request, _('Rôle supprimé avec succès.'))
        return redirect('role_list')
    return render(request, 'accounts/role_confirm_delete.html', {'role': role})
