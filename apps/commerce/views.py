from decimal import Decimal, ROUND_HALF_UP
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.utils.translation import gettext as _

from apps.accounts.permissions import has_permission, manager_required, permission_required, seller_required
from apps.core.pagination import paginate_queryset
from apps.inventory.models import Client, LoadingOrder, Product, ProductPackaging
from apps.inventory.forms import QuickProductForm
from apps.inventory.pricing import get_sale_price_context, sale_packaging_allowed

from .forms import (
    CustomerPaymentForm, PaymentCancellationForm, PaymentForm, PurchaseForm,
    PurchaseLineFormSet, SaleForm, SaleLineFormSet,
)
from .models import Payment, Purchase, Sale
from .services import create_sale, create_purchase, ensure_ticket_number, refresh_invoice_snapshot
from .utils import build_invoice_context, generate_invoice_pdf, qr_code_data_uri
from .product_search import commercial_products, search_products
from .receivables import attach_receivable_counts, receivable_account, receivables_queryset


MONEY_QUANTUM = Decimal('0.01')


def _client_search_rate_limited(user):
    limit = int(getattr(settings, 'SALE_CLIENT_SEARCH_RATE_LIMIT', 60))
    window = int(time.time()) // 60
    key = f'sale-client-search:{user.pk}:{window}'
    if cache.add(key, 1, timeout=70):
        return False
    try:
        count = cache.incr(key)
    except (ValueError, NotImplementedError):
        count = int(cache.get(key, 1)) + 1
        cache.set(key, count, timeout=70)
    return count > limit


def visible_sales(user, queryset=None):
    queryset = queryset if queryset is not None else Sale.objects.all()
    if LoadingOrder.objects.filter(operator=user).exists() and not has_permission(
        user, 'inventory.view_all_loadingorders'
    ):
        queryset = queryset.filter(created_by=user)
    return queryset


@login_required
@require_GET
def commercial_product_search(request):
    context = request.GET.get('context', '')
    commercial_products(request.user, context)
    query = request.GET.get('q', '').strip()
    if len(query) > 100:
        return JsonResponse({'error': _('Recherche trop longue.')}, status=400)
    customer = None
    client_id = request.GET.get('client_id')
    if context == 'sale' and client_id:
        if not client_id.isdecimal() or len(client_id) > 18:
            return JsonResponse({'error': _('Client invalide.')}, status=400)
        customer = get_object_or_404(Client, pk=client_id)
    response = JsonResponse({'results': search_products(
        user=request.user, query=query, context=context, customer=customer,
    )})
    response['Cache-Control'] = 'private, no-store'
    return response


@require_GET
@permission_required('commerce.add_sale', 'commerce.change_sale', any_permission=True)
def sale_client_search(request):
    if _client_search_rate_limited(request.user):
        response = JsonResponse({'error': _('Trop de recherches. Réessayez dans un instant.')}, status=429)
        response['Retry-After'] = '60'
        response['Cache-Control'] = 'private, no-store'
        return response

    query = request.GET.get('q', '').strip()
    if len(query) > 100:
        return JsonResponse({'error': _('Recherche trop longue.')}, status=400)
    if len(query) < 2:
        response = JsonResponse({'results': []})
        response['Cache-Control'] = 'private, no-store'
        return response

    clients = Client.objects.filter(
        Q(name__icontains=query)
        | Q(phone__icontains=query)
        | Q(tax_number__icontains=query)
    ).only('id', 'name', 'phone', 'tax_number', 'customer_type').order_by('name', 'pk')[:20]
    response = JsonResponse({'results': [
        {
            'id': client.pk,
            'name': client.name,
            'phone': client.phone,
            'tax_number': client.tax_number,
            'customer_type': client.get_customer_type_display(),
        }
        for client in clients
    ]})
    response['Cache-Control'] = 'private, no-store'
    return response


def _money(value):
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _set_formset_stock_user(formset, user):
    for line_form in formset.forms:
        line_form.instance._stock_user = user


def _add_stock_error(form, exception):
    for error in exception.messages:
        form.add_error(None, error)


def _record_remaining_sale_payment(sale, user):
    remaining = _money(sale.balance_due)
    if remaining <= 0:
        return None
    payment = Payment(
        sale=sale,
        amount=remaining,
        payment_type=sale.payment_type,
        created_by=user,
    )
    payment.save()
    return payment


