import io
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.export_security import excel_safe_text, pdf_safe_text
from apps.inventory.forms import ImportExcelForm, ProductForm
from apps.core.security import LOGIN_FAILURE_EVENT, get_client_ip, validate_receipt_upload


class SecurityHardeningTests(TestCase):
    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_client_ip_uses_the_proxy_appended_address(self):
        request = self.client.request().wsgi_request
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.10, 203.0.113.20'
        request.META['REMOTE_ADDR'] = '127.0.0.1'

        self.assertEqual(get_client_ip(request), '203.0.113.20')

    @override_settings(TRUST_X_FORWARDED_FOR=False)
    def test_client_ip_ignores_untrusted_forwarded_header(self):
        request = self.client.request().wsgi_request
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.10'
        request.META['REMOTE_ADDR'] = '127.0.0.1'

        self.assertEqual(get_client_ip(request), '127.0.0.1')

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_client_ip_does_not_fall_back_to_spoofed_forwarded_value(self):
        request = self.client.request().wsgi_request
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.10, invalid-value'
        request.META['REMOTE_ADDR'] = '127.0.0.1'

        self.assertEqual(get_client_ip(request), '127.0.0.1')

    def test_health_and_readiness_endpoints(self):
        health_response = self.client.get(reverse('health'))
        readiness_response = self.client.get(reverse('readiness'))

        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json(), {'status': 'ok'})
        self.assertEqual(readiness_response.status_code, 200)
        self.assertEqual(readiness_response.json(), {'status': 'ready'})
        self.assertEqual(health_response['Cache-Control'], 'no-store')

    def test_document_exports_escape_untrusted_content(self):
        self.assertEqual(excel_safe_text('=HYPERLINK("https://example.invalid")'), "'=HYPERLINK(\"https://example.invalid\")")
        self.assertEqual(excel_safe_text('+1+1'), "'+1+1")
        self.assertEqual(pdf_safe_text('<img src="file:///secret"/>'), '&lt;img src="file:///secret"/&gt;')

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
        log = AuditLog.objects.filter(action__startswith=LOGIN_FAILURE_EVENT).latest('created_at')
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

    def test_excel_import_rejects_spoofed_xlsx_content(self):
        uploaded = SimpleUploadedFile(
            'payload.xlsx',
            b'not-a-zip-workbook',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        form = ImportExcelForm(files={'file': uploaded})

        self.assertFalse(form.is_valid())
        self.assertIn('file', form.errors)

    def test_excel_import_rejects_invalid_xml_workbook(self):
        payload = io.BytesIO()
        with ZipFile(payload, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<invalid')
            archive.writestr('xl/workbook.xml', '<invalid')
        uploaded = SimpleUploadedFile(
            'payload.xlsx',
            payload.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        form = ImportExcelForm(files={'file': uploaded})

        self.assertFalse(form.is_valid())
        self.assertIn('file', form.errors)

    def test_receipt_rejects_spoofed_pdf_content(self):
        uploaded = SimpleUploadedFile('receipt.pdf', b'<script>alert(1)</script>', content_type='application/pdf')

        with self.assertRaises(ValidationError):
            validate_receipt_upload(uploaded)
