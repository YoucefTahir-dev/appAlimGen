from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import (
    AuthenticationForm,
    UserCreationForm,
    UserChangeForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from .models import User

class LoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={'autofocus': True, 'class': 'form-control', 'placeholder': 'Nom d\'utilisateur'}))
    password = forms.CharField(label='Mot de passe', widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Mot de passe'}))

class UserCreateForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'email', 'role', 'first_name', 'last_name')
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
        }

class UserUpdateForm(UserChangeForm):
    password = None

    class Meta:
        model = User
        fields = ('username', 'email', 'role', 'first_name', 'last_name', 'is_active')
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email')
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Prénom'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nom'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email'}),
        }

class StyledPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label='Mot de passe actuel',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'class': 'form-control', 'placeholder': 'Mot de passe actuel'}),
    )
    new_password1 = forms.CharField(
        label='Nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'form-control', 'placeholder': 'Nouveau mot de passe'}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label='Confirmation du nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'form-control', 'placeholder': 'Confirmation du mot de passe'}),
    )

class StyledPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label='Adresse email',
        max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'class': 'form-control', 'placeholder': 'Adresse email'}),
    )

class StyledSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label='Nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'form-control', 'placeholder': 'Nouveau mot de passe'}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label='Confirmation du nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'form-control', 'placeholder': 'Confirmation du mot de passe'}),
    )

class AdminPasswordResetForm(forms.Form):
    password1 = forms.CharField(
        label='Nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Nouveau mot de passe'}),
    )
    password2 = forms.CharField(
        label='Confirmation du nouveau mot de passe',
        strip=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirmation du mot de passe'}),
    )
    force_password_change = forms.BooleanField(
        label='Forcer l\'utilisateur à changer son mot de passe à la prochaine connexion',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Les deux mots de passe ne correspondent pas.')
        password_validation.validate_password(password1)
        return cleaned_data

    def save(self, user):
        password = self.cleaned_data['password1']
        user.set_password(password)
        user.force_password_change = self.cleaned_data['force_password_change']
        user.save()
        return user
