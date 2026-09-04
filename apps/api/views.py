from django.db.models import F, Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.commerce.models import Purchase, Sale
from apps.commerce.services import ensure_ticket_number
from apps.commerce.utils import build_invoice_context, generate_invoice_pdf, qr_code_data_uri
from apps.core.dashboard import DashboardPeriodError, dashboard_context
from apps.core.security import log_security_event
from apps.expenses.models import Expense, ExpenseCategory
from apps.inventory.models import Brand, Category, Client, Product, ProductPackaging, StockMovement, Supplier, Unit
from apps.inventory.pricing import get_sale_price_context
from apps.printing.models import PrinterProfile, PrintProfile
from apps.printing.services import encode_payload, invoice_print_data, printer_test_payload, select_printer_for_user

from .serializers import (
    BrandSerializer,
    CategorySerializer,
    ClientSerializer,
    ExpenseCategorySerializer,
    ExpenseSerializer,
    ProductSerializer,
    ProductPackagingSerializer,
    PrinterProfileSerializer,
    PrintProfileSerializer,
    PurchaseSerializer,
    SaleSerializer,
    StockMovementSerializer,
    SupplierSerializer,
    UnitSerializer,
)


class AuditMutationMixin:
    audit_name = 'resource'

    def _audit(self, operation, status_code=200):
        log_security_event(
            self.request,
            f'api.{self.audit_name}.{operation}',
            status_code=status_code,
        )

    def perform_create(self, serializer):
        serializer.save()
        self._audit('create', 201)

    def perform_update(self, serializer):
        serializer.save()
        self._audit('update')

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError(_('Suppression impossible : cet élément est utilisé.')) from exc
        self._audit('delete', 204)


class ProductViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('category', 'brand', 'unit').prefetch_related('packagings').all()
    search_fields = ('name', 'reference', 'barcode')
    ordering_fields = ('name', 'reference', 'quantity', 'sale_price', 'created_at')
    filterset_fields = ('category', 'brand', 'unit')
    audit_name = 'product'
    required_permissions = {
        'list': 'inventory.view_product', 'retrieve': 'inventory.view_product',
        'barcode': 'inventory.view_product', 'qr': 'inventory.view_product',
        'price': 'inventory.view_product',
        'create': 'inventory.add_product', 'update': 'inventory.change_product',
        'partial_update': 'inventory.change_product', 'destroy': 'inventory.delete_product',
    }

    @action(detail=False, methods=('get',), url_path=r'barcode/(?P<barcode>[^/.]+)')
    def barcode(self, request, barcode=None):
        product = self.get_queryset().filter(barcode=barcode).first()
        if product is None:
            return Response(
                {'success': False, 'error': {'code': 'PRODUCT_NOT_FOUND', 'message': _('Produit introuvable.')}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(product).data)

    @action(detail=False, methods=('get',), url_path=r'qr/(?P<reference>[^/.]+)')
    def qr(self, request, reference=None):
        product = self.get_queryset().filter(reference=reference).first()
        if product is None:
            return Response(
                {'success': False, 'error': {'code': 'PRODUCT_NOT_FOUND', 'message': _('Produit introuvable.')}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=('get',), url_path='price')
    def price(self, request, pk=None):
        product = self.get_object()
        client_id = request.query_params.get('client_id')
        if not client_id:
            raise ValidationError({'client_id': _('Le client est obligatoire.')})
        try:
            customer = Client.objects.get(pk=client_id)
        except (Client.DoesNotExist, TypeError, ValueError) as exc:
            raise ValidationError({'client_id': _('Client invalide.')}) from exc

        packaging = None
        packaging_id = request.query_params.get('packaging_id')
        if packaging_id:
            try:
                packaging = ProductPackaging.objects.get(
                    pk=packaging_id,
                    product=product,
                    is_active=True,
                )
            except (ProductPackaging.DoesNotExist, TypeError, ValueError) as exc:
                raise ValidationError({'packaging_id': _('Conditionnement invalide ou inactif.')}) from exc
        return Response(get_sale_price_context(product, customer, packaging))


class ProductPackagingViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    serializer_class = ProductPackagingSerializer
    queryset = ProductPackaging.objects.select_related('product').order_by('product_id', 'name', 'pk')
    search_fields = ('name', 'barcode', 'product__name', 'product__reference')
    ordering_fields = ('name', 'conversion_factor', 'default_sale_price')
    filterset_fields = ('product', 'is_active')
    audit_name = 'product_packaging'
    required_permissions = {
        'list': 'inventory.view_product', 'retrieve': 'inventory.view_product',
        'create': 'inventory.change_product', 'update': 'inventory.change_product',
        'partial_update': 'inventory.change_product', 'destroy': 'inventory.change_product',
    }


class ReferenceReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    pagination_class = None
    search_fields = ('name',)
    ordering = ('name',)
    required_permissions = {'*': 'inventory.view_product'}


class CategoryViewSet(ReferenceReadOnlyViewSet):
    queryset = Category.objects.order_by('name')
    serializer_class = CategorySerializer


class BrandViewSet(ReferenceReadOnlyViewSet):
    queryset = Brand.objects.order_by('name')
    serializer_class = BrandSerializer


class UnitViewSet(ReferenceReadOnlyViewSet):
    queryset = Unit.objects.order_by('name')
    serializer_class = UnitSerializer


class ClientViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    queryset = Client.objects.order_by('name')
    serializer_class = ClientSerializer
    search_fields = ('name', 'phone', 'email', 'tax_number')
    ordering_fields = ('name', 'created_at', 'balance')
    audit_name = 'client'
    required_permissions = {
        'list': 'inventory.view_client', 'retrieve': 'inventory.view_client',
        'history': 'inventory.view_client', 'create': 'inventory.add_client',
        'update': 'inventory.change_client', 'partial_update': 'inventory.change_client',
        'destroy': 'inventory.delete_client',
    }

    @action(detail=True, methods=('get',))
    def history(self, request, pk=None):
        sales = self.get_object().sales.prefetch_related('lines__product').order_by('-created_at')
        page = self.paginate_queryset(sales)
        serializer = SaleSerializer(page if page is not None else sales, many=True, context={'request': request})
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class SupplierViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.order_by('name')
    serializer_class = SupplierSerializer
    search_fields = ('name', 'phone', 'email', 'rc_number', 'tax_number')
    ordering_fields = ('name', 'created_at')
    audit_name = 'supplier'
    required_permissions = {
        'list': 'inventory.view_supplier', 'retrieve': 'inventory.view_supplier',
        'history': 'inventory.view_supplier', 'create': 'inventory.add_supplier',
        'update': 'inventory.change_supplier', 'partial_update': 'inventory.change_supplier',
        'destroy': 'inventory.delete_supplier',
    }

    @action(detail=True, methods=('get',))
    def history(self, request, pk=None):
        purchases = self.get_object().purchases.prefetch_related('lines__product').order_by('-created_at')
        page = self.paginate_queryset(purchases)
        serializer = PurchaseSerializer(page if page is not None else purchases, many=True, context={'request': request})
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class SaleViewSet(AuditMutationMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                  mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Sale.objects.select_related('client', 'created_by').prefetch_related(
        'lines__product', 'lines__packaging',
    ).order_by('-created_at', '-pk')
    serializer_class = SaleSerializer
    search_fields = ('invoice_number', 'ticket_number', 'client__name')
    ordering_fields = ('created_at', 'total', 'invoice_number')
    filterset_fields = ('client', 'payment_type')
    audit_name = 'sale'
    required_permissions = {
        'list': 'commerce.view_sale', 'retrieve': 'commerce.view_sale',
        'create': 'commerce.add_sale', 'destroy': 'commerce.delete_sale',
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            queryset = queryset.filter(created_at__date__gte=start)
        if end:
            queryset = queryset.filter(created_at__date__lte=end)
        return queryset

    def perform_destroy(self, instance):
        instance._stock_user = self.request.user
        super().perform_destroy(instance)


class PurchaseViewSet(AuditMutationMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                      mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Purchase.objects.select_related('supplier').prefetch_related('lines__product').order_by('-created_at', '-pk')
    serializer_class = PurchaseSerializer
    search_fields = ('reference', 'supplier__name')
    ordering_fields = ('created_at', 'total', 'reference')
    filterset_fields = ('supplier',)
    audit_name = 'purchase'
    required_permissions = {
        'list': 'commerce.view_purchase', 'retrieve': 'commerce.view_purchase',
        'create': 'commerce.add_purchase', 'destroy': 'commerce.delete_purchase',
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            queryset = queryset.filter(created_at__date__gte=start)
        if end:
            queryset = queryset.filter(created_at__date__lte=end)
        return queryset

    def perform_destroy(self, instance):
        instance._stock_user = self.request.user
        super().perform_destroy(instance)


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Sale.objects.select_related('client', 'created_by').prefetch_related(
        'lines__product', 'lines__packaging',
    ).order_by('-created_at', '-pk')
    serializer_class = SaleSerializer
    search_fields = ('invoice_number', 'ticket_number', 'client__name')
    required_permissions = {
        'list': 'accounts.view_invoices', 'retrieve': 'accounts.view_invoices',
        'pdf': 'accounts.download_invoice_pdf', 'ticket': 'accounts.print_invoice',
        'print_data': 'accounts.print_invoice',
    }

    @action(detail=True, methods=('get',))
    def pdf(self, request, pk=None):
        sale = self.get_object()
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename=facture_{sale.invoice_number}.pdf'
        generate_invoice_pdf(response, sale)
        return response

    @action(detail=True, methods=('get',))
    def ticket(self, request, pk=None):
        sale = self.get_object()
        ensure_ticket_number(sale)
        width = '58' if request.query_params.get('width') == '58' else '80'
        context = build_invoice_context(sale)
        context.update({
            'ticket_width': width,
            'auto_print': False,
            'cashier_name': (
                (sale.created_by.get_full_name() or sale.created_by.get_username())
                if sale.created_by_id else (request.user.get_full_name() or request.user.get_username())
            ),
            'qr_code_data_uri': qr_code_data_uri(sale, context['total_ttc']),
        })
        return HttpResponse(render_to_string('commerce/sale_ticket.html', context, request=request))

    @action(detail=True, methods=('get',), url_path='print-data')
    def print_data(self, request, pk=None):
        sale = self.get_object()
        printer = select_printer_for_user(request.user)
        requested_width = request.query_params.get('paper_width')
        try:
            paper_width = int(requested_width) if requested_width else (printer.paper_width if printer else 80)
        except (TypeError, ValueError) as exc:
            raise ValidationError({'paper_width': _('Largeur papier invalide.')}) from exc
        language = request.query_params.get('language', 'bilingual')
        if language not in {'fr', 'ar', 'en', 'bilingual'}:
            raise ValidationError({'language': _('Langue d’impression invalide.')})
        data = invoice_print_data(sale, paper_width=paper_width, language=language)
        data['printer'] = PrinterProfileSerializer(printer).data if printer else None
        return Response(data)


class PrinterProfileViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    queryset = PrinterProfile.objects.order_by('name')
    serializer_class = PrinterProfileSerializer
    search_fields = ('name', 'manufacturer', 'model_name')
    filterset_fields = ('connection_mode', 'protocol', 'paper_width', 'is_default', 'is_active')
    audit_name = 'printer'
    required_permissions = {
        'list': 'printing.view_printerprofile', 'retrieve': 'printing.view_printerprofile',
        'default': 'printing.view_printerprofile', 'create': 'printing.add_printerprofile',
        'update': 'printing.change_printerprofile', 'partial_update': 'printing.change_printerprofile',
        'destroy': 'printing.delete_printerprofile', 'test_payload': 'printing.test_printerprofile',
    }

    @action(detail=False, methods=('get',))
    def default(self, request):
        printer = select_printer_for_user(request.user)
        if printer is None:
            return Response(
                {'success': False, 'error': {'code': 'PRINTER_NOT_CONFIGURED', 'message': _('Aucune imprimante active configurée.')}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(printer).data)

    @action(detail=True, methods=('get',), url_path='test-payload')
    def test_payload(self, request, pk=None):
        printer = self.get_object()
        result = printer_test_payload(printer)
        return Response({
            'encoding': 'base64', 'payload': encode_payload(result.payload),
            'protocol': result.protocol,
            'raster_arabic_recommended': result.raster_arabic_recommended,
            'transport': 'client-side',
        })


class PrintProfileViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    queryset = PrintProfile.objects.select_related('printer').order_by('document_type', 'name')
    serializer_class = PrintProfileSerializer
    filterset_fields = ('document_type', 'printer', 'language', 'is_active')
    audit_name = 'print_profile'
    required_permissions = {
        'list': 'printing.view_printprofile', 'retrieve': 'printing.view_printprofile',
        'create': 'printing.add_printprofile', 'update': 'printing.change_printprofile',
        'partial_update': 'printing.change_printprofile', 'destroy': 'printing.delete_printprofile',
    }


class PrintingCapabilitiesView(APIView):
    required_permissions = {'get': 'accounts.print_invoice'}

    @extend_schema(responses=dict)
    def get(self, request):
        return Response({
            'server_connects_to_physical_printers': False,
            'supported_paper_widths': [58, 80],
            'languages': ['fr', 'ar', 'en', 'bilingual'],
            'transports': ['bluetooth', 'usb', 'network', 'windows', 'android'],
            'android_transport': 'local',
            'windows_transport': 'local-agent',
        })


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Product.objects.select_related('category', 'brand', 'unit').prefetch_related('packagings').order_by('name')
    serializer_class = ProductSerializer
    search_fields = ('name', 'reference', 'barcode')
    ordering_fields = ('name', 'quantity', 'minimum_stock')
    required_permissions = {'*': 'accounts.view_stock'}

    @action(detail=False, methods=('get',))
    def movements(self, request):
        queryset = StockMovement.objects.select_related('product', 'created_by').order_by('-created_at', '-pk')
        product = request.query_params.get('product')
        if product:
            queryset = queryset.filter(product_id=product)
        page = self.paginate_queryset(queryset)
        serializer = StockMovementSerializer(page if page is not None else queryset, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=False, methods=('get',))
    def alerts(self, request):
        queryset = self.get_queryset().filter(quantity__lte=F('minimum_stock') + 5)
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class ExpenseViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    queryset = Expense.objects.select_related('category', 'supplier', 'created_by').order_by('-date', '-pk')
    serializer_class = ExpenseSerializer
    search_fields = ('number', 'description', 'supplier__name', 'category__name')
    ordering_fields = ('date', 'amount', 'number')
    filterset_fields = ('category', 'supplier', 'payment_method')
    audit_name = 'expense'
    required_permissions = {
        'list': 'expenses.view_expense', 'retrieve': 'expenses.view_expense',
        'create': 'expenses.add_expense', 'update': 'expenses.change_expense',
        'partial_update': 'expenses.change_expense', 'destroy': 'expenses.delete_expense',
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            queryset = queryset.filter(date__gte=start)
        if end:
            queryset = queryset.filter(date__lte=end)
        return queryset


class ExpenseCategoryViewSet(AuditMutationMixin, viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.order_by('name')
    serializer_class = ExpenseCategorySerializer
    audit_name = 'expense_category'
    required_permissions = {
        'list': 'expenses.view_expense', 'retrieve': 'expenses.view_expense',
        'create': 'expenses.add_expensecategory', 'update': 'expenses.add_expensecategory',
        'partial_update': 'expenses.add_expensecategory', 'destroy': 'expenses.add_expensecategory',
    }


class DashboardView(APIView):
    required_permissions = {'get': 'accounts.view_dashboard'}

    @extend_schema(responses=dict)
    def get(self, request):
        try:
            context = dashboard_context(request, strict_period=True)
        except DashboardPeriodError as exc:
            raise ValidationError({'period': exc.args[0]}) from exc
        return Response({
            key: context[key]
            for key in (
                'period', 'start_date', 'end_date', 'sales_today', 'period_revenue',
                'sales_count', 'average_basket', 'gross_profit', 'expenses_total', 'net_profit',
                'stock_value', 'total_products', 'total_clients', 'total_suppliers',
                'purchases_total', 'period_sales', 'products_sold', 'products_purchased',
                'out_of_stock', 'low_stock', 'near_stockout', 'unpaid_invoices',
                'pending_supplier_payments', 'important_expenses', 'notification_count',
                'comparisons', 'top_products', 'top_clients', 'top_suppliers',
                'profitable_products', 'expense_categories', 'chart_data',
            )
        })


class AlertsView(APIView):
    required_permissions = {'get': 'accounts.view_dashboard'}

    @extend_schema(responses=dict)
    def get(self, request):
        products = Product.objects.filter(quantity__lte=F('minimum_stock') + 5).order_by('quantity', 'name')[:100]
        alerts = []
        for product in products:
            level = 'out_of_stock' if product.quantity == 0 else 'critical' if product.quantity <= product.minimum_stock else 'low'
            alerts.append({
                'type': 'stock', 'level': level, 'product_id': product.pk,
                'product': product.name, 'quantity': product.quantity,
                'minimum_stock': product.minimum_stock,
            })
        for sale in Sale.objects.filter(payment_tracking_initialized=True).select_related('client').order_by('-created_at')[:100]:
            if sale.balance_due > 0:
                alerts.append({
                    'type': 'unpaid_invoice', 'level': 'warning', 'sale_id': sale.pk,
                    'reference': sale.invoice_number, 'partner': sale.client.name,
                    'amount_due': sale.balance_due,
                })
        for purchase in Purchase.objects.filter(payment_tracking_initialized=True).select_related('supplier').order_by('-created_at')[:100]:
            if purchase.balance_due > 0:
                alerts.append({
                    'type': 'supplier_payment', 'level': 'warning', 'purchase_id': purchase.pk,
                    'reference': purchase.reference, 'partner': purchase.supplier.name,
                    'amount_due': purchase.balance_due,
                })
        for expense in Expense.objects.filter(Q(receipt='') | Q(receipt__isnull=True)).order_by('-date')[:100]:
            alerts.append({
                'type': 'missing_receipt', 'level': 'info', 'expense_id': expense.pk,
                'reference': expense.number, 'amount': expense.amount,
            })
        return Response({'count': len(alerts), 'results': alerts})
