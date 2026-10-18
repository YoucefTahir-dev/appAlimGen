from django import forms
from django.core.exceptions import ValidationError


class ProductAutocomplete(forms.TextInput):
    template_name = 'commerce/widgets/product_autocomplete.html'
    input_type = 'hidden'
    # Composite control must render once among visible fields, not hidden_fields.
    is_hidden = False

    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        if not value and data.get(name + '_search', '').strip():
            return 'unselected'
        return value

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['label'] = ''
        context['widget']['stock'] = ''
        if value:
            try:
                product = self.choices.queryset.filter(pk=value).only('name', 'quantity').first()
                if product:
                    context['widget']['label'] = product.name
                    context['widget']['stock'] = product.quantity
            except (ValueError, TypeError, ValidationError):
                pass
        return context


class ClientAutocomplete(forms.TextInput):
    template_name = 'commerce/widgets/client_autocomplete.html'
    input_type = 'hidden'
    is_hidden = False

    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        if not value and data.get(name + '_search', '').strip():
            return 'unselected'
        return value

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['label'] = ''
        if value:
            try:
                client = self.choices.queryset.filter(pk=value).only(
                    'name', 'phone', 'tax_number',
                ).first()
                if client:
                    details = client.phone or client.tax_number
                    context['widget']['label'] = (
                        f'{client.name} — {details}' if details else client.name
                    )
            except (ValueError, TypeError, ValidationError):
                pass
        return context