def _with_payment_totals(queryset):
    money_field = DecimalField(max_digits=14, decimal_places=2)
    return queryset.annotate(
        paid_total=Coalesce(
            Sum('payments__amount', filter=Q(payments__cancelled_at__isnull=True)),
            Value(Decimal('0.00')),
            output_field=money_field,
        )
    ).annotate(
        due_total=ExpressionWrapper(F('total') - F('paid_total'), output_field=money_field)
    )


def calculate_sale_total(sale, formset):
    total = 0
    for line in formset:
        if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
            total += line.cleaned_data['quantity'] * line.cleaned_data['unit_price']
    tax_amount = total * (sale.tax_rate / 100)
    return _money(total + tax_amount - sale.discount)


def discount_exceeds_sale_margin(sale, formset):
    available_margin = 0
    for line in formset:
        if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
            product = line.cleaned_data['product']
            packaging = line.cleaned_data.get('packaging')
            quantity = line.cleaned_data['quantity']
            unit_price = line.cleaned_data['unit_price']
            factor = packaging.conversion_factor if packaging else 1
            available_margin += quantity * (unit_price - (product.purchase_price * factor))
    return sale.discount > available_margin


def calculate_purchase_total(purchase, formset):
    total = 0
    for line in formset:
        if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
            total += line.cleaned_data['quantity'] * line.cleaned_data['purchase_price']
    tax_amount = total * (purchase.tax_rate / 100)
    return _money(total + tax_amount)


@seller_required
def sale_list(request):
    queryset = _with_payment_totals(visible_sales(request.user, Sale.objects.select_related('client'))).order_by('-created_at', '-pk')
    page_obj, pagination_query = paginate_queryset(request, queryset, per_page=25)
    return render(
        request,
        'commerce/sale_list.html',
        {'sales': page_obj, 'page_obj': page_obj, 'pagination_query': pagination_query},
    )


@manager_required
def purchase_list(request):
    queryset = _with_payment_totals(Purchase.objects.select_related('supplier')).order_by('-created_at', '-pk')
    page_obj, pagination_query = paginate_queryset(request, queryset, per_page=25)
    return render(
        request,
        'commerce/purchase_list.html',
        {'purchases': page_obj, 'page_obj': page_obj, 'pagination_query': pagination_query},
    )


@seller_required
def sale_create(request):
    form = SaleForm(request.POST or None)
    formset = SaleLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        try:
            create_sale(
                client=form.cleaned_data['client'],
                lines=[line.cleaned_data for line in formset
                       if line.cleaned_data and not line.cleaned_data.get('DELETE')],
                discount=form.cleaned_data['discount'],
                tax_rate=form.cleaned_data['tax_rate'],
                payment_type=form.cleaned_data['payment_type'],
                user=request.user,
                pay_full=form.cleaned_data['settlement_action'] == SaleForm.PAY_FULL,
                initial_payment_amount=form.cleaned_data.get('amount_received'),
                show_legal_information=form.cleaned_data['show_legal_information'],
            )
        except ValidationError as exc:
            _add_stock_error(form, exc)
        else:
            messages.success(request, 'Facture enregistrée avec succès.')
            return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': _('Nouvelle facture')})


@require_GET
@permission_required('commerce.add_sale', 'commerce.change_sale', any_permission=True)
def sale_price_lookup(request):
    product_id = request.GET.get('product_id')
    client_id = request.GET.get('client_id')
    if not product_id or not client_id:
        return JsonResponse({'error': _('Produit et client obligatoires.')}, status=400)
    if any(not value.isdecimal() or len(value) > 18 for value in (product_id, client_id)):
        return JsonResponse({'error': _('Produit et client obligatoires.')}, status=400)
    product = get_object_or_404(commercial_products(request.user, 'sale').prefetch_related('packagings'), pk=product_id)
    customer = get_object_or_404(Client, pk=client_id)
    packaging = None
    packaging_id = request.GET.get('packaging_id')
    if packaging_id:
        if not packaging_id.isdecimal() or len(packaging_id) > 18:
            return JsonResponse({'error': _('Conditionnement invalide ou inactif.')}, status=400)
        packaging = get_object_or_404(
            ProductPackaging,
            pk=packaging_id,
            product=product,
            is_active=True,
            sale_enabled=True,
        )
        if not sale_packaging_allowed(product, packaging):
            return JsonResponse(
                {'error': _('Conditionnement de vente invalide ou inactif.')},
                status=400,
            )
    return JsonResponse(get_sale_price_context(product, customer, packaging))


