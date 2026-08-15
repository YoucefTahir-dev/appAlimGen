from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.forms import (
    AuthenticationForm,
    UserCreationForm,
    UserChangeForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.utils.translation import gettext_lazy as _

from .models import User
from .permissions import get_managed_permissions


LEGACY_ROLE_BY_GROUP = {
    'Administrateur': User.ADMIN,
    'Gestionnaire': User.MANAGER,
    'Vendeur': User.SELLER,
}


def clear_permission_caches(user):
    for attribute in (
        '_perm_cache',
        '_group_perm_cache',
        '_user_perm_cache',
        '_denied_permission_names_cache',
        '_has_dynamic_role_cache',
    ):
        if hasattr(user, attribute):
            delattr(user, attribute)


def validate_permission_overrides(form, cleaned_data):
    granted = set(cleaned_data.get('individual_permissions') or ())
    denied = set(cleaned_data.get('denied_permissions') or ())
    overlap = granted & denied
    if overlap:
        message = _(
            'Une permission ne peut pas être à la fois accordée et refusée.'
        )
        form.add_error('individual_permissions', message)
        form.add_error('denied_permissions', message)
    return cleaned_data


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label=_("Nom d'utilisateur"),
        widget=forms.TextInput(
            attrs={
                'autofocus': True,
                'class': 'form-control',
                'placeholder': _("Nom d'utilisateur"),
            }
        ),
    )
    password = forms.CharField(
        label=_('Mot de passe'),
        widget=forms.PasswordInput(
            attrs={'class': 'form-control', 'placeholder': _('Mot de passe')}
        ),
    )


class UserCreateForm(UserCreationForm):
    assigned_role = forms.ModelChoiceField(
        label=_('Rôle'),
        queryset=Group.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    individual_permissions = forms.ModelMultipleChoiceField(
        label=_('Permissions individuelles supplémentaires'),
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    denied_permissions = forms.ModelMultipleChoiceField(
        label=_('Permissions individuelles refusées'),
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_('Ces refus sont prioritaires sur les droits du rôle.'),
    )

    class Meta:
        model = User
        fields = (
            'username',
            'first_name',
            'last_name',
            'email',
            'phone',
            'photo',
            'is_active',
            'force_password_change',
        )
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'photo': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'force_password_change': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_role'].queryset = Group.objects.order_by('name')
        self.fields['individual_permissions'].queryset = get_managed_permissions()
        self.fields['denied_permissions'].queryset = get_managed_permissions()
        self.fields['email'].required = True
        self.fields['password1'].widget.attrs['class'] = 'form-control'
        self.fields['password2'].widget.attrs['class'] = 'form-control'

    def clean(self):
        return validate_permission_overrides(self, super().clean())

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            assigned_role = self.cleaned_data['assigned_role']
            user.groups.set([assigned_role])
            user.user_permissions.set(self.cleaned_data['individual_permissions'])
            user.denied_permissions.set(self.cleaned_data['denied_permissions'])
            legacy_role = LEGACY_ROLE_BY_GROUP.get(assigned_role.name)
            if legacy_role and user.role != legacy_role:
                user.role = legacy_role
                user.save(update_fields=['role'])
            clear_permission_caches(user)
        return user


class UserUpdateForm(UserChangeForm):
    password = None
    assigned_role = forms.ModelChoiceField(
        label=_('Rôle'),
        queryset=Group.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    individual_permissions = forms.ModelMultipleChoiceField(
        label=_('Permissions individuelles supplémentaires'),
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    denied_permissions = forms.ModelMultipleChoiceField(
        label=_('Permissions individuelles refusées'),
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_('Ces refus sont prioritaires sur les droits du rôle.'),
    )

    class Meta:
        model = User
        fields = (
            'username',
            'first_name',
            'last_name',
            'email',
            'phone',
            'photo',
            'is_active',
            'force_password_change',
        )
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'photo': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'force_password_change': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_role'].queryset = Group.objects.order_by('name')
        self.fields['individual_permissions'].queryset = get_managed_permissions()
        self.fields['denied_permissions'].queryset = get_managed_permissions()
        self.fields['email'].required = True
        if self.instance and self.instance.pk:
            self.fields['assigned_role'].initial = self.instance.primary_role
            self.fields['individual_permissions'].initial = self.instance.user_permissions.all()
            self.fields['denied_permissions'].initial = self.instance.denied_permissions.all()

    def clean(self):
        return validate_permission_overrides(self, super().clean())

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            assigned_role = self.cleaned_data['assigned_role']
            user.groups.set([assigned_role])
            user.user_permissions.set(self.cleaned_data['individual_permissions'])
            user.denied_permissions.set(self.cleaned_data['denied_permissions'])
            legacy_role = LEGACY_ROLE_BY_GROUP.get(assigned_role.name)
            if legacy_role and user.role != legacy_role:
                user.role = legacy_role
                user.save(update_fields=['role'])
            clear_permission_caches(user)
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone', 'photo')
        widgets = {
            'first_name': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': _('Prénom')}
            ),
            'last_name': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': _('Nom')}
            ),
            'email': forms.EmailInput(
                attrs={'class': 'form-control', 'placeholder': _('Email')}
            ),
            'phone': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': _('Téléphone')}
            ),
            'photo': forms.ClearableFileInput(
                attrs={'class': 'form-control', 'accept': 'image/*'}
            ),
        }


class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        label=_('Permissions'),
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Group
        fields = ('name', 'permissions')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['permissions'].queryset = get_managed_permissions()


class StyledPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label=_('Mot de passe actuel'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'current-password',
                'class': 'form-control',
                'placeholder': _('Mot de passe actuel'),
            }
        ),
    )
    new_password1 = forms.CharField(
        label=_('Nouveau mot de passe'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'new-password',
                'class': 'form-control',
                'placeholder': _('Nouveau mot de passe'),
            }
        ),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label=_('Confirmation du nouveau mot de passe'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'new-password',
                'class': 'form-control',
                'placeholder': _('Confirmation du mot de passe'),
            }
        ),
    )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.force_password_change = False
        if commit:
            user.save()
        return user


class StyledPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label=_('Adresse email'),
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                'autocomplete': 'email',
                'class': 'form-control',
                'placeholder': _('Adresse email'),
            }
        ),
    )


class StyledSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label=_('Nouveau mot de passe'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'new-password',
                'class': 'form-control',
                'placeholder': _('Nouveau mot de passe'),
            }
        ),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label=_('Confirmation du nouveau mot de passe'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'new-password',
                'class': 'form-control',
                'placeholder': _('Confirmation du mot de passe'),
            }
        ),
    )


class AdminPasswordResetForm(forms.Form):
    password1 = forms.CharField(
        label=_('Nouveau mot de passe'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={'class': 'form-control', 'placeholder': _('Nouveau mot de passe')}
        ),
    )
    password2 = forms.CharField(
        label=_('Confirmation du nouveau mot de passe'),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': _('Confirmation du mot de passe'),
            }
        ),
    )
    force_password_change = forms.BooleanField(
        label=_("Forcer l'utilisateur à changer son mot de passe à la prochaine connexion"),
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_('Les deux mots de passe ne correspondent pas.'))
        password_validation.validate_password(password1)
        return cleaned_data

    def save(self, user):
        password = self.cleaned_data['password1']
        user.set_password(password)
        user.force_password_change = self.cleaned_data['force_password_change']
        user.save()
        return user
