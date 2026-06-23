from .models import CompanySettings


def company_settings(request):
    settings = CompanySettings.objects.first()
    return {'company_settings': settings}
