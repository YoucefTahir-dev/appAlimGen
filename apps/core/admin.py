from django.contrib import admin
from .models import CompanySettings, AuditLog

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'tax_number', 'rc_number', 'phone')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'level', 'action', 'status_code', 'created_at')
    list_filter = ('level', 'status_code', 'created_at')
    search_fields = ('user__username', 'action', 'path', 'ip_address')
    readonly_fields = ('user', 'level', 'action', 'created_at', 'ip_address', 'path', 'status_code')
