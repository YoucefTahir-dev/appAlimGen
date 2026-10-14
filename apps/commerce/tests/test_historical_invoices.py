from decimal import Decimal

from django.template.loader import render_to_string
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from apps.api.serializers import SaleSerializer
from apps.commerce.models import Payment, Sale
from apps.commerce.services import create_sale, update_sale
from apps.commerce.utils import build_invoice_context
from apps.core.models import CompanySettings
from apps.inventory.models import Client, Product, Unit
from apps.printing.services import invoice_print_data


class HistoricalInvoiceSnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, _ = CompanySettings.objects.update_or_create(
            pk=1,
            defaults={
                'company_name': 'Entreprise historique',
                'address': 'Ancienne adresse entreprise',
                'phone': '0555000001',
                'email': 'historique@example.com',
                'rc_number': 'RC-OLD',
                'tax_number': 'NIF-OLD',
                'nis': 'NIS-OLD',
                'article_number': 'AI-OLD',
            },
        )
        cls.client_record = Client.objects.create(
            name='Client historique',
            phone='0555000002',
            address='Ancienne adresse client',
            tax_number='CLIENT-NIF-OLD',
            nis='CLIENT-NIS-OLD',
            article_number='CLIENT-AI-OLD',
            trade_register_number='CLIENT-RC-OLD',
            customer_type=Client.CustomerType.WHOLESALE,
        )
        cls.unit = Unit.objects.create(name='Carton')
        cls.product = Product.objects.create(
            name='Produit historique',
            unit=cls.unit,
            purchase_price=Decimal('10.00'),
            sale_price=Decimal('25.00'),
            wholesale_price=Decimal('25.00'),
            quantity=100,
        )

    def create_invoice(self):
        return create_sale(
            client=self.client_record,
            lines=[{
                'product': self.product,
                'quantity': 2,
                'unit_price': Decimal('25.00'),
            }],
            discount=Decimal('5.00'),
            tax_rate=Decimal('19.00'),
        )

    def mutate_reference_data(self):
        self.company.company_name = 'Entreprise actuelle'
        self.company.address = 'Nouvelle adresse entreprise'
        self.company.tax_number = 'NIF-NEW'
        self.company.save()
        self.client_record.name = 'Client actuel'
        self.client_record.address = 'Nouvelle adresse client'
        self.client_record.tax_number = 'CLIENT-NIF-NEW'
        self.client_record.save()
        self.product.name = 'Produit actuel'
        self.product.sale_price = Decimal('99.00')
        self.product.save()
        self.unit.name = 'Palette'
        self.unit.save()

    def test_creation_freezes_parties_lines_and_decimal_totals(self):
        sale = self.create_invoice()
        line = sale.lines.get()

        self.assertEqual(sale.snapshot_integrity_status, Sale.SNAPSHOT_COMPLETE)
        self.assertEqual(sale.snapshot_version, 1)
        self.assertIsNotNone(sale.issued_at)
        self.assertEqual(sale.currency_snapshot, 'DZD')
        self.assertEqual(sale.company_name_snapshot, 'Entreprise historique')
        self.assertEqual(sale.company_name_ar_snapshot, 'الأمين للمواد الغذائية و غير الغذائية')
        self.assertEqual(sale.client_name_snapshot, 'Client historique')
        self.assertEqual(sale.subtotal_ht_snapshot, Decimal('50.00'))
        self.assertEqual(sale.tax_amount_snapshot, Decimal('9.50'))
        self.assertEqual(sale.total, Decimal('54.50'))
        self.assertEqual(line.product_name_snapshot, 'Produit historique')
        self.assertEqual(line.product_reference_snapshot, self.product.reference)
        self.assertEqual(line.unit_name_snapshot, 'Carton')
        self.assertEqual(line.line_total_ht_snapshot, Decimal('50.00'))

    def test_reference_changes_do_not_change_any_invoice_representation(self):
        sale = self.create_invoice()
        self.mutate_reference_data()
        sale = Sale.objects.select_related('client').prefetch_related(
            'lines__product__unit', 'payments',
        ).get(pk=sale.pk)

        context = build_invoice_context(sale)
        self.assertEqual(context['company'].company_name, 'Entreprise historique')
        self.assertEqual(context['company'].tax_number, 'NIF-OLD')
        self.assertEqual(context['invoice_client'].name, 'Client historique')
        self.assertEqual(context['invoice_client'].tax_number, 'CLIENT-NIF-OLD')
        self.assertEqual(context['lines'][0].invoice_product_name, 'Produit historique')
        self.assertEqual(context['lines'][0].invoice_unit_name, 'Carton')
        self.assertEqual(context['total_ht'], Decimal('50.00'))
        self.assertEqual(context['tax_amount'], Decimal('9.50'))
        self.assertEqual(context['total_ttc'], Decimal('54.50'))
        self.assertEqual(context['snapshot_warning'], '')

        api = SaleSerializer(sale).data
        self.assertEqual(api['client_details']['name'], 'Client historique')
        self.assertEqual(api['company_details']['company_name'], 'Entreprise historique')
        self.assertEqual(api['lines'][0]['product']['name'], 'Produit historique')
        self.assertTrue(api['historical_document']['is_certified'])

        thermal = invoice_print_data(sale, paper_width=58, language='fr')
        self.assertEqual(thermal['company']['name_fr'], 'Entreprise historique')
        self.assertEqual(thermal['customer']['name'], 'Client historique')
        self.assertEqual(thermal['items'][0]['name'], 'Produit historique')
        self.assertEqual(thermal['items'][0]['unit'], 'Carton')
        self.assertEqual(thermal['totals']['total_ttc'], '54.50')

        request = RequestFactory().get('/invoice-preview/')
        request.user = AnonymousUser()
        html = render_to_string('commerce/sale_invoice_preview.html', context, request=request)
        self.assertIn('Entreprise historique', html)
        self.assertIn('Client historique', html)
        self.assertIn('Produit historique', html)
        self.assertNotIn('Entreprise actuelle', html)
        self.assertNotIn('Client actuel', html)
        self.assertNotIn('Produit actuel', html)

    def test_deliberate_invoice_edit_refreshes_snapshot_atomically(self):
        sale = self.create_invoice()
        self.mutate_reference_data()

        update_sale(
            sale=sale,
            client=self.client_record,
            lines=[{
                'product': self.product,
                'quantity': 1,
                'unit_price': Decimal('30.00'),
            }],
            discount=Decimal('0.00'),
            tax_rate=Decimal('19.00'),
        )
        sale.refresh_from_db()
        line = sale.lines.get()

        self.assertEqual(sale.client_name_snapshot, 'Client actuel')
        self.assertEqual(sale.company_name_snapshot, 'Entreprise actuelle')
        self.assertEqual(line.product_name_snapshot, 'Produit actuel')
        self.assertEqual(line.unit_name_snapshot, 'Palette')
        self.assertEqual(sale.subtotal_ht_snapshot, Decimal('30.00'))
        self.assertEqual(sale.tax_amount_snapshot, Decimal('5.70'))
        self.assertEqual(sale.total, Decimal('35.70'))

    def test_legacy_invoice_is_never_silently_certified(self):
        sale = Sale.objects.create(
            invoice_number='FAC-LEGACY-001',
            client=self.client_record,
            total=Decimal('0.00'),
            discount=Decimal('0.00'),
            tax_rate=Decimal('0.00'),
        )

        context = build_invoice_context(sale)
        self.assertEqual(sale.snapshot_integrity_status, Sale.SNAPSHOT_LEGACY_UNKNOWN)
        self.assertFalse(sale.has_certified_snapshot)
        self.assertTrue(context['snapshot_warning'])
        historical_document = SaleSerializer(sale).data['historical_document']
        self.assertFalse(historical_document['is_certified'])
        self.assertTrue(historical_document['warning'])

    def test_editing_a_legacy_invoice_does_not_silently_certify_it(self):
        sale = Sale.objects.create(
            invoice_number='FAC-LEGACY-EDIT-001',
            client=self.client_record,
            total=Decimal('0.00'),
            discount=Decimal('0.00'),
            tax_rate=Decimal('0.00'),
        )

        update_sale(sale=sale, client=self.client_record, lines=None)
        sale.refresh_from_db()

        self.assertEqual(sale.snapshot_integrity_status, Sale.SNAPSHOT_LEGACY_UNKNOWN)
        self.assertEqual(sale.snapshot_version, 0)
        self.assertFalse(sale.has_certified_snapshot)
        self.assertTrue(build_invoice_context(sale)['snapshot_warning'])

    def test_later_payments_remain_dynamic_and_do_not_modify_snapshot(self):
        sale = self.create_invoice()
        original = (
            sale.subtotal_ht_snapshot,
            sale.tax_amount_snapshot,
            sale.total,
            sale.client_name_snapshot,
        )
        Payment.objects.create(sale=sale, amount=Decimal('10.00'), payment_type=Sale.CASH)
        sale.refresh_from_db()

        self.assertEqual(
            (
                sale.subtotal_ht_snapshot,
                sale.tax_amount_snapshot,
                sale.total,
                sale.client_name_snapshot,
            ),
            original,
        )
        self.assertEqual(sale.amount_paid, Decimal('10.00'))
        self.assertEqual(sale.balance_due, Decimal('44.50'))
