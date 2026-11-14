from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.template.loader import render_to_string

from apps.api.serializers import CompanySettingsSerializer, SaleSerializer
from apps.commerce.models import Sale
from apps.commerce.services import create_sale, refresh_invoice_snapshot, update_sale
from apps.commerce.utils import build_invoice_context
from apps.core.models import CompanySettings
from apps.inventory.models import Client, Product, Unit
from apps.printing.services import invoice_print_data


class ShareCapitalInvoiceSnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, _ = CompanySettings.objects.update_or_create(
            pk=1, defaults={
                'company_name': 'Entreprise historique capital',
                'share_capital': Decimal('1250000.50'),
            },
        )
        cls.customer = Client.objects.create(name='Client capital')
        cls.unit = Unit.objects.create(name='Unité capital')
        cls.product = Product.objects.create(
            name='Produit capital', unit=cls.unit, purchase_price=Decimal('10.00'),
            sale_price=Decimal('25.00'), quantity=100,
        )

    def create_invoice(self):
        return create_sale(
            client=self.customer,
            lines=[{'product': self.product, 'quantity': 2, 'unit_price': Decimal('25.00')}],
        )

    def test_creation_captures_optional_zero_and_positive_capital(self):
        for capital in (None, Decimal('0.00'), Decimal('1250000.50')):
            with self.subTest(capital=capital):
                self.company.share_capital = capital
                self.company.save(update_fields=['share_capital'])
                sale = self.create_invoice()
                sale.refresh_from_db()
                self.assertEqual(sale.company_share_capital_snapshot, capital)
                self.assertEqual(build_invoice_context(sale)['company'].share_capital, capital)
                expected = None if capital is None else str(capital)
                api = SaleSerializer(sale).data
                self.assertEqual(api['company_details']['share_capital'], expected)
                self.assertEqual(api['historical_document']['company']['share_capital'], expected)

    def test_new_off_invoice_without_company_does_not_invent_branding(self):
        CompanySettings.objects.all().delete()
        sale = create_sale(
            client=self.customer,
            show_legal_information=False,
            lines=[{'product': self.product, 'quantity': 2, 'unit_price': Decimal('25.00')}],
        )
        self.assertEqual(sale.company_name_snapshot, '')
        self.assertEqual(sale.company_logo_snapshot, '')
        self.assertIsNone(sale.company_share_capital_snapshot)
        for width in (58, 80):
            with self.subTest(width=width):
                payload = invoice_print_data(sale, paper_width=width, language='fr')
                self.assertEqual(payload['company'], {'name_fr': '', 'logo_url': None})
                context = build_invoice_context(sale)
                context['ticket_width'] = str(width)
                html = render_to_string('commerce/sale_ticket.html', context)
                main = html.split('<main class="ticket">', 1)[1].split('</main>', 1)[0]
                self.assertNotIn('El Amine', main)
                self.assertNotIn('company-name', main)
                self.assertNotIn('<img ', main)
                self.assertIn('Produit capital', main)
                self.assertIn('TOTAL GÉNÉRAL', main)

    def test_company_change_or_clear_does_not_change_issued_capital(self):
        sale = self.create_invoice()
        for current in (Decimal('9999999.99'), None):
            with self.subTest(current=current):
                self.company.share_capital = current
                self.company.save(update_fields=['share_capital'])
                sale.refresh_from_db()
                self.assertEqual(sale.company_share_capital_snapshot, Decimal('1250000.50'))
                self.assertEqual(build_invoice_context(sale)['company'].share_capital, Decimal('1250000.50'))
                self.assertEqual(SaleSerializer(sale).data['company_details']['share_capital'], '1250000.50')

    def test_preexisting_certified_snapshot_remains_unknown_without_backfill(self):
        sale = Sale.objects.create(
            invoice_number='CAPITAL-CERTIFIED-OLD', client=self.customer,
            total=Decimal('0.00'), snapshot_version=1,
            snapshot_integrity_status=Sale.SNAPSHOT_COMPLETE,
            company_name_snapshot='Entreprise certifiée ancienne',
        )
        self.assertIsNone(sale.company_share_capital_snapshot)
        self.assertIsNone(build_invoice_context(sale)['company'].share_capital)
        data = SaleSerializer(sale).data
        self.assertIsNone(data['company_details']['share_capital'])
        self.assertTrue(data['historical_document']['is_certified'])
        self.assertEqual(data['historical_document']['version'], 1)

    def test_legacy_render_does_not_add_live_capital_or_mutate_cached_company(self):
        sale = Sale.objects.create(
            invoice_number='CAPITAL-LEGACY-OLD', client=self.customer, total=Decimal('0.00'),
        )
        context = build_invoice_context(sale)
        self.assertIsNone(context['company'].share_capital)
        self.assertTrue(context['snapshot_warning'])
        request = RequestFactory().get('/api/v1/invoices/')
        request._company_settings = self.company
        data = SaleSerializer(sale, context={'request': request}).data
        self.assertIsNone(data['company_details']['share_capital'])
        self.assertIsNone(data['historical_document']['company']['share_capital'])
        self.assertFalse(data['historical_document']['is_certified'])
        self.assertEqual(request._company_settings.share_capital, Decimal('1250000.50'))
        self.assertEqual(CompanySettingsSerializer(request._company_settings).data['share_capital'], '1250000.50')
        self.company.refresh_from_db()
        self.assertEqual(self.company.share_capital, Decimal('1250000.50'))

    def test_explicit_edits_refresh_capital_with_existing_snapshot_policy(self):
        sale = self.create_invoice()
        self.company.share_capital = Decimal('9000000.00')
        self.company.company_name = 'Entreprise actuelle capital'
        self.company.save(update_fields=['share_capital', 'company_name'])
        update_sale(sale=sale, client=self.customer, lines=None)
        sale.refresh_from_db()
        self.assertEqual(sale.company_share_capital_snapshot, Decimal('9000000.00'))
        self.assertEqual(sale.company_name_snapshot, 'Entreprise actuelle capital')
        self.assertEqual(sale.snapshot_version, 1)
        self.assertEqual(sale.snapshot_integrity_status, Sale.SNAPSHOT_COMPLETE)
        for capital in (Decimal('0.00'), None):
            with self.subTest(capital=capital):
                self.company.share_capital = capital
                self.company.save(update_fields=['share_capital'])
                refresh_invoice_snapshot(sale)
                sale.refresh_from_db()
                self.assertEqual(sale.company_share_capital_snapshot, capital)
                self.assertEqual(build_invoice_context(sale)['company'].share_capital, capital)
                self.assertEqual(sale.snapshot_version, 1)
                self.assertEqual(sale.snapshot_integrity_status, Sale.SNAPSHOT_COMPLETE)

    def test_editing_old_snapshot_captures_current_capital_without_certifying_legacy(self):
        for status in (Sale.SNAPSHOT_LEGACY_UNKNOWN, Sale.SNAPSHOT_COMPLETE):
            with self.subTest(status=status):
                sale = Sale.objects.create(
                    invoice_number=f'CAPITAL-EDIT-OLD-{status}', client=self.customer,
                    total=Decimal('0.00'), snapshot_version=0 if status == Sale.SNAPSHOT_LEGACY_UNKNOWN else 1,
                    snapshot_integrity_status=status,
                )
                update_sale(sale=sale, client=self.customer, lines=None)
                sale.refresh_from_db()
                self.assertEqual(sale.company_share_capital_snapshot, Decimal('1250000.50'))
                self.assertEqual(sale.snapshot_integrity_status, status)
                self.assertEqual(build_invoice_context(sale)['company'].share_capital, Decimal('1250000.50'))
                self.assertEqual(SaleSerializer(sale).data['company_details']['share_capital'], '1250000.50')

    def test_deliberately_edited_legacy_capital_is_stable_without_certifying_identity(self):
        sale = Sale.objects.create(
            invoice_number='CAPITAL-EDITED-LEGACY-READ', client=self.customer,
            total=Decimal('0.00'), snapshot_integrity_status=Sale.SNAPSHOT_LEGACY_UNKNOWN,
        )
        update_sale(sale=sale, client=self.customer, lines=None)
        request = RequestFactory().get('/api/v1/invoices/')
        request._company_settings = self.company
        for current in (Decimal('9999999.99'), None):
            with self.subTest(current=current):
                self.company.share_capital = current
                self.company.save(update_fields=['share_capital'])
                sale.refresh_from_db()
                context = build_invoice_context(sale)
                data = SaleSerializer(sale, context={'request': request}).data
                self.assertEqual(context['company'].share_capital, Decimal('1250000.50'))
                self.assertEqual(data['company_details']['share_capital'], '1250000.50')
                self.assertEqual(data['historical_document']['company']['share_capital'], '1250000.50')
                self.assertFalse(data['historical_document']['is_certified'])
                self.assertTrue(context['snapshot_warning'])
                self.assertEqual(sale.snapshot_integrity_status, Sale.SNAPSHOT_LEGACY_UNKNOWN)
                self.assertEqual(sale.snapshot_version, 0)
                self.assertEqual(request._company_settings.share_capital, current)
        CompanySettings.objects.all().delete()
        self.assertEqual(build_invoice_context(sale)['company'].share_capital, Decimal('1250000.50'))
        self.assertEqual(SaleSerializer(sale).data['company_details']['share_capital'], '1250000.50')

    def test_legacy_without_company_never_invents_api_or_thermal_identity(self):
        from apps.printing.services import invoice_print_data

        CompanySettings.objects.all().delete()
        for capital in (None, Decimal('77.50')):
            with self.subTest(capital=capital):
                sale = Sale.objects.create(
                    invoice_number=f'CAPITAL-NO-COMPANY-LEGACY-{capital}',
                    client=self.customer, total=Decimal('0.00'),
                    show_legal_information=False,
                    company_share_capital_snapshot=capital,
                )
                data = SaleSerializer(sale).data
                self.assertEqual(data['company_details']['company_name'], '')
                self.assertIsNone(data['company_details']['logo_url'])
                self.assertFalse(data['historical_document']['is_certified'])
                expected = None if capital is None else str(capital)
                self.assertEqual(data['company_details']['share_capital'], expected)
                for width in (58, 80):
                    self.assertEqual(invoice_print_data(sale, paper_width=width)['company'], {
                        'name_fr': '', 'logo_url': None,
                    })
