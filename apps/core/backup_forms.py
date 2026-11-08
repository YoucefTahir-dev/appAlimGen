from django import forms
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables


class BackupDriveForm(forms.Form):
    drive_destination = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)
    current_password = forms.CharField(
        label=_("Votre mot de passe actuel"),
        strip=False,
        max_length=256,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'current-password',
        }),
        help_text=_("Vérification de votre identité avant l’export des données."),
    )
    confirm_database_only = forms.BooleanField(
        label=_("Je confirme : cette archive contient uniquement la base de données, sans les médias."),
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    @sensitive_variables('password')
    def clean_current_password(self):
        password = self.cleaned_data['current_password']
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Le mot de passe actuel est incorrect."))
        return password

    def clean(self):
        cleaned_data = super().clean()
        if set(self.data) - {self.add_prefix(name) for name in self.fields} - {'csrfmiddlewaretoken'}:
            raise forms.ValidationError(_("La demande contient des paramètres non autorisés."))
        return cleaned_data


class DriveConnectionForm(BackupDriveForm):
    """Reauthenticate connecting/disconnecting without launching an export."""
    confirm_database_only = None
    drive_destination = None
