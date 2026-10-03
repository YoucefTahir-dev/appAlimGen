from django.urls import path
from .views import (
    company_settings,
    company_settings_update,
    dashboard,
    dashboard_export_excel,
    dashboard_export_pdf,
)

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('dashboard/export/excel/', dashboard_export_excel, name='dashboard_export_excel'),
    path('dashboard/export/pdf/', dashboard_export_pdf, name='dashboard_export_pdf'),
    path('company/', company_settings, name='company_settings'),
    path('company/edit/', company_settings_update, name='company_settings_update'),
]
