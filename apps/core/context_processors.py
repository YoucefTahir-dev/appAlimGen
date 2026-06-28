from .models import CompanySettings
from apps.accounts.permissions import is_admin, is_manager, is_seller


def company_settings(request):
    settings = CompanySettings.objects.first()
    user = request.user
    return {
        'company_settings': settings,
        'can_admin': is_admin(user),
        'can_manage': is_manager(user),
        'can_sell': is_seller(user),
    }
