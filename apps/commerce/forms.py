from django import forms
from django.forms import inlineformset_factory
from .models import Purchase, PurchaseLine, Sale, SaleLine, Payment

class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ['reference', 'supplier', 'tax_rate']
        widgets = {
            'reference': forms.TextInput(attrs={'class': 'form-control'}),
            'supplier': forms.Select(attrs={'class': 'form-select'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ['invoice_number', 'client', 'discount', 'tax_rate']
        widgets = {
            'invoice_number': forms.TextInput(attrs={'class': 'form-control'}),
            'client': forms.Select(attrs={'class': 'form-select'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['reference', 'sale', 'purchase', 'amount', 'payment_type']
        widgets = {
            'reference': forms.TextInput(attrs={'class': 'form-control'}),
            'sale': forms.Select(attrs={'class': 'form-select'}),
            'purchase': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'payment_type': forms.Select(attrs={'class': 'form-select'}),
        }

PurchaseLineFormSet = inlineformset_factory(Purchase, PurchaseLine, fields=('product', 'quantity', 'purchase_price'), extra=1, can_delete=True, widgets={
    'product': forms.Select(attrs={'class': 'form-select'}),
    'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
    'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
})

SaleLineFormSet = inlineformset_factory(Sale, SaleLine, fields=('product', 'quantity', 'unit_price'), extra=1, can_delete=True, widgets={
    'product': forms.Select(attrs={'class': 'form-select'}),
    'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
    'unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
})
