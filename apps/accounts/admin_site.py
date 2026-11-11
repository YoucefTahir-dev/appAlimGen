from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminPasswordChangeForm as DjangoAdminPasswordChangeForm
from django.contrib.auth.views import PasswordChangeView
from django.db import transaction
from django.urls import reverse


class RevokeTokensOnPasswordSaveMixin:
    """Persist a password and invalidate old JWTs as one database change."""

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            with transaction.atomic():
                # A password form must not write a stale token version or
                # overwrite concurrently edited profile/permission fields.
                user.save(update_fields=['password'])
                user.revoke_api_tokens()
        return user


class AdminSelfPasswordChangeForm(
    RevokeTokensOnPasswordSaveMixin, DjangoAdminPasswordChangeForm,
):
    """The admin's own-password route must also revoke API credentials."""


class SecureAdminSite(AdminSite):
    def password_change(self, request, extra_context=None):
        # Keep Django's PasswordChangeView (CSRF protection, password validation,
        # and preservation of the current session), replacing only its form.
        defaults = {
            'form_class': AdminSelfPasswordChangeForm,
            'success_url': reverse('admin:password_change_done', current_app=self.name),
            'extra_context': {**self.each_context(request), **(extra_context or {})},
        }
        if self.password_change_template is not None:
            defaults['template_name'] = self.password_change_template
        request.current_app = self.name
        return PasswordChangeView.as_view(**defaults)(request)
