from django.contrib import admin

from .models import PrinterProfile, PrintProfile, UserPrinterPreference


@admin.register(PrinterProfile)
class PrinterProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'model_name', 'connection_mode', 'paper_width', 'is_default', 'is_active')
    list_filter = ('connection_mode', 'protocol', 'paper_width', 'is_default', 'is_active')
    search_fields = ('name', 'manufacturer', 'model_name')


@admin.register(PrintProfile)
class PrintProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'document_type', 'printer', 'paper_width', 'copies', 'language', 'is_active')
    list_filter = ('document_type', 'language', 'is_active')


@admin.register(UserPrinterPreference)
class UserPrinterPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'printer', 'updated_at')
    autocomplete_fields = ('user', 'printer')
