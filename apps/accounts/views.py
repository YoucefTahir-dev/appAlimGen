from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse_lazy
from .forms import LoginForm

class UserLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

@login_required
def user_logout(request):
    logout(request)
    return redirect('login')
