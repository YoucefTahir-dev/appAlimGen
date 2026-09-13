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
        if value:
            try:
                product = self.choices.queryset.filter(pk=value).only('name').first()
                if product:
                    context['widget']['label'] = product.name
            except (ValueError, TypeError, ValidationError):
                pass
        return context
