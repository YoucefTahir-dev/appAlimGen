from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from .forms import SaleForm, SaleLineFormSet, PurchaseForm, PurchaseLineFormSet
from .models import Sale, Purchase
from .utils import generate_invoice_pdf

@login_required
def sale_list(request):
    sales = Sale.objects.select_related('client').all()
    return render(request, 'commerce/sale_list.html', {'sales': sales})

@login_required
def purchase_list(request):
    purchases = Purchase.objects.select_related('supplier').all()
    return render(request, 'commerce/purchase_list.html', {'purchases': purchases})

@login_required
def sale_create(request):
    form = SaleForm(request.POST or None)
    formset = SaleLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        sale = form.save(commit=False)
        total = 0
        for line in formset:
            if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
                total += line.cleaned_data['quantity'] * line.cleaned_data['unit_price']
        tax_amount = total * (sale.tax_rate / 100)
        sale.total = total + tax_amount - sale.discount
        sale.save()
        formset.instance = sale
        formset.save()
        messages.success(request, 'Facture enregistrée avec succès.')
        return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': 'Nouvelle facture'})

@login_required
def purchase_create(request):
    form = PurchaseForm(request.POST or None)
    formset = PurchaseLineFormSet(request.POST or None, prefix='lines')
    if form.is_valid() and formset.is_valid():
        purchase = form.save(commit=False)
        total = 0
        for line in formset:
            if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
                total += line.cleaned_data['quantity'] * line.cleaned_data['purchase_price']
        tax_amount = total * (purchase.tax_rate / 100)
        purchase.total = total + tax_amount
        purchase.save()
        formset.instance = purchase
        formset.save()
        messages.success(request, 'Bon d\'achat enregistré avec succès.')
        return redirect('purchase_list')
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': 'Nouvel achat'})

@login_required
def sale_update(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    form = SaleForm(request.POST or None, instance=sale)
    formset = SaleLineFormSet(request.POST or None, instance=sale, prefix='lines')
    if form.is_valid() and formset.is_valid():
        sale = form.save(commit=False)
        total = 0
        for line in formset:
            if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
                total += line.cleaned_data['quantity'] * line.cleaned_data['unit_price']
        tax_amount = total * (sale.tax_rate / 100)
        sale.total = total + tax_amount - sale.discount
        sale.save()
        formset.save()
        messages.success(request, 'Facture mise à jour avec succès.')
        return redirect('sale_list')
    return render(request, 'commerce/sale_form.html', {'form': form, 'formset': formset, 'title': 'Modifier la facture'})

@login_required
def sale_delete(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method == 'POST':
        sale.delete()
        messages.success(request, 'Facture supprimée.')
        return redirect('sale_list')
    return render(request, 'commerce/sale_confirm_delete.html', {'sale': sale})

@login_required
def purchase_update(request, pk):
    purchase = get_object_or_404(Purchase, pk=pk)
    form = PurchaseForm(request.POST or None, instance=purchase)
    formset = PurchaseLineFormSet(request.POST or None, instance=purchase, prefix='lines')
    if form.is_valid() and formset.is_valid():
        purchase = form.save(commit=False)
        total = 0
        for line in formset:
            if line.cleaned_data and not line.cleaned_data.get('DELETE', False):
                total += line.cleaned_data['quantity'] * line.cleaned_data['purchase_price']
        tax_amount = total * (purchase.tax_rate / 100)
        purchase.total = total + tax_amount
        purchase.save()
        formset.save()
        messages.success(request, 'Bon d\'achat mis à jour avec succès.')
        return redirect('purchase_list')
    return render(request, 'commerce/purchase_form.html', {'form': form, 'formset': formset, 'title': 'Modifier l\'achat'})

@login_required
def purchase_delete(request, pk):
    purchase = get_object_or_404(Purchase, pk=pk)
    if request.method == 'POST':
        purchase.delete()
        messages.success(request, 'Bon d\'achat supprimé.')
        return redirect('purchase_list')
    return render(request, 'commerce/purchase_confirm_delete.html', {'purchase': purchase})

@login_required
def sale_invoice_pdf(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=facture_{sale.invoice_number}.pdf'
    generate_invoice_pdf(response, sale)
    return response
