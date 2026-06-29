from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.inventory.forms import ImportExcelForm, ProductForm


class SecurityHardeningTests(TestCase):
    def test_security_headers_are_added(self):
        response = self.client.get(reverse('login'))

        self.assertIn('Content-Security-Policy', response)
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertEqual(response['Cross-Origin-Opener-Policy'], 'same-origin')
        self.assertEqual(response['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn('Permissions-Policy', response)

    @override_settings(SESSION_IDLE_TIMEOUT_SECONDS=1)
    def test_idle_session_is_expired(self):
        User = get_user_model()
        user = User.objects.create_user(username='idle_user', password='Pass12345!')
        self.client.force_login(user)
        session = self.client.session
        session['last_activity'] = int(timezone.now().timestamp()) - 120
        session.save()

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertTrue(AuditLog.objects.filter(action__icontains='inactivité', level=AuditLog.LEVEL_WARNING).exists())

    def test_failed_login_is_audited_without_user(self):
        response = self.client.post(reverse('login'), {'username': 'ghost', 'password': 'bad'})

        self.assertEqual(response.status_code, 200)
        log = AuditLog.objects.filter(action__icontains='Tentative de connexion échouée').latest('created_at')
        self.assertIsNone(log.user)
        self.assertEqual(log.status_code, 401)
        self.assertEqual(log.level, AuditLog.LEVEL_WARNING)

    def test_product_form_rejects_dangerous_upload(self):
        uploaded = SimpleUploadedFile('payload.php', b'<?php echo 1; ?>', content_type='application/x-php')
        form = ProductForm(
            data={
                'name': 'Produit upload',
                'brand_text': 'Marque',
                'purchase_price': '10.00',
                'sale_price': '15.00',
                'quantity': '1',
                'minimum_stock': '0',
                'description': '',
            },
            files={'photo': uploaded},
        )

        self.assertFalse(form.is_valid())
        self.assertIn('photo', form.errors)

    def test_excel_import_rejects_dangerous_upload(self):
        uploaded = SimpleUploadedFile('payload.exe', b'MZ', content_type='application/octet-stream')
        form = ImportExcelForm(files={'file': uploaded})

        self.assertFalse(form.is_valid())
        self.assertIn('file', form.errors)
