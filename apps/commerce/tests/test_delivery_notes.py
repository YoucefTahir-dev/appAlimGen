import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from apps.commerce.models import Payment, Sale, SaleLine
from apps.commerce.services import create_sale
from apps.commerce.utils import build_delivery_note_context, delivery_note_number
from apps.core.models import CompanySettings
from apps.inventory.models import (
    Category,
    Client,
    Product,
    ProductPackaging,
    StockMovement,
    Unit,
)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class DeliveryNoteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username='delivery-manager', password='StrongPass123!', role='manager',
        )
        cls.seller = user_model.objects.create_user(
            username='delivery-seller', password='StrongPass123!', role='seller',
        )
        cls.other_seller = user_model.objects.create_user(
            username='delivery-other', password='StrongPass123!', role='seller',
        )
        CompanySettings.objects.update_or_create(pk=1, defaults={
            'company_name': 'Entreprise historique BL',
            'address': '12 rue de la Liberté',
            'phone': '0555000010',
            'email': 'bl@example.test',
        })
        cls.category = Category.objects.create(name='Livraisons')
        cls.unit = Unit.objects.create(name='Pièce')
        cls.customer = Client.objects.create(
            name='Client Français الأمين',
            phone='0555000020', address='Alger centre',
            tax_number='NIF-BL-OLD',
        )

    def setUp(self):
        self.product = Product.objects.create(
            name='Café qualité ممتازة', category=self.category, unit=self.unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('10.00'), sale_price=Decimal('25.00'),
            super_wholesale_price=Decimal('22.00'),
            wholesale_price=Decimal('23.00'), retail_price=Decimal('25.00'),
            quantity=200,
        )
        self.carton = ProductPackaging.objects.create(
            product=self.product, name='Carton de 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price=Decimal('500.00'),
            retail_price=Decimal('500.00'), sale_enabled=True,
        )

    def create_invoice(self, *, user=None, pay=False):
        return create_sale(
            client=self.customer,
            lines=[
                {'product': self.product, 'packaging': self.carton, 'quantity': 2},
                {'product': self.product, 'quantity': 3},
            ],
            user=user or self.manager,
            initial_payment_amount=Decimal('10.00') if pay else None,
        )

    @staticmethod
    def pdf_text(response):
        reader = PdfReader(io.BytesIO(response.content))
        return reader, '\n'.join(page.extract_text() or '' for page in reader.pages)

    def test_preview_and_a4_pdf_contain_delivery_data_without_financial_data(self):
        sale = self.create_invoice(pay=True)
        self.client.force_login(self.manager)

        preview = self.client.get(reverse('sale_delivery_note_preview', args=[sale.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, f'BL-{sale.invoice_number}')
        self.assertContains(preview, sale.invoice_number)
        self.assertContains(preview, 'Client Français')
        self.assertContains(preview, 'الأمين')
        self.assertContains(preview, 'Café qualité')
        self.assertContains(preview, 'ممتازة')
        self.assertContains(preview, 'Carton de 24')
        self.assertContains(preview, 'Pièce')
        self.assertContains(preview, 'Signature du livreur')
        self.assertContains(preview, 'Signature du client')
        self.assertContains(preview, '@page { size: A4; margin: 0; }')
        for forbidden in ('Prix', 'Total HT', 'Total TTC', 'Règlement', 'Paiement', '500,00'):
            self.assertNotContains(preview, forbidden)

        pdf = self.client.get(reverse('sale_delivery_note_pdf', args=[sale.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        reader, text = self.pdf_text(pdf)
        compact_text = ' '.join(text.split())
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.28, places=1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 841.89, places=1)
        for expected in (
            'BON DE LIVRAISON', f'BL-{sale.invoice_number}', sale.invoice_number,
            'Client Fran', self.product.reference, 'Carton de 24',
            'Signature du livreur', 'Signature du client',
        ):
            self.assertIn(expected, compact_text)
        for forbidden in ('Prix', 'Total HT', 'Total TTC', 'Règlement', 'Paiement', '500.00'):
            self.assertNotIn(forbidden, text)

    def test_number_is_stable_and_repeated_generation_is_read_only(self):
        sale = self.create_invoice(pay=True)
        self.client.force_login(self.manager)
        before = {
            'sales': Sale.objects.count(),
            'lines': SaleLine.objects.count(),
            'payments': Payment.objects.count(),
            'movements': StockMovement.objects.count(),
            'quantity': Product.objects.get(pk=self.product.pk).quantity,
            'total': sale.total,
        }

        expected_number = f'BL-{sale.invoice_number}'
        self.assertEqual(delivery_note_number(sale), expected_number)
        for _index in range(2):
            self.assertEqual(
                self.client.get(reverse('sale_delivery_note_preview', args=[sale.pk])).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(reverse('sale_delivery_note_pdf', args=[sale.pk])).status_code,
                200,
            )

        sale.refresh_from_db()
        after = {
            'sales': Sale.objects.count(),
            'lines': SaleLine.objects.count(),
            'payments': Payment.objects.count(),
            'movements': StockMovement.objects.count(),
            'quantity': Product.objects.get(pk=self.product.pk).quantity,
            'total': sale.total,
        }
        self.assertEqual(after, before)
        self.assertEqual(delivery_note_number(sale), expected_number)

    def test_historical_snapshots_survive_reference_data_changes(self):
        sale = self.create_invoice()
        CompanySettings.objects.filter(pk=1).update(company_name='Entreprise actuelle')
        Client.objects.filter(pk=self.customer.pk).update(name='Client actuel')
        Product.objects.filter(pk=self.product.pk).update(name='Produit actuel')
        Unit.objects.filter(pk=self.unit.pk).update(name='Palette')
        sale = Sale.objects.select_related('client').prefetch_related(
            'lines__product__unit', 'lines__packaging',
        ).get(pk=sale.pk)

        context = build_delivery_note_context(sale)
        self.assertEqual(context['company_name_fr'], 'Entreprise historique BL')
        self.assertEqual(context['invoice_client'].name, 'Client Français الأمين')
        self.assertEqual(context['lines'][0].invoice_product_name, 'Café qualité ممتازة')
        self.assertEqual(context['lines'][1].invoice_unit_name, 'Pièce')

        self.client.force_login(self.manager)
        preview = self.client.get(reverse('sale_delivery_note_preview', args=[sale.pk]))
        for historical in ('Entreprise historique BL', 'Client Français', 'Café qualité'):
            self.assertContains(preview, historical)
        for current in ('Entreprise actuelle', 'Client actuel', 'Produit actuel', 'Palette'):
            self.assertNotContains(preview, current)

    def test_legacy_invoice_is_rendered_with_explicit_history_warning(self):
        sale = Sale.objects.create(
            invoice_number='FAC-LEGACY-BL-001', client=self.customer,
            total=Decimal('0.00'), created_by=self.manager,
        )
        self.client.force_login(self.manager)
        preview = self.client.get(reverse('sale_delivery_note_preview', args=[sale.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'Facture antérieure aux snapshots')
        pdf = self.client.get(reverse('sale_delivery_note_pdf', args=[sale.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_seller_cannot_access_another_sellers_delivery_note(self):
        sale = self.create_invoice(user=self.other_seller)
        self.client.force_login(self.seller)
        for route in ('sale_delivery_note_preview', 'sale_delivery_note_pdf'):
            response = self.client.get(reverse(route, args=[sale.pk]))
            self.assertEqual(response.status_code, 404)

    def test_invoice_permissions_protect_preview_and_download_separately(self):
        sale = self.create_invoice(user=self.seller)
        self.client.force_login(self.seller)
        preview_url = reverse('sale_delivery_note_preview', args=[sale.pk])
        pdf_url = reverse('sale_delivery_note_pdf', args=[sale.pk])
        self.assertEqual(self.client.get(preview_url).status_code, 200)
        self.assertEqual(self.client.get(pdf_url).status_code, 200)

        view_permission = Permission.objects.get(
            content_type__app_label='accounts', codename='view_invoices',
        )
        self.seller.denied_permissions.add(view_permission)
        self.assertEqual(self.client.get(preview_url).status_code, 403)
        self.assertEqual(self.client.get(pdf_url).status_code, 200)

        download_permission = Permission.objects.get(
            content_type__app_label='accounts', codename='download_invoice_pdf',
        )
        self.seller.denied_permissions.add(download_permission)
        self.assertEqual(self.client.get(pdf_url).status_code, 403)

    def test_print_mode_and_safe_attachment_filename(self):
        sale = self.create_invoice()
        Sale.objects.filter(pk=sale.pk).update(invoice_number='FAC/BL 2026:001')
        sale.refresh_from_db()
        self.client.force_login(self.manager)

        preview = self.client.get(
            reverse('sale_delivery_note_preview', args=[sale.pk]), {'print': '1'},
        )
        self.assertContains(preview, 'js/auto-print.js')
        self.assertContains(preview, 'data-print-page')
        pdf = self.client.get(reverse('sale_delivery_note_pdf', args=[sale.pk]))
        self.assertEqual(
            pdf['Content-Disposition'],
            'attachment; filename="BL-FAC_BL_2026_001.pdf"',
        )

    def test_sales_list_shows_responsive_delivery_note_action(self):
        sale = self.create_invoice()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('sale_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('sale_delivery_note_preview', args=[sale.pk]))
        self.assertContains(response, 'Bon de livraison')
        self.assertContains(response, 'table-actions')
