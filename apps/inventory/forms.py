from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _
from apps.core.security import validate_excel_upload
from apps.commerce.widgets import ProductAutocomplete
from .models import Brand, Client, LoadingOrder, LoadingOrderLine, Product, ProductPackaging, StockMovement, Supplier

class ProductForm(forms.ModelForm):
    barcode_display = forms.CharField(
        label=_('Code-barres'),
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )
    brand_text = forms.CharField(
        label=_('Marque'),
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Product
        fields = [
            'name',
            'purchase_price',
            'super_wholesale_price',
            'wholesale_price',
            'retail_price',
            'quantity',
            'minimum_stock',
            'description',
            'photo',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'super_wholesale_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'wholesale_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'retail_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
            'minimum_stock': forms.NumberInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        data = args[0] if args else kwargs.get('data')
        if data is not None and 'sale_price' in data and not any(
            field_name in data
            for field_name in ('super_wholesale_price', 'wholesale_price', 'retail_price')
        ):
            mutable_data = data.copy()
            for field_name in ('super_wholesale_price', 'wholesale_price', 'retail_price'):
                mutable_data[field_name] = data.get('sale_price')
            if args:
                args = (mutable_data, *args[1:])
            else:
                kwargs['data'] = mutable_data
        super().__init__(*args, **kwargs)
        self.fields['barcode_display'].initial = self.instance.barcode if self.instance and self.instance.pk else _('Généré automatiquement')
        if self.instance and self.instance.pk and self.instance.brand:
            self.fields['brand_text'].initial = self.instance.brand.name

    def save(self, commit=True):
        brand_name = self.cleaned_data.get('brand_text', '').strip()
        if brand_name:
            self.instance.brand = Brand.objects.resolve(brand_name)
        else:
            self.instance.brand = None
        return super().save(commit=commit)

    def clean_purchase_price(self):
        purchase_price = self.cleaned_data['purchase_price']
        if purchase_price < 0:
            raise ValidationError(_("Le prix d'achat ne peut pas être négatif."))
        return purchase_price


class LoadingOrderForm(forms.ModelForm):
    class Meta:
        model = LoadingOrder
        fields = ('operator', 'notes')
        widgets = {
            'operator': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operator'].queryset = self.fields['operator'].queryset.filter(is_active=True).order_by('username')


class LoadingOrderLineForm(forms.ModelForm):
    quantity = forms.IntegerField(
        min_value=1,
        error_messages={'min_value': _('La quantité doit être strictement positive.')},
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].error_messages['invalid_choice'] = _(
            'Veuillez sélectionner un produit dans les résultats proposés.'
        )

    class Meta:
        model = LoadingOrderLine
        fields = ('product', 'quantity')
        widgets = {
            'product': ProductAutocomplete(attrs={'search_context': 'loading_order'}),
        }


class BaseLoadingOrderLineFormSet(BaseInlineFormSet):
    default_error_messages = {
        **BaseInlineFormSet.default_error_messages,
        'too_few_forms': _('Le bon de chargement doit contenir au moins un produit.'),
    }

    def clean(self):
        selected_product_ids = [
            form.cleaned_data['product'].pk for form in self.forms
            if getattr(form, 'cleaned_data', None)
            and not form.cleaned_data.get('DELETE')
            and form.cleaned_data.get('product')
        ]
        duplicate_product = len(selected_product_ids) != len(set(selected_product_ids))
        try:
            super().clean()
        except ValidationError:
            if duplicate_product:
                raise ValidationError(_('Ce produit est déjà présent dans le bon de chargement.'))
            if not selected_product_ids:
                raise ValidationError(_('Le bon de chargement doit contenir au moins un produit.'))
            raise
        if any(self.errors):
            if not selected_product_ids:
                raise ValidationError(_('Le bon de chargement doit contenir au moins un produit.'))
            return

        active_lines = [
            form.cleaned_data for form in self.forms
            if getattr(form, 'cleaned_data', None)
            and not form.cleaned_data.get('DELETE')
            and form.cleaned_data.get('product')
        ]
        if not active_lines:
            raise ValidationError(_('Le bon de chargement doit contenir au moins un produit.'))

        if duplicate_product:
            raise ValidationError(_('Ce produit est déjà présent dans le bon de chargement.'))


LoadingOrderLineFormSet = inlineformset_factory(
    LoadingOrder, LoadingOrderLine, form=LoadingOrderLineForm,
    formset=BaseLoadingOrderLineFormSet,
    extra=0, can_delete=True, min_num=1, validate_min=True,
)

class QuickProductForm(ProductForm):
    """Reuse catalogue validation; purchases provide the stock separately."""

    class Meta(ProductForm.Meta):
        fields = [
            'name', 'purchase_price', 'super_wholesale_price',
            'wholesale_price', 'retail_price',
        ]


class ProductPackagingForm(forms.ModelForm):
    class Meta:
        model = ProductPackaging
        fields = ('name', 'conversion_factor', 'default_sale_price', 'barcode', 'is_active')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'conversion_factor': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'default_sale_price': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': '0.01'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


ProductPackagingFormSet = inlineformset_factory(
    Product,
    ProductPackaging,
    form=ProductPackagingForm,
    extra=1,
    can_delete=True,
)

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['name', 'phone', 'address', 'wilaya', 'customer_type', 'email', 'tax_number', 'balance', 'notes',
                  'latitude', 'longitude', 'location_accuracy', 'formatted_address', 'place_id']
        widgets = {
            'latitude': forms.HiddenInput(),
            'longitude': forms.HiddenInput(),
            'location_accuracy': forms.HiddenInput(),
            'formatted_address': forms.HiddenInput(),
            'place_id': forms.HiddenInput(),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'wilaya': forms.TextInput(attrs={'class': 'form-control'}),
            'customer_type': forms.Select(attrs={'class': 'form-select'}),
            'email': forms.HiddenInput(),
            'tax_number': forms.TextInput(attrs={'class': 'form-control'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        data = args[0] if args else kwargs.get('data')
        if data is not None and 'customer_type' not in data:
            mutable_data = data.copy()
            mutable_data['customer_type'] = Client.CustomerType.RETAIL
            if args:
                args = (mutable_data, *args[1:])
            else:
                kwargs['data'] = mutable_data
        super().__init__(*args, **kwargs)

class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'address', 'wilaya', 'email', 'rc_number', 'tax_number', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'wilaya': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'rc_number': forms.TextInput(attrs={'class': 'form-control'}),
            'tax_number': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ['product', 'movement_type', 'quantity', 'reason']
        help_texts = {
            'quantity': _(
                'Entrée/sortie : nombre d’unités. Ajustement : stock physique final compté.'
            ),
        }
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'movement_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'reason': forms.TextInput(attrs={'class': 'form-control'}),
        }

class ImportExcelForm(forms.Form):
    file = forms.FileField(
        label=_('Fichier Excel'),
        validators=[validate_excel_upload],
        widget=forms.FileInput(attrs={'class': 'form-control'}),
    )
