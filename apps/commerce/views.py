from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import manager_required, seller_required

from .forms import PurchaseForm, PurchaseLineFormSet, SaleForm, SaleLineFormSet
from .models import InvoiceSequence, Purchase, Sale
from .utils import build_invoice_context, generate_invoice_pdf


def generate_invoice_number():
    year = timezone.now().year
    sequence, _ = InvoiceSequence.objects.select_for_update().get_or_create(year=year)
    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])
    return f'FAC-{year}-{sequence.last_number:06d}'


def calculate_sale_total(sale, formset):
    total = 0
    for line in formset:
        if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
            total += line.cleaned_data['quantity'] * line.cleaned_data['unit_price']
    tax_amount = total * (sale.tax_rate / 100)
    return total + tax_amount - sale.discount


def calculate_purchase_total(purchase, formset):
    total = 0
    for line in formset:
        if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
            total += line.cleaned_data['quantity'] * line.cleaned_data['purchase_price']
    tax_amount = total * (purchase.tax_rate / 100)
    return total + tax_amount


@seller_required
def sale_list(request):
    sales = Sale.objects.select_related('client').order_by('-created_at', '-pk')
    return render(request, 'commerce/sale_list.html', {'sales': sales})


@manager_required
def purchase_list(request):
    purchases = Purchase.objects.select_related('supplier').order_by('-created_at', '-pk')
    return render(request, 'commerce/purchase_list.html', {'purchases': purchases})


@seller_required
def sale_create(request):
    form = SaleForm(request.POST or None)
    formset = SaleLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        with transaction.atomic():
            sale = form.save(commit=False)
            sale.invoice_number = generate_invoice_number()
            sale.total = calculate_sale_total(sale, formset)
            sale.save()
            formset.instance = sale
            formset.save()
        messages.success(request, 'Facture enregistrée avec succès.')
        return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': 'Nouvelle facture'})


@manager_required
def purchase_create(request):
    form = PurchaseForm(request.POST or None)
    formset = PurchaseLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        purchase = form.save(commit=False)
        purchase.total = calculate_purchase_total(purchase, formset)
        purchase.save()
        formset.instance = purchase
        formset.save()
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
        sale.save()
        formset.save()
        messages.success(request, 'Facture mise à jour avec succès.')
        return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': 'Modifier la facture'})


@manager_required
def sale_delete(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method == 'POST':
        sale.delete()
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
        purchase.total = calculate_purchase_total(purchase, formset)
        purchase.save()
        formset.save()
        messages.success(request, "Bon d'achat mis à jour avec succès.")
        return redirect('purchase_list')
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': "Modifier l'achat"})


@manager_required
def purchase_delete(request, pk):
    purchase = get_object_or_404(Purchase, pk=pk)
    if request.method == 'POST':
        purchase.delete()
        messages.success(request, "Bon d'achat supprimé.")
        return redirect('purchase_list')
    return render(request, 'commerce/purchase_confirm_delete.html', {'purchase': purchase})


@seller_required
def sale_invoice_pdf(request, pk):
    sale = get_object_or_404(Sale.objects.select_related('client').prefetch_related('lines__product', 'lines__packaging'), pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=facture_{sale.invoice_number}.pdf'
    generate_invoice_pdf(response, sale)
    return response


@seller_required
def sale_invoice_preview(request, pk):
    sale = get_object_or_404(Sale.objects.select_related('client').prefetch_related('lines__product', 'lines__packaging'), pk=pk)
    context = build_invoice_context(sale)
    context['auto_print'] = request.GET.get('print') == '1'
    return render(request, 'commerce/sale_invoice_preview.html', context)
