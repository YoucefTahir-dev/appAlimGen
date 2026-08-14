from django.contrib import admin

from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('number', 'date', 'category', 'amount', 'payment_method', 'supplier', 'created_by')
    list_filter = ('category', 'payment_method', 'date')
    search_fields = ('number', 'description', 'supplier__name')
    readonly_fields = ('number', 'created_by', 'created_at', 'updated_at')
