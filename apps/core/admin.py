from django.contrib import admin
from .models import CompanySettings, AuditLog

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = (
        'company_name', 'tax_number', 'nis', 'rc_number', 'article_number', 'phone',
    )

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'level', 'action', 'status_code', 'created_at')
    list_filter = ('level', 'status_code', 'created_at')
    search_fields = ('user__username', 'action', 'path', 'ip_address')
    readonly_fields = ('user', 'level', 'action', 'created_at', 'ip_address', 'path', 'status_code')

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def has_view_permission(self, request, obj=None):
        # Audit rows can retain sensitive historical metadata. General staff
        # delegation must not silently grant access to the security journal.
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
