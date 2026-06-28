import secrets
import string
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django import forms
from .models import User

class AdminPasswordResetForm(forms.Form):
    password1 = forms.CharField(
        label='Nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )
    password2 = forms.CharField(
        label='Confirmation du nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )
    force_password_change = forms.BooleanField(
        label='Forcer le changement du mot de passe à la prochaine connexion',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Les mots de passe ne correspondent pas.')
        return cleaned_data

    def save(self, user):
        user.set_password(self.cleaned_data['password1'])
        if self.cleaned_data['force_password_change']:
            user.force_password_change = True
        user.save()
        return user


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('username', 'email', 'role', 'is_active', 'is_staff')
    list_filter = ('role', 'is_active', 'is_staff')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Informations personnelles', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates importantes', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'role', 'password1', 'password2'),
        }),
    )
    actions = ['reset_password_action']

    def _generate_password(self, length=12):
        alphabet = string.ascii_letters + string.digits + '!@#$%&*?'
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    def reset_password_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, 'Sélectionnez un seul utilisateur pour réinitialiser le mot de passe.', level='error')
            return
        user = queryset.first()
        new_password = self._generate_password()
        user.set_password(new_password)
        user.save()
        self.message_user(request, f'Le mot de passe de {user.username} a été réinitialisé. Nouveau mot de passe : {new_password}')
    reset_password_action.short_description = 'Réinitialiser le mot de passe utilisateur'
