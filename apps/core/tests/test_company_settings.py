from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.core.models import CompanySettings


class CompanySettingsWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='company-settings-admin', password='StrongPass123!',
        )
        cls.company, _created = CompanySettings.objects.update_or_create(
            pk=1,
            defaults={
                'company_name': 'El Amine ERP',
                'tax_number': '002610028552077',
                'nis': '002610010000282',
                'rc_number': '10/00-0285520 B26',
                'article_number': '10018109008',
                'tax_rate': '20.00',
            },
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_company_settings_are_visible_and_prefilled(self):
        detail = self.client.get(reverse('company_settings'))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '002610028552077')
        self.assertContains(detail, '002610010000282')
        self.assertContains(detail, '10/00-0285520 B26')
        self.assertContains(detail, '10018109008')
        self.assertContains(detail, reverse('company_settings_update'))

        edit = self.client.get(reverse('company_settings_update'))
        self.assertEqual(edit.status_code, 200)
        self.assertContains(edit, 'value="002610028552077"')
        self.assertContains(edit, 'value="002610010000282"')
        self.assertContains(edit, 'value="10/00-0285520 B26"')
        self.assertContains(edit, 'value="10018109008"')

    def test_company_settings_can_be_updated_from_web(self):
        response = self.client.post(reverse('company_settings_update'), {
            'company_name': 'Nouvelle entreprise',
            'address': 'Alger',
            'phone': '0555000000',
            'email': 'contact@example.com',
            'tax_number': 'NIF-NEW',
            'nis': 'NIS-NEW',
            'rc_number': 'RC-NEW',
            'article_number': 'AI-NEW',
            'tax_rate': '19.00',
        })

        self.assertRedirects(response, reverse('company_settings'))
        self.company.refresh_from_db()
        self.assertEqual(self.company.company_name, 'Nouvelle entreprise')
        self.assertEqual(self.company.tax_number, 'NIF-NEW')
        self.assertEqual(self.company.nis, 'NIS-NEW')
        self.assertEqual(self.company.rc_number, 'RC-NEW')
        self.assertEqual(self.company.article_number, 'AI-NEW')
        self.assertEqual(str(self.company.tax_rate), '19.00')

    def test_view_only_user_cannot_change_company_settings(self):
        user = get_user_model().objects.create_user(
            username='company-settings-reader', password='StrongPass123!',
        )
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='view_companysettings',
        ))
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('company_settings')).status_code, 200)
        self.assertEqual(self.client.get(reverse('company_settings_update')).status_code, 403)
