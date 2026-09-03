from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from apps.core.views import health, readiness

urlpatterns = [
    path('healthz/', health, name='health'),
    path('readyz/', readiness, name='readiness'),
    path('i18n/', include('django.conf.urls.i18n')),
    path('api/', include('apps.api.urls')),
    path('admin/', admin.site.urls),
    path('', include('apps.accounts.urls')),
    path('core/', include('apps.core.urls')),
    path('inventory/', include('apps.inventory.urls')),
    path('commerce/', include('apps.commerce.urls')),
    path('expenses/', include('apps.expenses.urls')),
    path('settings/printers/', include('apps.printing.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
