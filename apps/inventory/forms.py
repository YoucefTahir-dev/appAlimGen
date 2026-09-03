from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _
from apps.core.security import validate_excel_upload
from .models import Product, ProductPackaging, Client, Supplier, StockMovement, Brand

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
        fields = ['name', 'phone', 'address', 'wilaya', 'customer_type', 'email', 'tax_number', 'balance', 'notes']
        widgets = {
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
