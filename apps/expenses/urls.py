from django.urls import path

from .views import (
    expense_category_create,
    expense_create,
    expense_delete,
    expense_export_excel,
    expense_export_pdf,
    expense_list,
    expense_print,
    expense_update,
)


urlpatterns = [
    path('', expense_list, name='expense_list'),
    path('new/', expense_create, name='expense_create'),
    path('<int:pk>/edit/', expense_update, name='expense_update'),
    path('<int:pk>/delete/', expense_delete, name='expense_delete'),
    path('categories/new/', expense_category_create, name='expense_category_create'),
    path('export/excel/', expense_export_excel, name='expense_export_excel'),
    path('export/pdf/', expense_export_pdf, name='expense_export_pdf'),
    path('print/', expense_print, name='expense_print'),
]
