from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import IsAdminUser
from rest_framework.routers import DefaultRouter

from .authentication import CurrentUserView, LogoutView, MobileTokenRefreshView, MobileTokenView
from .views import (
    AlertsView,
    BrandViewSet,
    CategoryViewSet,
    ClientViewSet,
    DashboardView,
    ExpenseCategoryViewSet,
    ExpenseViewSet,
    InvoiceViewSet,
    ProductViewSet,
    ProductPackagingViewSet,
    PrinterProfileViewSet,
    PrintProfileViewSet,
    PrintingCapabilitiesView,
    PurchaseViewSet,
    SaleViewSet,
    StockViewSet,
    SupplierViewSet,
    UnitViewSet,
)


router = DefaultRouter()
router.register('products', ProductViewSet, basename='api-product')
router.register('packagings', ProductPackagingViewSet, basename='api-product-packaging')
router.register('printers', PrinterProfileViewSet, basename='api-printer')
router.register('print-profiles', PrintProfileViewSet, basename='api-print-profile')
router.register('categories', CategoryViewSet, basename='api-category')
router.register('brands', BrandViewSet, basename='api-brand')
router.register('units', UnitViewSet, basename='api-unit')
router.register('clients', ClientViewSet, basename='api-client')
router.register('suppliers', SupplierViewSet, basename='api-supplier')
router.register('sales', SaleViewSet, basename='api-sale')
router.register('purchases', PurchaseViewSet, basename='api-purchase')
router.register('invoices', InvoiceViewSet, basename='api-invoice')
router.register('stock', StockViewSet, basename='api-stock')
router.register('expenses', ExpenseViewSet, basename='api-expense')
router.register('expense-categories', ExpenseCategoryViewSet, basename='api-expense-category')

urlpatterns = [
    path('schema/', SpectacularAPIView.as_view(permission_classes=[IsAdminUser]), name='api-schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='api-schema', permission_classes=[IsAdminUser]), name='api-docs'),
    path('v1/auth/login/', MobileTokenView.as_view(), name='api-login'),
    path('v1/auth/refresh/', MobileTokenRefreshView.as_view(), name='api-refresh'),
    path('v1/auth/logout/', LogoutView.as_view(), name='api-logout'),
    path('v1/auth/me/', CurrentUserView.as_view(), name='api-me'),
    path('v1/dashboard/', DashboardView.as_view(), name='api-dashboard'),
    path('v1/alerts/', AlertsView.as_view(), name='api-alerts'),
    path('v1/printing/', PrintingCapabilitiesView.as_view(), name='api-printing-capabilities'),
    path('v1/', include(router.urls)),
]
