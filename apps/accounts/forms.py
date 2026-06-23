from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UserChangeForm
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
