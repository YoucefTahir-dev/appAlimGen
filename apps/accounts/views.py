from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import (
    LoginView,
    PasswordChangeDoneView,
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)
from django.urls import reverse_lazy
from django.shortcuts import redirect, render
from django.views import View
from .forms import (
    LoginForm,
    ProfileForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from .models import User
from apps.core.security import log_security_event

class UserLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def form_valid(self, form):
        response = super().form_valid(form)
        log_security_event(self.request, 'Connexion réussie', user=form.get_user())
        return response

    def form_invalid(self, form):
        username = self.request.POST.get('username', '')[:80]
        log_security_event(self.request, f'Tentative de connexion échouée pour {username}', level='warning', status_code=401)
        return super().form_invalid(form)

@login_required
def user_logout(request):
    log_security_event(request, 'Déconnexion')
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
        profile_form = ProfileForm(request.POST, instance=request.user)
        if 'change_password' in request.POST:
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                log_security_event(request, 'Changement de mot de passe')
                return redirect('password_change_done')
        elif 'update_profile' in request.POST:
            if profile_form.is_valid():
                profile_form.save()
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
