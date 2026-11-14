import io
import json
from decimal import Decimal
from html import escape
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from reportlab.platypus import Paragraph
from rest_framework.test import APIClient

from apps.commerce.models import Sale
from apps.commerce.services import create_sale
from apps.commerce.utils import generate_invoice_pdf, money
from apps.core.models import CompanySettings
from apps.inventory.models import Client, Product, ProductPackaging, Unit
from apps.printing.services import invoice_print_data


@override_settings(LANGUAGE_CODE='fr', STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ThermalBrandingRenderingTests(TestCase):
    def setUp(self):
        media = TemporaryDirectory(prefix='thermal-branding-test-')
        self.addCleanup(media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.user = get_user_model().objects.create_superuser(
            username='CAISSIER-SECRET-THERMIQUE', password='local-test-only',
        )
        self.client.force_login(self.user)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.company, _ = CompanySettings.objects.update_or_create(pk=1, defaults={
            'company_name': 'Société officielle & distribution',
            'address': 'ADRESSE-ENTREPRISE-SECRET',
            'phone': 'TEL-ENTREPRISE-SECRET',
            'email': 'secret-company@example.test',
            'tax_number': 'NIF-ENTREPRISE-SECRET',
            'nis': 'NIS-ENTREPRISE-SECRET',
            'rc_number': 'RC-ENTREPRISE-SECRET',
            'article_number': 'AI-ENTREPRISE-SECRET',
            'share_capital': Decimal('1234567.89'),
            'logo': '',
        })
        self.customer = Client.objects.create(
            name='CLIENT-SECRET-THERMIQUE', phone='TEL-CLIENT-SECRET',
            address='ADRESSE-CLIENT-SECRET', tax_number='NIF-CLIENT-SECRET',
            nis='NIS-CLIENT-SECRET', article_number='AI-CLIENT-SECRET',
            trade_register_number='RC-CLIENT-SECRET',
        )
        self.product = Product.objects.create(
            name='Produit thermique', unit=Unit.objects.create(name='Pièce thermique'),
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price='10.00', sale_price='25.00',
            super_wholesale_price='25.00', wholesale_price='25.00',
            retail_price='25.00', quantity=500,
        )
        self.packaging = ProductPackaging.objects.create(
            product=self.product, name='Pack thermique de 6',
            conversion_factor=6, default_sale_price='150.00',
        )

    def _logo(self, filename='official-brand.png'):
        image = io.BytesIO()
        Image.new('RGB', (40, 20), '#163b2f').save(image, format='PNG')
        self.company.logo.save(filename, ContentFile(image.getvalue()), save=True)
        return self.company.logo.url

    def _sale(self, *, visible=False):
        return create_sale(
            client=self.customer, user=self.user, discount='5.00', tax_rate='19.00',
            show_legal_information=visible,
            lines=[
                {'product': self.product, 'packaging': self.packaging, 'quantity': 2, 'unit_price': '150.00'},
                {'product': self.product, 'quantity': 3, 'unit_price': '25.00'},
            ],
        )

    def _html(self, sale, width, *, api=False, auto_print=False):
        if api:
            response = self.api.get(reverse('api-invoice-ticket', args=[sale.pk]), {'width': width})
        else:
            response = self.client.get(
                reverse('sale_ticket_preview', args=[sale.pk, width]),
                {'print': '1'} if auto_print else {},
            )
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def _main(self, html):
        return html.split('<main class="ticket">', 1)[1].split('</main>', 1)[0]

    def _assert_private_information_absent(self, main, sale):
        for secret in (
            sale.invoice_number, sale.ticket_number, self.user.username,
            self.customer.name, self.customer.phone, self.customer.address,
            self.customer.tax_number, self.customer.nis, self.customer.article_number,
            self.customer.trade_register_number, self.company.address, self.company.phone,
            self.company.email, self.company.tax_number, self.company.nis,
            self.company.rc_number, self.company.article_number,
            '1234567', 'Capital social', 'Caissier', 'Heure',
            '<span>Date</span>', '<span>Facture</span>', '<span>Ticket</span>',
            'Paiement', 'TVA', 'Remise', 'Total HT', 'Total TTC',
            'data:image/png;base64', 'Merci pour votre visite', 'À bientôt',
            'snapshot', 'company-ar',
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, main)

    def test_off_web_and_api_keep_only_official_branding_items_and_total_for_both_widths(self):
        logo_url = self._logo()
        sale = self._sale()
        for width in (58, 80):
            for api in (False, True):
                with self.subTest(width=width, api=api):
                    html = self._html(sale, width, api=api)
                    main = self._main(html)
                    header = main.split('<header class="center ticket-branding">', 1)[1].split('</header>', 1)[0]
                    self.assertEqual(header.count('<img '), 1)
                    self.assertIn(f'src="{logo_url}"', header)
                    self.assertIn(escape(self.company.company_name), header)
                    self.assertLess(header.index('<img '), header.index('class="company-name"'))
                    self.assertNotIn('static/img/logo.png', main)
                    self.assertEqual(main.count('class="separator"'), 1)
                    self.assertIn('Produit thermique', main)
                    self.assertIn('Pack thermique de 6', main)
                    self.assertIn('Pièce thermique', main)
                    for cell in ('>2</td>', '>3</td>', '150,00', '300,00', '25,00', '75,00'):
                        self.assertIn(cell, main)
                    self.assertIn('TOTAL GÉNÉRAL', main)
                    self.assertIn('441,25 DZD', main)
                    self.assertIn(f'size: {width}mm auto', html)
                    self._assert_private_information_absent(main, sale)

            payload = invoice_print_data(sale, paper_width=width, language='fr')
            self.assertEqual(payload['company'], {
                'name_fr': self.company.company_name, 'logo_url': logo_url,
            })
            self.assertEqual(payload['totals'], {'total_ttc': '441.25'})
            self.assertFalse(payload['show_ticket_information'])
            self.assertFalse(payload['show_legal_information'])
            self.assertFalse(payload['show_complete_customer_details'])
            self.assertEqual([item['quantity'] for item in payload['items']], [2, 3])
            self.assertEqual([item['stock_quantity'] for item in payload['items']], [12, 3])
            self.assertEqual([item['total'] for item in payload['items']], ['300.00', '75.00'])
            self.assertEqual(set(payload), {
                'language', 'paper_width', 'characters_per_line', 'show_legal_information',
                'show_ticket_information', 'show_complete_customer_details', 'company', 'items', 'totals',
            })
            self.assertNotIn(self.company.tax_number, json.dumps(payload))

    def test_off_without_logo_omits_image_and_wraps_actual_long_name(self):
        self.company.company_name = 'Distribution alimentaire très longue ' * 3 + 'X' * 70
        self.company.save(update_fields=['company_name'])
        sale = self._sale()
        for width in (58, 80):
            html = self._html(sale, width)
            main = self._main(html)
            self.assertIn(escape(self.company.company_name), main)
            self.assertNotIn('<img ', main)
            self.assertNotIn('static/img/logo.png', main)
            self.assertIn('overflow-wrap: anywhere', html)
            self.assertEqual(invoice_print_data(sale, paper_width=width)['company'], {
                'name_fr': self.company.company_name, 'logo_url': None,
            })

    def test_off_branding_uses_snapshot_not_later_company_identity(self):
        original_logo = self._logo()
        sale = self._sale()
        original_name = self.company.company_name
        self.company.company_name = 'ENTREPRISE-MODIFIEE-APRES-VENTE'
        self.company.save(update_fields=['company_name'])
        changed_logo = self._logo('replacement.png')
        for width in (58, 80):
            main = self._main(self._html(sale, width))
            self.assertIn(escape(original_name), main)
            self.assertIn(original_logo, main)
            self.assertNotIn(self.company.company_name, main)
            self.assertNotIn(changed_logo, main)
            self.assertEqual(invoice_print_data(sale, paper_width=width)['company'], {
                'name_fr': original_name, 'logo_url': original_logo,
            })

    def test_off_without_company_does_not_invent_branding(self):
        sale = self._sale()
        sale.snapshot_integrity_status = Sale.SNAPSHOT_LEGACY_UNKNOWN
        sale.save(update_fields=['snapshot_integrity_status'])
        CompanySettings.objects.all().delete()
        for width in (58, 80):
            main = self._main(self._html(sale, width))
            self.assertNotIn('<header', main)
            self.assertNotIn('<img ', main)
            self.assertNotIn('El Amine', main)
            self.assertNotIn('snapshot', main)
            self.assertEqual(invoice_print_data(sale, paper_width=width)['company'], {
                'name_fr': '', 'logo_url': None,
            })

    def test_off_long_unbroken_product_and_large_amounts_are_not_truncated(self):
        long_name = 'PRODUIT' * 35
        self.product.name = long_name
        self.product.save(update_fields=['name'])
        sale = create_sale(
            client=self.customer, user=self.user, show_legal_information=False,
            lines=[{'product': self.product, 'quantity': 3, 'unit_price': '1234567890.12'}],
        )
        for width in (58, 80):
            html = self._html(sale, width)
            main = self._main(html)
            self.assertIn(long_name, main)
            self.assertIn('1234567890,12', main)
            self.assertIn('3703703670,36 DZD', main)
            self.assertIn('class="minimal-items"', main)
            self.assertIn('class="row total minimal-total"', main)
            self.assertIn('table-layout: fixed', html)
            self.assertIn('padding-left: 1mm', html)
            self.assertNotIn('text-overflow: ellipsis', html)
            self.assertNotIn('overflow: hidden', html)
            payload = invoice_print_data(sale, paper_width=width)
            self.assertEqual(payload['items'][0]['name'], long_name)
            self.assertEqual(payload['items'][0]['unit_price'], '1234567890.12')
            self.assertEqual(payload['totals'], {'total_ttc': '3703703670.36'})

    def test_off_auto_print_and_preview_have_identical_ticket_content(self):
        self._logo()
        sale = self._sale()
        for width in (58, 80):
            preview = self._html(sale, width)
            printable = self._html(sale, width, auto_print=True)
            self.assertEqual(self._main(preview), self._main(printable))
            self.assertIn('js/auto-print.js', printable)
            self.assertNotIn('js/auto-print.js', preview)

    def test_on_retains_legal_header_metadata_customer_qr_and_existing_totals(self):
        logo_url = self._logo()
        sale = self._sale(visible=True)
        for width in (58, 80):
            main = self._main(self._html(sale, width))
            for value in (
                logo_url, escape(self.company.company_name), self.company.address,
                self.company.phone, self.company.tax_number, self.company.nis,
                self.company.rc_number, self.company.article_number,
                sale.invoice_number, sale.ticket_number, self.user.username,
                self.customer.name, self.customer.tax_number, self.customer.nis,
                self.customer.article_number, self.customer.trade_register_number,
                'TVA', 'Remise', 'Paiement', 'data:image/png;base64', 'Merci pour votre visite',
            ):
                self.assertIn(value, main)
            self.assertNotIn('ticket-branding', main)
            self.assertNotIn('Capital social', main)
            payload = invoice_print_data(sale, paper_width=width, language='fr')
            self.assertTrue(payload['show_ticket_information'])
            self.assertEqual(payload['company']['tax_number'], self.company.tax_number)
            self.assertEqual(payload['company']['logo_url'], logo_url)
            self.assertEqual(payload['customer']['name'], self.customer.name)
            self.assertIn('qr_code', payload)
            self.assertIn('messages', payload)
            self.assertNotIn('share_capital', payload['company'])

    def test_off_does_not_hide_official_invoice_html_or_pdf_including_known_zero_capital(self):
        self._logo()
        for capital in (Decimal('0.00'), Decimal('1234567.89'), None):
            with self.subTest(capital=capital):
                self.company.share_capital = capital
                self.company.save(update_fields=['share_capital'])
                sale = self._sale()
                response = self.client.get(reverse('sale_invoice_preview', args=[sale.pk]))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode('utf-8')
                pdf = io.BytesIO()
                with patch('apps.commerce.utils.Paragraph', wraps=Paragraph) as paragraphs:
                    generate_invoice_pdf(pdf, sale)
                self.assertTrue(pdf.getvalue().startswith(b'%PDF'))
                pdf_text = '\n'.join(str(call.args[0]) for call in paragraphs.call_args_list)
                for value in (sale.invoice_number, self.company.tax_number, self.customer.tax_number):
                    self.assertIn(value, html)
                    self.assertIn(value, pdf_text)
                if capital is None:
                    self.assertNotIn('Capital social', html)
                    self.assertNotIn('Capital social', pdf_text)
                else:
                    self.assertIn('Capital social', html)
                    self.assertIn(f'{capital:.2f}'.replace('.', ',') + ' DZD', html)
                    self.assertIn(f'Capital social : {money(capital)}', pdf_text)