@manager_required
def purchase_create(request):
    form = PurchaseForm(request.POST or None)
    formset = PurchaseLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        try:
            create_purchase(
                reference=form.cleaned_data['reference'],
                supplier=form.cleaned_data['supplier'],
                tax_rate=form.cleaned_data['tax_rate'],
                lines=[line.cleaned_data for line in formset
                       if line.cleaned_data and not line.cleaned_data.get('DELETE')],
                user=request.user,
            )
        except ValidationError as exc:
            _add_stock_error(form, exc)
        else:
            messages.success(request, "Bon d'achat enregistré avec succès.")
            return redirect('purchase_list')
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': _('Nouvel achat'), 'quick_product_form': QuickProductForm(prefix='quick')})


@manager_required
def sale_update(request, pk):
    sale = get_object_or_404(visible_sales(request.user), pk=pk)
    form = SaleForm(request.POST or None, instance=sale)
    formset = SaleLineFormSet(request.POST or None, instance=sale, prefix='lines')
    if form.is_valid() and formset.is_valid():
        sale = form.save(commit=False)
        sale.total = calculate_sale_total(sale, formset)
        if sale.total < 0:
            form.add_error('discount', _('La remise ne peut pas dépasser le total de la vente.'))
        elif discount_exceeds_sale_margin(sale, formset):
            form.add_error(
                'discount',
                _("Impossible de vendre un produit à un prix inférieur à son prix d'achat."),
            )
        else:
            try:
                with transaction.atomic():
                    Sale.objects.select_for_update().get(pk=sale.pk)
                    list(Payment.objects.select_for_update().filter(sale=sale).order_by('pk'))
                    sale.payment_tracking_initialized = True
                    sale.show_legal_information = form.cleaned_data['show_legal_information']
                    sale.save()
                    _set_formset_stock_user(formset, request.user)
                    formset.save()
                    refresh_invoice_snapshot(sale)
                    remaining = _money(sale.balance_due)
                    action = form.cleaned_data['settlement_action']
                    if action == SaleForm.PAY_FULL:
                        _record_remaining_sale_payment(sale, request.user)
                    elif action == SaleForm.PAY_PARTIAL:
                        amount = _money(form.cleaned_data['amount_received'])
                        if not Decimal('0.00') < amount < remaining:
                            raise ValidationError(
                                _('Un paiement partiel doit être strictement positif et inférieur au reste à payer.')
                            )
                        Payment.objects.create(
                            sale=sale, amount=amount, payment_type=sale.payment_type,
                            created_by=request.user,
                        )
            except ValidationError as exc:
                _add_stock_error(form, exc)
            else:
                messages.success(request, 'Facture mise à jour avec succès.')
                return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': _('Modifier la facture')})


@manager_required
def sale_delete(request, pk):
    sale = get_object_or_404(visible_sales(request.user), pk=pk)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                sale._stock_user = request.user
                sale.delete()
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except ProtectedError:
            messages.error(request, _('Suppression impossible : cette facture possède des règlements.'))
        else:
            messages.success(request, 'Facture supprimée.')
        return redirect('sale_list')
    return render(request, 'commerce/sale_confirm_delete.html', {'sale': sale})


@manager_required
def purchase_update(request, pk):
    purchase = get_object_or_404(Purchase, pk=pk)
    form = PurchaseForm(request.POST or None, instance=purchase)
    formset = PurchaseLineFormSet(request.POST or None, instance=purchase, prefix='lines')
    if form.is_valid() and formset.is_valid():
        purchase = form.save(commit=False)
        try:
            with transaction.atomic():
                purchase.total = calculate_purchase_total(purchase, formset)
                purchase.save()
                _set_formset_stock_user(formset, request.user)
                formset.save()
        except ValidationError as exc:
            _add_stock_error(form, exc)
        else:
            messages.success(request, "Bon d'achat mis à jour avec succès.")
            return redirect('purchase_list')
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': _("Modifier l'achat"), 'quick_product_form': QuickProductForm(prefix='quick')})


