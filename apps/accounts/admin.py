from django.contrib import admin
from django import forms
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin, UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import (
    AdminPasswordChangeForm as DjangoAdminPasswordChangeForm,
    UserChangeForm as DjangoUserChangeForm,
)
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from .admin_site import RevokeTokensOnPasswordSaveMixin
from .models import User


class SuperuserAccountAdminMixin:
    """The Django admin is not a delegated account/permission editor.

    The application's Web/API permission policy remains unchanged. In the
    lower-level admin, user and group management require a real superuser,
    regardless of a business role or explicitly granted model permissions.
    """

    @staticmethod
    def _can_manage_accounts(request):
        user = request.user
        return bool(user.is_active and user.is_staff and user.is_superuser)

    def has_module_permission(self, request):
        return self._can_manage_accounts(request)

    def has_view_permission(self, request, obj=None):
        return self._can_manage_accounts(request)

    def has_add_permission(self, request):
        return self._can_manage_accounts(request)

    def has_change_permission(self, request, obj=None):
        return self._can_manage_accounts(request)

    def has_delete_permission(self, request, obj=None):
        return self._can_manage_accounts(request)


class UserAdminPasswordChangeForm(
    RevokeTokensOnPasswordSaveMixin, DjangoAdminPasswordChangeForm,
):
    """Also revoke tokens when password authentication is disabled."""


class UserAdminChangeForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        granted = set(cleaned_data.get('user_permissions') or ())
        denied = set(cleaned_data.get('denied_permissions') or ())
        if granted & denied:
            raise forms.ValidationError(
                _('Une permission ne peut pas être à la fois accordée et refusée.')
            )
        return cleaned_data


@admin.register(User)
class UserAdmin(SuperuserAccountAdminMixin, DjangoUserAdmin):
    form = UserAdminChangeForm
    change_password_form = UserAdminPasswordChangeForm
    list_display = ('username', 'email', 'phone', 'role', 'is_active', 'is_staff')
    list_filter = ('role', 'is_active', 'is_staff')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Informations personnelles', {'fields': ('first_name', 'last_name', 'email', 'phone', 'photo')}),
        ('Permissions', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions', 'denied_permissions')}),
        ('Dates importantes', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'role', 'password1', 'password2'),
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            return super().save_model(request, obj, form, change)
        # The object was loaded before form validation. A concurrent password
        # reset/revocation must never be undone by an ordinary profile edit.
        # Restrict the UPDATE to editable concrete fields actually changed in
        # this form; Django saves M2M fields separately through save_related().
        update_fields = [
            field.name for field in self.model._meta.concrete_fields
            if field.editable and not field.primary_key
            and field.name not in {'password', 'auth_token_version'}
            and field.name in form.fields and field.name in form.changed_data
        ]
        if update_fields:
            obj.save(update_fields=update_fields)


# django.contrib.auth registers its unguarded GroupAdmin before autodiscovery
# reaches this application. Replace it only on the default admin site.
if admin.site.is_registered(Group):
    admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(SuperuserAccountAdminMixin, DjangoGroupAdmin):
    pass
