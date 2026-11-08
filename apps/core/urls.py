from django.urls import path
from .backup_views import (backup_create, backup_list, backup_drive_connect,
                          backup_drive_callback, backup_drive_disconnect)
from .views import (
    company_settings,
    company_settings_update,
    dashboard,
    dashboard_export_excel,
    dashboard_export_pdf,
)

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('backups/', backup_list, name='backup_list'),
    path('backups/create/', backup_create, name='backup_create'),
    path('backups/drive/connect/', backup_drive_connect, name='backup_drive_connect'),
    path('backups/drive/callback/', backup_drive_callback, name='backup_drive_callback'),
    path('backups/drive/disconnect/', backup_drive_disconnect, name='backup_drive_disconnect'),
    path('dashboard/export/excel/', dashboard_export_excel, name='dashboard_export_excel'),
    path('dashboard/export/pdf/', dashboard_export_pdf, name='dashboard_export_pdf'),
    path('company/', company_settings, name='company_settings'),
    path('company/edit/', company_settings_update, name='company_settings_update'),
]
