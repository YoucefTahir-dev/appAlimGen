from django import forms
from django.utils.translation import gettext_lazy as _


PERIOD_CHOICES = [
    ("today", _("Aujourd'hui")),
    ("yesterday", _("Hier")),
    ("week", _("Cette semaine")),
    ("month", _("Ce mois")),
    ("year", _("Cette année")),
    ("custom", _("Période personnalisée")),
]


class DashboardPeriodForm(forms.Form):
    period = forms.ChoiceField(
        label=_("Période"),
        choices=PERIOD_CHOICES,
        initial="today",
        widget=forms.Select(attrs={"class": "form-select", "id": "dashboardPeriod"}),
    )
    start_date = forms.DateField(
        label=_("Date de début"),
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    end_date = forms.DateField(
        label=_("Date de fin"),
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("period") != "custom":
            return cleaned_data

        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date is None:
            self.add_error("start_date", _("La date de début est obligatoire pour une période personnalisée."))
        if end_date is None:
            self.add_error("end_date", _("La date de fin est obligatoire pour une période personnalisée."))
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", _("La date de fin doit être postérieure ou égale à la date de début."))
        return cleaned_data