@manager_required
def purchase_delete(request, pk):
    purchase = get_object_or_404(Purchase, pk=pk)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                purchase._stock_user = request.user
                purchase.delete()
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except ProtectedError:
            messages.error(request, _('Suppression impossible : cet achat possède des règlements.'))
        else:
            messages.success(request, "Bon d'achat supprimé.")
        return redirect('purchase_list')
    return render(request, 'commerce/purchase_confirm_delete.html', {'purchase': purchase})


def _payment_context(document, document_type):
    return {
        'document': document,
        'document_type': document_type,
        'payments': document.payments.select_related('created_by').order_by('-created_at', '-pk'),
        'paid_total': document.amount_paid,
        'due_total': document.balance_due,
        'payment_status': document.payment_status,
    }


@permission_required('commerce.view_sale')
def sale_payment_list(request, pk):
    sale = get_object_or_404(visible_sales(request.user, Sale.objects.select_related('client')), pk=pk)
    return render(request, 'commerce/payment_list.html', _payment_context(sale, 'sale'))


@permission_required('commerce.view_purchase')
def purchase_payment_list(request, pk):
    purchase = get_object_or_404(Purchase.objects.select_related('supplier'), pk=pk)
    return render(request, 'commerce/payment_list.html', _payment_context(purchase, 'purchase'))


def _payment_create(request, document, document_type):
    form = PaymentForm(request.POST or None, document=document)
    if form.is_valid():
        request_token = form.cleaned_data.get('request_token') or None
        existing = Payment.objects.filter(idempotency_key=request_token).first() if request_token else None
        if existing is not None:
            route = 'sale_payment_list' if document_type == 'sale' else 'purchase_payment_list'
            return redirect(route, pk=document.pk)
        payment = form.save(commit=False)
        payment.created_by = request.user
        payment.idempotency_key = request_token
        try:
            payment.save()
        except IntegrityError:
            if request_token and Payment.objects.filter(idempotency_key=request_token).exists():
                route = 'sale_payment_list' if document_type == 'sale' else 'purchase_payment_list'
                return redirect(route, pk=document.pk)
            raise
        except ValidationError as exc:
            for error in exc.messages:
                form.add_error('amount' if 'amount' in getattr(exc, 'message_dict', {}) else None, error)
        else:
            messages.success(request, _('Règlement enregistré avec succès.'))
            route = 'sale_payment_list' if document_type == 'sale' else 'purchase_payment_list'
            return redirect(route, pk=document.pk)
    return render(
        request,
        'commerce/payment_form.html',
        {
            'form': form,
            'document': document,
            'document_type': document_type,
            'due_total': document.balance_due,
        },
    )


@permission_required('commerce.change_sale')
def sale_payment_create(request, pk):
    return _payment_create(request, get_object_or_404(visible_sales(request.user), pk=pk), 'sale')


@permission_required('commerce.change_purchase')
def purchase_payment_create(request, pk):
    return _payment_create(request, get_object_or_404(Purchase, pk=pk), 'purchase')


def _payment_delete(request, payment, document_type):
    document = payment.sale if document_type == 'sale' else payment.purchase
    form = PaymentCancellationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        payment.cancel(user=request.user, reason=form.cleaned_data['reason'])
        messages.success(request, _('Règlement annulé avec traçabilité.'))
        route = 'sale_payment_list' if document_type == 'sale' else 'purchase_payment_list'
        return redirect(route, pk=document.pk)
    return render(
        request,
        'commerce/payment_confirm_delete.html',
        {
            'payment': payment, 'document': document,
            'document_type': document_type, 'form': form,
        },
    )


@permission_required('commerce.cancel_customerpayment')
def sale_payment_delete(request, sale_pk, pk):
    payment = get_object_or_404(Payment.objects.select_related('sale'), pk=pk, sale_id=sale_pk)
    return _payment_delete(request, payment, 'sale')


@permission_required('commerce.change_purchase')
def purchase_payment_delete(request, purchase_pk, pk):
    payment = get_object_or_404(Payment.objects.select_related('purchase'), pk=pk, purchase_id=purchase_pk)
    return _payment_delete(request, payment, 'purchase')


