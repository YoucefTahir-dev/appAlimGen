from django.urls import path
from .views import (
    sale_list,
    purchase_list,
    sale_create,
    sale_update,
    sale_delete,
    purchase_create,
    purchase_update,
    purchase_delete,
    sale_invoice_pdf,
    sale_invoice_preview,
)

urlpatterns = [
    path('sales/', sale_list, name='sale_list'),
    path('sales/new/', sale_create, name='sale_create'),
    path('sales/<int:pk>/edit/', sale_update, name='sale_update'),
    path('sales/<int:pk>/delete/', sale_delete, name='sale_delete'),
    path('sales/<int:pk>/preview/', sale_invoice_preview, name='sale_invoice_preview'),
    path('sales/<int:pk>/invoice/', sale_invoice_pdf, name='sale_invoice_pdf'),
    path('purchases/', purchase_list, name='purchase_list'),
    path('purchases/new/', purchase_create, name='purchase_create'),
    path('purchases/<int:pk>/edit/', purchase_update, name='purchase_update'),
    path('purchases/<int:pk>/delete/', purchase_delete, name='purchase_delete'),
]
