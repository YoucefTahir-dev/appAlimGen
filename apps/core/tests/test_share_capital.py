from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.core.forms import CompanySettingsForm
from apps.core.models import CompanySettings


class ShareCapitalValidationTests(SimpleTestCase):
    def test_capital_is_optional_and_has_no_monetary_default(self):
        field = CompanySettings._meta.get_field('share_capital')
        self.assertIsNone(CompanySettings().share_capital)
        self.assertFalse(field.has_default())
        self.assertIsNone(field.clean(None, CompanySettings()))

    def test_money_accepts_zero_and_two_decimal_places(self):
        field = CompanySettings._meta.get_field('share_capital')
        for value in ('0', '0.00', '123456.78', '999999999999.99'):
            with self.subTest(value=value):
                self.assertEqual(field.clean(value, CompanySettings()), Decimal(value))

    def test_money_rejects_negative_nonfinite_and_excess_precision(self):
        field = CompanySettings._meta.get_field('share_capital')
        for value in ('-0.01', 'NaN', 'Infinity', '-Infinity', '1.001', '1000000000000.00'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                field.clean(value, CompanySettings())

    def test_form_accepts_blank_zero_and_positive_money(self):
        for value, expected in (('', None), ('0.00', Decimal('0.00')), ('1200000.50', Decimal('1200000.50'))):
            with self.subTest(value=value):
                form = CompanySettingsForm(data={
                    'company_name': 'Entreprise capital', 'tax_rate': '19.00',
                    'share_capital': value,
                })
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['share_capital'], expected)

    def test_form_rejects_negative_capital_and_exposes_number_bounds(self):
        form = CompanySettingsForm(data={
            'company_name': 'Entreprise capital', 'tax_rate': '19.00',
            'share_capital': '-1.00',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('share_capital', form.errors)
        self.assertFalse(form.fields['share_capital'].required)
        self.assertEqual(form.fields['share_capital'].widget.attrs['min'], '0')
        self.assertEqual(form.fields['share_capital'].widget.attrs['step'], '0.01')


class ShareCapitalWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='capital-web-admin', password='CapitalTests-strong-password',
        )
        cls.company, _ = CompanySettings.objects.update_or_create(
            pk=1, defaults={'company_name': 'Entreprise capital', 'share_capital': None},
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_settings_save_prefill_and_clear_capital(self):
        for value, expected in (('12345.67', Decimal('12345.67')), ('0.00', Decimal('0.00')), ('', None)):
            with self.subTest(value=value):
                response = self.client.post(reverse('company_settings_update'), {
                    'company_name': 'Entreprise capital', 'tax_rate': '19.00',
                    'share_capital': value,
                })
                self.assertRedirects(response, reverse('company_settings'))
                self.company.refresh_from_db()
                self.assertEqual(self.company.share_capital, expected)
                detail = self.client.get(reverse('company_settings'))
                self.assertContains(detail, 'Capital social')
                if expected is not None:
                    self.assertContains(detail, 'DZD')
                edit = self.client.get(reverse('company_settings_update'))
                self.assertContains(edit, 'name="share_capital"', count=1)

    def test_negative_capital_is_not_saved(self):
        response = self.client.post(reverse('company_settings_update'), {
            'company_name': 'Entreprise capital', 'tax_rate': '19.00',
            'share_capital': '-0.01',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('share_capital', response.context['form'].errors)
        self.company.refresh_from_db()
        self.assertIsNone(self.company.share_capital)

    def test_unknown_capital_is_not_zero_and_explicit_zero_is_visible(self):
        detail = self.client.get(reverse('company_settings'))
        self.assertContains(detail, (
            '<dt class="col-sm-4">Capital social</dt>'
            '<dd class="col-sm-8">—</dd>'
        ), html=True)
        self.company.share_capital = Decimal('0.00')
        self.company.save(update_fields=['share_capital'])
        detail = self.client.get(reverse('company_settings'))
        self.assertRegex(detail.content.decode(), r'0[,.]00 DZD')

    def test_view_only_user_cannot_submit_capital_changes(self):
        reader = get_user_model().objects.create_user(username='capital-web-reader')
        reader.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='view_companysettings',
        ))
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse('company_settings')).status_code, 200)
        self.assertEqual(self.client.get(reverse('company_settings_update')).status_code, 403)
        response = self.client.post(reverse('company_settings_update'), {
            'company_name': 'Modification interdite', 'tax_rate': '19.00',
            'share_capital': '999999.99',
        })
        self.assertEqual(response.status_code, 403)
        self.company.refresh_from_db()
        self.assertIsNone(self.company.share_capital)
        self.assertEqual(self.company.company_name, 'Entreprise capital')
