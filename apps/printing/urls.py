from django.urls import path

from .views import printer_create, printer_delete, printer_list, printer_test, printer_update


urlpatterns = [
    path('', printer_list, name='printer_list'),
    path('new/', printer_create, name='printer_create'),
    path('<int:pk>/edit/', printer_update, name='printer_update'),
    path('<int:pk>/delete/', printer_delete, name='printer_delete'),
    path('<int:pk>/test/', printer_test, name='printer_test'),
]
