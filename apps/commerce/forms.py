from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _

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

    def clean_tax_rate(self):
        tax_rate = self.cleaned_data['tax_rate']
        if tax_rate < 0 or tax_rate > 100:
            raise ValidationError(_('Le taux de TVA doit être compris entre 0 et 100.'))
        return tax_rate


class SaleForm(forms.ModelForm):
    PAY_FULL = 'pay_full'
    NO_PAYMENT = 'no_payment'
    settlement_action = forms.ChoiceField(
        label=_('Règlement à l’enregistrement'),
        choices=(
            (PAY_FULL, _('Régler intégralement maintenant')),
            (NO_PAYMENT, _('Ne pas ajouter de règlement maintenant')),
        ),
        initial=PAY_FULL,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Sale
        fields = ['client', 'discount', 'tax_rate', 'payment_type']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'payment_type': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['settlement_action'].initial = self.NO_PAYMENT

    def clean_discount(self):
        discount = self.cleaned_data['discount']
        if discount < 0:
            raise ValidationError(_('La remise ne peut pas être négative.'))
        return discount

    def clean_tax_rate(self):
        tax_rate = self.cleaned_data['tax_rate']
        if tax_rate < 0 or tax_rate > 100:
            raise ValidationError(_('Le taux de TVA doit être compris entre 0 et 100.'))
        return tax_rate

    def clean_settlement_action(self):
        selected = self.cleaned_data.get('settlement_action')
        if selected:
            return selected
        return self.NO_PAYMENT if self.instance and self.instance.pk else self.PAY_FULL

    def clean_payment_type(self):
        """Keep legacy/API posts deterministic when the optional field is absent."""
        return self.cleaned_data.get('payment_type') or Sale.CASH


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'payment_type']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'payment_type': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, document=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.document = document
        if isinstance(document, Sale):
            self.instance.sale = document
            self.instance.purchase = None
            self.fields['payment_type'].initial = document.payment_type
        elif isinstance(document, Purchase):
            self.instance.purchase = document
            self.instance.sale = None
        if document is not None and not self.is_bound:
            self.fields['amount'].initial = document.balance_due

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise ValidationError(_('Le montant doit être strictement positif.'))
        if self.document is not None and amount > self.document.balance_due:
            raise ValidationError(
                _('Surpaiement interdit : le montant dépasse le solde restant.')
            )
        return amount


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
        quantity = cleaned_data.get('quantity')
        unit_price = cleaned_data.get('unit_price')

        if quantity is not None and quantity < 1:
            self.add_error('quantity', _('La quantité doit être strictement positive.'))
        if unit_price is not None and unit_price < 0:
            self.add_error('unit_price', _('Le prix unitaire ne peut pas être négatif.'))
        if product and unit_price is not None and unit_price < product.purchase_price:
            raise ValidationError(_("Le prix de vente est inférieur au coût d'achat. Vente refusée."))

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
                    _('Stock insuffisant pour ce produit : demandé %(required)s, disponible %(available)s.') % {
                        'required': required_quantity,
                        'available': available_quantity,
                    }
                )

        if errors:
            raise ValidationError(errors)


class PurchaseLineForm(forms.ModelForm):
    class Meta:
        model = PurchaseLine
        fields = ('product', 'quantity', 'purchase_price')
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
        }

    def clean_quantity(self):
        quantity = self.cleaned_data['quantity']
        if quantity < 1:
            raise ValidationError(_('La quantité doit être strictement positive.'))
        return quantity

    def clean_purchase_price(self):
        purchase_price = self.cleaned_data['purchase_price']
        if purchase_price < 0:
            raise ValidationError(_("Le prix d'achat ne peut pas être négatif."))
        return purchase_price


PurchaseLineFormSet = inlineformset_factory(
    Purchase,
    PurchaseLine,
    form=PurchaseLineForm,
    extra=1,
    can_delete=True,
)

SaleLineFormSet = inlineformset_factory(
    Sale,
    SaleLine,
    form=SaleLineForm,
    formset=BaseSaleLineFormSet,
    extra=1,
    can_delete=True,
)
