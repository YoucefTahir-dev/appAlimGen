from django import forms
from .models import Product, Client, Supplier, StockMovement, Brand

class ProductForm(forms.ModelForm):
    barcode_display = forms.CharField(
        label='Code-barres',
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )
    brand_text = forms.CharField(
        label='Marque',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Product
        fields = ['name', 'purchase_price', 'sale_price', 'quantity', 'minimum_stock', 'description', 'photo']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'sale_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
            'minimum_stock': forms.NumberInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['barcode_display'].initial = self.instance.barcode if self.instance and self.instance.pk else 'Généré automatiquement'
        if self.instance and self.instance.pk and self.instance.brand:
            self.fields['brand_text'].initial = self.instance.brand.name

    def save(self, commit=True):
        brand_name = self.cleaned_data.get('brand_text', '').strip()
        if brand_name:
            brand, _ = Brand.objects.get_or_create(name=brand_name)
            self.instance.brand = brand
        else:
            self.instance.brand = None
        return super().save(commit=commit)

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['name', 'phone', 'address', 'wilaya', 'email', 'tax_number', 'balance', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'wilaya': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'tax_number': forms.TextInput(attrs={'class': 'form-control'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

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
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'movement_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
            'reason': forms.TextInput(attrs={'class': 'form-control'}),
        }

class ImportExcelForm(forms.Form):
    file = forms.FileField(label='Fichier Excel', widget=forms.FileInput(attrs={'class': 'form-control'}))