@permission_required('commerce.view_receivables')
def receivable_list(request):
    clients = receivables_queryset(request.user)
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', 'debtors')
    if query:
        clients = clients.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) | Q(tax_number__icontains=query)
        )
    clients = clients.annotate(
        receivable_balance=ExpressionWrapper(
            F('receivable_invoiced') - F('receivable_paid'),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )
    if status == 'settled':
        clients = clients.filter(receivable_invoice_count__gt=0, receivable_balance__lte=0)
    elif status == 'all':
        pass
    else:
        status = 'debtors'
        clients = clients.filter(receivable_balance__gt=0)
    clients = clients.order_by('-receivable_balance', 'name', 'pk')
    page_obj, pagination_query = paginate_queryset(request, clients, per_page=25)
    attach_receivable_counts(page_obj.object_list)
    return render(request, 'commerce/receivable_list.html', {
        'page_obj': page_obj,
        'pagination_query': pagination_query,
        'query': query,
        'status_filter': status,
    })


@permission_required('commerce.view_receivables')
def receivable_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    return render(
        request, 'commerce/receivable_detail.html',
        receivable_account(request.user, client),
    )


@permission_required('commerce.view_receivables')
def receivable_statement(request, pk):
    client = get_object_or_404(Client, pk=pk)
    return render(
        request, 'commerce/receivable_statement.html',
        receivable_account(request.user, client),
    )


@permission_required('commerce.add_customerpayment')
def receivable_payment_create(request, pk):
    client = get_object_or_404(Client, pk=pk)
    form = CustomerPaymentForm(request.POST or None, client=client, user=request.user)
    if form.is_valid():
        request_token = form.cleaned_data.get('request_token') or None
        existing = Payment.objects.filter(idempotency_key=request_token).first() if request_token else None
        if existing is None:
            payment = form.save(commit=False)
            payment.created_by = request.user
            payment.purchase = None
            payment.idempotency_key = request_token
            try:
                payment.save()
            except IntegrityError:
                if not request_token or not Payment.objects.filter(idempotency_key=request_token).exists():
                    raise
        messages.success(request, _('Règlement client enregistré.'))
        return redirect('receivable_detail', pk=client.pk)
    return render(request, 'commerce/receivable_payment_form.html', {
        'client': client, 'form': form,
    })


def _initialize_payment_tracking(document, document_type):
    with transaction.atomic():
        model = Sale if document_type == 'sale' else Purchase
        locked = model.objects.select_for_update().get(pk=document.pk)
        locked.payment_tracking_initialized = True
        locked.save(update_fields=['payment_tracking_initialized'])
    route = 'sale_payment_list' if document_type == 'sale' else 'purchase_payment_list'
    return redirect(route, pk=document.pk)


@require_POST
@permission_required('commerce.change_sale')
def sale_payment_tracking_initialize(request, pk):
    return _initialize_payment_tracking(get_object_or_404(visible_sales(request.user), pk=pk), 'sale')


@require_POST
@permission_required('commerce.change_purchase')
def purchase_payment_tracking_initialize(request, pk):
    return _initialize_payment_tracking(get_object_or_404(Purchase, pk=pk), 'purchase')


@seller_required
def sale_invoice_pdf(request, pk):
    sale = get_object_or_404(visible_sales(request.user, Sale.objects.select_related('client').prefetch_related('lines__product')), pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=facture_{sale.invoice_number}.pdf'
    generate_invoice_pdf(response, sale)
    return response


@seller_required
def sale_invoice_preview(request, pk):
    sale = get_object_or_404(visible_sales(request.user, Sale.objects.select_related('client').prefetch_related('lines__product')), pk=pk)
    context = build_invoice_context(sale)
    context['auto_print'] = request.GET.get('print') == '1'
    return render(request, 'commerce/sale_invoice_preview.html', context)


@seller_required
def sale_ticket_preview(request, pk, width):
    sale = get_object_or_404(visible_sales(request.user, Sale.objects.select_related('client').prefetch_related('lines__product')), pk=pk)
    ensure_ticket_number(sale)
    ticket_width = '58' if str(width) == '58' else '80'
    context = build_invoice_context(sale)
    context.update({
        'ticket_width': ticket_width,
        'auto_print': request.GET.get('print') == '1',
            'cashier_name': (
                (sale.created_by.get_full_name() or sale.created_by.get_username())
                if sale.created_by_id else (request.user.get_full_name() or request.user.get_username())
            ),
        'qr_code_data_uri': qr_code_data_uri(
            sale, context['total_ttc'], client_name=context['invoice_client'].name,
        ),
    })
    return render(request, 'commerce/sale_ticket.html', context)
