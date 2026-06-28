from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import Payment, Purchase, PurchaseLine, Sale, SaleLine


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
        fields = ['client', 'discount', 'tax_rate']
        widgets = {
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


class SaleLineForm(forms.ModelForm):
    class Meta:
        model = SaleLine
        fields = ('product', 'quantity', 'unit_price')
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        unit_price = cleaned_data.get('unit_price')

        if product and unit_price is not None and unit_price < product.purchase_price:
            raise ValidationError("Le prix de vente est inférieur au coût d'achat. Vente refusée.")

        return cleaned_data


class BaseSaleLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        required_by_product = {}
        available_by_product = {}

        for form in self.forms:
            cleaned_data = getattr(form, 'cleaned_data', None)
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue

            product = cleaned_data.get('product')
            quantity = cleaned_data.get('quantity') or 0
            if not product:
                continue

            required_by_product[product.pk] = required_by_product.get(product.pk, 0) + quantity
            if product.pk not in available_by_product:
                available_by_product[product.pk] = product.quantity

            if form.instance and form.instance.pk:
                old_line = SaleLine.objects.select_related('product').get(pk=form.instance.pk)
                if old_line.product_id == product.pk:
                    available_by_product[product.pk] += old_line.quantity

        errors = []
        for product_id, required_quantity in required_by_product.items():
            available_quantity = available_by_product.get(product_id, 0)
            if required_quantity > available_quantity:
                errors.append(
                    f'Stock insuffisant pour ce produit : demandé {required_quantity}, disponible {available_quantity}.'
                )

        if errors:
            raise ValidationError(errors)


PurchaseLineFormSet = inlineformset_factory(
    Purchase,
    PurchaseLine,
    fields=('product', 'quantity', 'purchase_price'),
    extra=1,
    can_delete=True,
    widgets={
        'product': forms.Select(attrs={'class': 'form-select'}),
        'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
        'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    },
)

SaleLineFormSet = inlineformset_factory(
    Sale,
    SaleLine,
    form=SaleLineForm,
    formset=BaseSaleLineFormSet,
    extra=1,
    can_delete=True,
)
