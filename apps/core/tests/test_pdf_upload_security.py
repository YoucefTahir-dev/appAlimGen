import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter
from pypdf.actions import JavaScript
from reportlab.pdfgen import canvas
from rest_framework.test import APITestCase

from apps.core.security import validate_receipt_upload
from apps.expenses.models import Expense, ExpenseCategory


def reportlab_pdf(*, pages=1, form=False, padding=0):
    output = io.BytesIO()
    document = canvas.Canvas(output)
    for page in range(pages):
        document.drawString(40, 800, f'Justificatif fictif {page + 1}')
        if form and page == 0:
            document.acroForm.textfield(name='reference', x=40, y=740, width=200, height=24)
        if padding:
            document.setAuthor('A' * padding)
        document.showPage()
    document.save()
    return output.getvalue()


def writer_pdf(*, encrypted=False, javascript=False, attachment=False):
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    if encrypted:
        writer.encrypt('fictitious-password')
    if javascript:
        writer.add_open_action(JavaScript("app.alert('test')"))
    if attachment:
        writer.add_attachment('payload.txt', b'fictitious attachment')
    writer.write(output)
    return output.getvalue()


def upload(payload, *, name='receipt.pdf', content_type='application/pdf'):
    return SimpleUploadedFile(name, payload, content_type=content_type)


class ReceiptPdfValidationTests(TestCase):
    def assert_rejected(self, payload, **kwargs):
        with self.assertRaises(ValidationError):
            validate_receipt_upload(upload(payload, **kwargs))

    def test_accepts_reportlab_pdf_and_multiple_pages(self):
        validate_receipt_upload(upload(reportlab_pdf(pages=3)))

    def test_accepts_standard_acroform_without_active_actions(self):
        validate_receipt_upload(upload(reportlab_pdf(form=True)))

    def test_accepts_large_valid_pdf_within_configured_limit(self):
        payload = reportlab_pdf(padding=750_000)
        self.assertGreater(len(payload), 700_000)
        validate_receipt_upload(upload(payload))

    def test_rejects_fake_signature_and_leading_whitespace(self):
        self.assert_rejected(b'')
        self.assert_rejected(b'%PDF-1.7\nnot a pdf\n%%EOF\n')
        self.assert_rejected(b'<html>not a pdf</html>')
        self.assert_rejected(b' \n' + reportlab_pdf())

    def test_rejects_truncated_pdf_and_trailing_polyglot_payload(self):
        payload = reportlab_pdf()
        self.assert_rejected(payload[:-8])
        self.assert_rejected(payload + b'PK\x03\x04polyglot')

    def test_rejects_encrypted_pdf(self):
        self.assert_rejected(writer_pdf(encrypted=True))

    def test_rejects_javascript_and_embedded_files(self):
        self.assert_rejected(writer_pdf(javascript=True))
        self.assert_rejected(writer_pdf(attachment=True))

    @override_settings(MAX_RECEIPT_PDF_PAGES=2)
    def test_rejects_pdf_over_page_limit(self):
        self.assert_rejected(reportlab_pdf(pages=3))

    @override_settings(MAX_RECEIPT_PDF_OBJECTS=2)
    def test_rejects_pdf_over_object_limit(self):
        self.assert_rejected(reportlab_pdf())

    @override_settings(MAX_RECEIPT_UPLOAD_SIZE=1024)
    def test_rejects_pdf_over_byte_limit_without_parsing_it(self):
        self.assert_rejected(reportlab_pdf())

    def test_rejects_extension_and_mime_mismatches(self):
        payload = reportlab_pdf()
        self.assert_rejected(payload, name='receipt.exe')
        self.assert_rejected(payload, content_type='text/html')

    def test_accepts_pdf_generated_by_erp_and_download_is_attachment(self):
        user = get_user_model().objects.create_superuser(
            username='erp-pdf-admin', email='erp-pdf@example.invalid', password='StrongPass123!'
        )
        category = ExpenseCategory.objects.create(name='Export fictif')
        Expense.objects.create(
            category=category,
            description='Charge fictive',
            amount='12.50',
            payment_method=Expense.CASH,
            created_by=user,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('expense_export_pdf'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        validate_receipt_upload(upload(response.content))


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(prefix='erp-pdf-upload-tests-'),
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class ReceiptPdfEndpointTests(APITestCase):
    password = 'StrongPass123!'

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='pdf-upload-admin', email='pdf@example.invalid', password=cls.password
        )
        cls.category = ExpenseCategory.objects.create(name='PDF fictif')

    def setUp(self):
        self.client.force_authenticate(self.user)

    def expense_payload(self, receipt):
        return {
            'date': '2026-11-16',
            'category': self.category.pk,
            'description': 'Justificatif fictif',
            'amount': '10.00',
            'payment_method': Expense.CASH,
            'observation': '',
            'receipt': receipt,
        }

    def test_api_rejects_active_pdf_without_saving_expense(self):
        response = self.client.post(
            reverse('api-expense-list'),
            self.expense_payload(upload(writer_pdf(javascript=True))),
            format='multipart',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Expense.objects.exists())

    def test_api_accepts_safe_pdf_and_uses_opaque_storage_name(self):
        response = self.client.post(
            reverse('api-expense-list'),
            self.expense_payload(upload(reportlab_pdf(), name='supplier-secret-name.pdf')),
            format='multipart',
        )

        self.assertEqual(response.status_code, 201, response.data)
        receipt_name = Expense.objects.get().receipt.name
        self.assertRegex(receipt_name, r'^expense-receipts/[0-9a-f]{32}\.pdf$')
        self.assertNotIn('supplier-secret-name', receipt_name)

    def test_api_upload_still_requires_expense_permission(self):
        unauthorized = get_user_model().objects.create_user(
            username='pdf-no-permission', password=self.password
        )
        self.client.force_authenticate(unauthorized)

        response = self.client.post(
            reverse('api-expense-list'),
            self.expense_payload(upload(reportlab_pdf())),
            format='multipart',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Expense.objects.exists())

    def test_web_form_rejects_active_pdf(self):
        self.client.force_authenticate(None)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('expense_create'),
            self.expense_payload(upload(writer_pdf(javascript=True))),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'contenu actif')
        self.assertFalse(Expense.objects.exists())
