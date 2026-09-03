from django import forms

from .models import PrinterProfile


class PrinterProfileForm(forms.ModelForm):
    class Meta:
        model = PrinterProfile
        fields = (
            'name', 'description', 'printer_type', 'manufacturer', 'model_name',
            'connection_mode', 'local_identifier', 'ip_address', 'network_port',
            'paper_width', 'protocol', 'characters_per_line', 'encoding',
            'auto_print', 'is_default', 'is_active',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
