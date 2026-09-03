from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _

from apps.accounts.permissions import manager_required, permission_required, seller_required
from apps.core.pagination import paginate_queryset

from .forms import PaymentForm, PurchaseForm, PurchaseLineFormSet, SaleForm, SaleLineFormSet
from .models import Payment, Purchase, Sale
from .services import ensure_ticket_number, generate_invoice_number, generate_ticket_number
from .utils import build_invoice_context, generate_invoice_pdf, qr_code_data_uri


MONEY_QUANTUM = Decimal('0.01')


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
        paid_total=Coalesce(Sum('payments__amount'), Value(Decimal('0.00')), output_field=money_field)
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
    queryset = _with_payment_totals(Sale.objects.select_related('client')).order_by('-created_at', '-pk')
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
                    sale.invoice_number = generate_invoice_number()
                    sale.ticket_number = generate_ticket_number()
                    sale.payment_tracking_initialized = True
                    sale.created_by = request.user
                    sale.save()
                    formset.instance = sale
                    _set_formset_stock_user(formset, request.user)
                    formset.save()
                    if form.cleaned_data['settlement_action'] == SaleForm.PAY_FULL:
                        _record_remaining_sale_payment(sale, request.user)
            except ValidationError as exc:
                _add_stock_error(form, exc)
                sale.pk = None
                sale._state.adding = True
            else:
                messages.success(request, 'Facture enregistrée avec succès.')
                return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': _('Nouvelle facture')})


@manager_required
def purchase_create(request):
    form = PurchaseForm(request.POST or None)
    formset = PurchaseLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        purchase = form.save(commit=False)
        try:
            with transaction.atomic():
                purchase.total = calculate_purchase_total(purchase, formset)
                purchase.save()
                formset.instance = purchase
                _set_formset_stock_user(formset, request.user)
                formset.save()
        except ValidationError as exc:
            _add_stock_error(form, exc)
            purchase.pk = None
            purchase._state.adding = True
        else:
            messages.success(request, "Bon d'achat enregistré avec succès.")
            return redirect('purchase_list')
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': 'Nouvel achat'})


@manager_required
def sale_update(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
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
                    sale.payment_tracking_initialized = True
                    sale.save()
                    _set_formset_stock_user(formset, request.user)
                    formset.save()
                    if form.cleaned_data['settlement_action'] == SaleForm.PAY_FULL:
                        _record_remaining_sale_payment(sale, request.user)
            except ValidationError as exc:
                _add_stock_error(form, exc)
            else:
                messages.success(request, 'Facture mise à jour avec succès.')
                return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': _('Modifier la facture')})


@manager_required
def sale_delete(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
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
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': "Modifier l'achat"})


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
    sale = get_object_or_404(Sale.objects.select_related('client'), pk=pk)
    return render(request, 'commerce/payment_list.html', _payment_context(sale, 'sale'))


@permission_required('commerce.view_purchase')
def purchase_payment_list(request, pk):
    purchase = get_object_or_404(Purchase.objects.select_related('supplier'), pk=pk)
    return render(request, 'commerce/payment_list.html', _payment_context(purchase, 'purchase'))


def _payment_create(request, document, document_type):
    form = PaymentForm(request.POST or None, document=document)
    if form.is_valid():
        payment = form.save(commit=False)
        payment.created_by = request.user
        try:
            payment.save()
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
    return _payment_create(request, get_object_or_404(Sale, pk=pk), 'sale')


@permission_required('commerce.change_purchase')
def purchase_payment_create(request, pk):
    return _payment_create(request, get_object_or_404(Purchase, pk=pk), 'purchase')


def _payment_delete(request, payment, document_type):
    document = payment.sale if document_type == 'sale' else payment.purchase
    if request.method == 'POST':
        payment.delete()
        messages.success(request, _('Règlement supprimé.'))
        route = 'sale_payment_list' if document_type == 'sale' else 'purchase_payment_list'
        return redirect(route, pk=document.pk)
    return render(
        request,
        'commerce/payment_confirm_delete.html',
        {'payment': payment, 'document': document, 'document_type': document_type},
    )


@permission_required('commerce.change_sale')
def sale_payment_delete(request, sale_pk, pk):
    payment = get_object_or_404(Payment.objects.select_related('sale'), pk=pk, sale_id=sale_pk)
    return _payment_delete(request, payment, 'sale')


@permission_required('commerce.change_purchase')
def purchase_payment_delete(request, purchase_pk, pk):
    payment = get_object_or_404(Payment.objects.select_related('purchase'), pk=pk, purchase_id=purchase_pk)
    return _payment_delete(request, payment, 'purchase')


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
    return _initialize_payment_tracking(get_object_or_404(Sale, pk=pk), 'sale')


@require_POST
@permission_required('commerce.change_purchase')
def purchase_payment_tracking_initialize(request, pk):
    return _initialize_payment_tracking(get_object_or_404(Purchase, pk=pk), 'purchase')


@seller_required
def sale_invoice_pdf(request, pk):
    sale = get_object_or_404(Sale.objects.select_related('client').prefetch_related('lines__product'), pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=facture_{sale.invoice_number}.pdf'
    generate_invoice_pdf(response, sale)
    return response


@seller_required
def sale_invoice_preview(request, pk):
    sale = get_object_or_404(Sale.objects.select_related('client').prefetch_related('lines__product'), pk=pk)
    context = build_invoice_context(sale)
    context['auto_print'] = request.GET.get('print') == '1'
    return render(request, 'commerce/sale_invoice_preview.html', context)


@seller_required
def sale_ticket_preview(request, pk, width):
    sale = get_object_or_404(Sale.objects.select_related('client').prefetch_related('lines__product'), pk=pk)
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
        'qr_code_data_uri': qr_code_data_uri(sale, context['total_ttc']),
    })
    return render(request, 'commerce/sale_ticket.html', context)
