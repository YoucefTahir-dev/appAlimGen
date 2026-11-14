from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.api.serializers import CompanySettingsSerializer
from apps.core.models import CompanySettings


class ShareCapitalSerializerTests(SimpleTestCase):
    def test_optional_nullable_decimal_contract(self):
        for value, expected in ((None, None), ('', None), ('0.00', Decimal('0.00')), ('12345.67', Decimal('12345.67'))):
            with self.subTest(value=value):
                serializer = CompanySettingsSerializer(data={'share_capital': value}, partial=True)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.validated_data['share_capital'], expected)
        omitted = CompanySettingsSerializer(data={}, partial=True)
        self.assertTrue(omitted.is_valid(), omitted.errors)
        self.assertNotIn('share_capital', omitted.validated_data)

    def test_negative_nonfinite_and_excess_precision_are_rejected(self):
        for value in ('-0.01', 'NaN', 'Infinity', '-Infinity', '1.001', '1000000000000.00'):
            with self.subTest(value=value):
                serializer = CompanySettingsSerializer(data={'share_capital': value}, partial=True)
                self.assertFalse(serializer.is_valid())
                self.assertIn('share_capital', serializer.errors)

    def test_decimal_response_is_a_string_and_null_remains_null(self):
        for value, expected in ((None, None), (Decimal('0.00'), '0.00'), (Decimal('12345.67'), '12345.67')):
            with self.subTest(value=value):
                company = CompanySettings(share_capital=value)
                self.assertEqual(CompanySettingsSerializer(company).data['share_capital'], expected)


class ShareCapitalApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='capital-api-admin', password='CapitalTests-strong-password',
        )
        cls.company, _ = CompanySettings.objects.update_or_create(
            pk=1, defaults={'company_name': 'Entreprise capital API', 'share_capital': None},
        )

    def setUp(self):
        self.client.force_authenticate(user=self.admin)

    @staticmethod
    def payload(response):
        return response.data.get('data', response.data)

    def test_get_patch_omission_zero_and_null_clear(self):
        url = reverse('api-company-settings')
        self.assertIsNone(self.payload(self.client.get(url))['share_capital'])
        for value in ('12345.67', '0.00'):
            with self.subTest(value=value):
                response = self.client.patch(url, {'share_capital': value}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(self.payload(response)['share_capital'], value)
                self.company.refresh_from_db()
                self.assertEqual(self.company.share_capital, Decimal(value))
        response = self.client.patch(url, {'phone': '0555000000'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.share_capital, Decimal('0.00'))
        response = self.client.patch(url, {'share_capital': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(self.payload(response)['share_capital'])
        self.company.refresh_from_db()
        self.assertIsNone(self.company.share_capital)

    def test_put_accepts_null_and_invalid_values_do_not_mutate_settings(self):
        url = reverse('api-company-settings')
        response = self.client.put(url, {
            'company_name': 'Entreprise capital API', 'share_capital': '100.25',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for value in ('-0.01', 'NaN', 'Infinity', '1.001', '1000000000000.00'):
            with self.subTest(value=value):
                response = self.client.patch(url, {'share_capital': value}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.company.refresh_from_db()
                self.assertEqual(self.company.share_capital, Decimal('100.25'))
        response = self.client.put(url, {
            'company_name': 'Entreprise capital API', 'share_capital': None,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertIsNone(self.company.share_capital)

    def test_blank_json_clears_optional_capital_in_patch_and_put(self):
        url = reverse('api-company-settings')
        for method in (self.client.patch, self.client.put):
            with self.subTest(method=method.__name__):
                self.company.share_capital = Decimal('100.25')
                self.company.save(update_fields=['share_capital'])
                response = method(url, {
                    'company_name': 'Entreprise capital API', 'share_capital': '',
                }, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertIsNone(self.payload(response)['share_capital'])
                self.company.refresh_from_db()
                self.assertIsNone(self.company.share_capital)

    def test_capital_patch_preserves_unrelated_company_fields(self):
        self.company.phone = '0555000123'
        self.company.tax_number = 'NIF-CAPITAL-UNCHANGED'
        self.company.tax_rate = Decimal('19.00')
        self.company.save(update_fields=['phone', 'tax_number', 'tax_rate'])
        response = self.client.patch(reverse('api-company-settings'), {
            'share_capital': '500000.25',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.share_capital, Decimal('500000.25'))
        self.assertEqual(self.company.company_name, 'Entreprise capital API')
        self.assertEqual(self.company.phone, '0555000123')
        self.assertEqual(self.company.tax_number, 'NIF-CAPITAL-UNCHANGED')
        self.assertEqual(self.company.tax_rate, Decimal('19.00'))

    def test_view_only_user_cannot_patch_or_put_capital(self):
        reader = get_user_model().objects.create_user(username='capital-api-reader')
        reader.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='view_companysettings',
        ))
        self.client.force_authenticate(user=reader)
        url = reverse('api-company-settings')
        self.assertEqual(self.client.get(url).status_code, 200)
        for method in (self.client.patch, self.client.put):
            with self.subTest(method=method.__name__):
                response = method(url, {
                    'company_name': 'Modification interdite', 'share_capital': '999999.99',
                }, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                self.company.refresh_from_db()
                self.assertIsNone(self.company.share_capital)
                self.assertEqual(self.company.company_name, 'Entreprise capital API')
