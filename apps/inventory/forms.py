from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _
from apps.core.security import validate_excel_upload
from apps.commerce.widgets import ProductAutocomplete
from .models import Brand, Client, LoadingOrder, LoadingOrderLine, Product, ProductPackaging, StockMovement, Supplier, Unit

class ProductForm(forms.ModelForm):
    STOCK_BASE = 'base'
    STOCK_CARTON = 'carton'
    stock_unit = forms.ChoiceField(
        label=_('Unité du stock saisi'),
        choices=((STOCK_BASE, _('Pièce')), (STOCK_CARTON, _('Carton'))),
        initial=STOCK_BASE,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
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
            'category',
            'unit_mode',
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
            'category': forms.Select(attrs={'class': 'form-select'}),
            'unit_mode': forms.Select(attrs={'class': 'form-select'}),
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
        provided_instance = kwargs.get('instance')
        self._original_unit_mode = (
            provided_instance.unit_mode
            if provided_instance is not None and provided_instance.pk
            else Product.UnitMode.PIECE_ONLY
        )
        if data is not None and ('unit_mode' not in data or 'stock_unit' not in data):
            mutable_data = data.copy()
            mutable_data.setdefault(
                'unit_mode',
                provided_instance.unit_mode if provided_instance is not None and provided_instance.pk
                else Product.UnitMode.PIECE_ONLY,
            )
            mutable_data.setdefault('stock_unit', self.STOCK_BASE)
            if args:
                args = (mutable_data, *args[1:])
            else:
                kwargs['data'] = mutable_data
            data = mutable_data
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
        # Category remains optional at database level and was optional in the
        # historical product form.  Keep that contract for old Web/API
        # payloads while still exposing the selector in the richer editor.
        if 'category' in self.fields:
            self.fields['category'].required = False
            self.fields['category'].label = _('Catégorie')
        if 'unit_mode' in self.fields and (
            not self.instance.pk or self._original_unit_mode != Product.UnitMode.LEGACY
        ):
            self.fields['unit_mode'].choices = [
                choice for choice in self.fields['unit_mode'].choices
                if choice[0] != Product.UnitMode.LEGACY
            ]
        if 'unit_mode' in self.fields and not self.is_bound and not self.instance.pk:
            self.initial['unit_mode'] = Product.UnitMode.PIECE_ONLY
        if (
            'stock_unit' in self.fields
            and self.instance.pk
            and self.instance.unit_mode == Product.UnitMode.LEGACY
        ):
            self.fields['stock_unit'].choices = (
                (self.STOCK_BASE, _('Unité de base historique')),
                (self.STOCK_CARTON, _('Carton')),
            )
        if 'quantity' in self.fields:
            self.fields['quantity'].label = _('Quantité de stock')
        if 'minimum_stock' in self.fields:
            self.fields['minimum_stock'].help_text = _('Toujours exprimé dans l’unité de base.')
        if 'barcode_display' in self.fields:
            self.fields['barcode_display'].initial = (
                self.instance.barcode if self.instance and self.instance.pk
                else _('Généré automatiquement')
            )
        if 'brand_text' in self.fields and self.instance and self.instance.pk and self.instance.brand:
            self.fields['brand_text'].initial = self.instance.brand.name

    def save(self, commit=True):
        brand_name = self.cleaned_data.get('brand_text', '').strip()
        if brand_name:
            self.instance.brand = Brand.objects.resolve(brand_name)
        else:
            self.instance.brand = None
        if (
            'unit_mode' in self.cleaned_data
            and self.cleaned_data.get('unit_mode') != Product.UnitMode.LEGACY
        ):
            self.instance.unit = Unit.objects.resolve('Pièce')
        return super().save(commit=commit)

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get('unit_mode')
        stock_unit = cleaned_data.get('stock_unit')
        if mode == Product.UnitMode.PIECE_ONLY and stock_unit == self.STOCK_CARTON:
            self.add_error('stock_unit', _('Le stock en cartons exige un mode autorisant le carton.'))
        if mode == Product.UnitMode.LEGACY and stock_unit == self.STOCK_CARTON:
            self.add_error('stock_unit', _('Confirmez d’abord l’unité historique de ce produit.'))

        if (
            self.instance.pk
            and self._original_unit_mode == Product.UnitMode.LEGACY
            and mode != Product.UnitMode.LEGACY
            and not self._legacy_product_is_safely_piece_based()
        ):
            self.add_error(
                'unit_mode',
                _(
                    'La signification historique de ce stock n’est pas confirmée. '
                    'Utilisez la future procédure de migration contrôlée.'
                ),
            )
        return cleaned_data

    def _legacy_product_is_safely_piece_based(self):
        unit_name = (self.instance.unit.name if self.instance.unit_id else '').strip().casefold()
        explicitly_piece = unit_name in {'pièce', 'piece', 'pieces', 'pièces'}
        has_history = (
            self.instance.movements.exists()
            or self.instance.saleline_set.exists()
            or self.instance.purchaseline_set.exists()
            or self.instance.loading_lines.exists()
            or self.instance.operator_stocks.exists()
        )
        return explicitly_piece or (self.instance.quantity == 0 and not has_history)

    def stock_quantity_in_base(self, carton_form=None):
        quantity = int(self.cleaned_data.get('quantity') or 0)
        if self.cleaned_data.get('stock_unit') != self.STOCK_CARTON:
            return quantity
        if carton_form is None or not carton_form.is_valid():
            raise ValidationError({'quantity': _('Le conditionnement carton est invalide.')})
        return quantity * int(carton_form.cleaned_data['conversion_factor'])

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The purchase quick-create contract has no stock or packaging input;
        # the purchase line remains the sole source of its stock movement.
        self.fields.pop('stock_unit', None)


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


class BaseOtherProductPackagingFormSet(BaseInlineFormSet):
    """Keep the legacy generic packaging editor without mixing in Carton."""

    def get_queryset(self):
        return super().get_queryset().exclude(kind=ProductPackaging.CARTON)


OtherProductPackagingFormSet = inlineformset_factory(
    Product,
    ProductPackaging,
    form=ProductPackagingForm,
    formset=BaseOtherProductPackagingFormSet,
    extra=1,
    can_delete=True,
)


class CartonConfigurationForm(forms.ModelForm):
    class Meta:
        model = ProductPackaging
        fields = (
            'conversion_factor', 'reference_purchase_price',
            'super_wholesale_price', 'wholesale_price', 'retail_price',
            'barcode',
        )
        widgets = {
            'conversion_factor': forms.NumberInput(attrs={'class': 'form-control', 'min': 2}),
            'reference_purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': '0.01'}),
            'super_wholesale_price': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': '0.01'}),
            'wholesale_price': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': '0.01'}),
            'retail_price': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': '0.01'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, required=False, **kwargs):
        self.carton_required = required
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.required = required and field_name != 'barcode'
        self.fields['conversion_factor'].label = _('Nombre de pièces par carton')
        self.fields['conversion_factor'].widget.attrs['min'] = 2
        self.fields['reference_purchase_price'].label = _('Prix d’achat carton')

    def clean(self):
        cleaned_data = super().clean()
        if not self.carton_required:
            return cleaned_data
        factor = cleaned_data.get('conversion_factor')
        if factor is not None and factor <= 1:
            self.add_error('conversion_factor', _('Un carton doit contenir plus d’une pièce.'))
        prices = [
            cleaned_data.get('super_wholesale_price'),
            cleaned_data.get('wholesale_price'),
            cleaned_data.get('retail_price'),
        ]
        if all(price is not None for price in prices) and not prices[0] <= prices[1] <= prices[2]:
            self.add_error(
                'retail_price',
                _('Les tarifs carton doivent respecter l’ordre : Super Gros ≤ Gros ≤ Détail.'),
            )
        self.instance.name = str(_('Carton'))
        self.instance.kind = ProductPackaging.CARTON
        self.instance.default_sale_price = cleaned_data.get('retail_price') or Decimal('0.00')
        self.instance.purchase_enabled = True
        self.instance.sale_enabled = True
        self.instance.is_active = True
        return cleaned_data

    def save_for(self, product):
        carton = self.save(commit=False)
        if not carton.pk:
            # A deployment may already contain a legacy generic packaging
            # literally named "Carton". Reuse it only when the manager
            # explicitly enables carton mode; do not create a duplicate or
            # reinterpret it during the additive migration.
            carton = ProductPackaging.objects.filter(
                product=product, name__iexact=str(_('Carton')),
            ).first() or carton
            for field_name in self.Meta.fields:
                setattr(carton, field_name, self.cleaned_data.get(field_name))
        carton.product = product
        carton.name = str(_('Carton'))
        carton.kind = ProductPackaging.CARTON
        carton.default_sale_price = self.cleaned_data['retail_price']
        carton.purchase_enabled = True
        carton.sale_enabled = True
        carton.is_active = True
        carton.save()
        return carton

    @staticmethod
    def disable_for(product):
        ProductPackaging.objects.filter(
            product=product, kind=ProductPackaging.CARTON,
        ).update(purchase_enabled=False, sale_enabled=False, is_active=False)

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['name', 'phone', 'address', 'wilaya', 'customer_type', 'email', 'tax_number',
                  'nis', 'article_number', 'trade_register_number', 'balance', 'notes',
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
            'nis': forms.TextInput(attrs={'class': 'form-control'}),
            'article_number': forms.TextInput(attrs={'class': 'form-control'}),
            'trade_register_number': forms.TextInput(attrs={'class': 'form-control'}),
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
