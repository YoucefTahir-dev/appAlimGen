from django.contrib import admin
from .models import CompanySettings, AuditLog

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'tax_number', 'rc_number', 'phone')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'created_at')
    readonly_fields = ('user', 'action', 'created_at', 'ip_address')
