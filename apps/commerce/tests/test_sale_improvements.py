from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.models import Payment, Sale
from apps.commerce.services import create_sale
from apps.core.models import CompanySettings
from apps.inventory.models import Category, Client, Product
from apps.printing.services import invoice_print_data


class SalePaymentAndLegalInformationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser('sale-improvements', 'sale@example.com', 'pass')
        self.client.force_login(self.user)
        self.category = Category.objects.create(name='Ventes améliorées')
        self.customer = Client.objects.create(
            name='Client légal', tax_number='NIF-CLIENT', nis='NIS-CLIENT',
            article_number='AI-CLIENT', trade_register_number='RC-CLIENT',
        )
        CompanySettings.objects.update_or_create(pk=1, defaults={
            'company_name': 'El Amine', 'tax_number': 'NIF-SOCIETE', 'nis': 'NIS-SOCIETE',
            'article_number': 'AI-SOCIETE', 'rc_number': 'RC-SOCIETE',
        })
        self.product = Product.objects.create(
            name='Produit paiement', category=self.category, purchase_price='10.00',
            super_wholesale_price='50.00', wholesale_price='50.00', retail_price='50.00',
            quantity=100, minimum_stock=1, unit_mode=Product.UnitMode.PIECE_ONLY,
        )

    def _sale(self, **kwargs):
        return create_sale(
            client=self.customer,
            lines=[{'product': self.product, 'quantity': 2, 'unit_price': '50.00'}],
            user=self.user,
            **kwargs,
        )

    def test_three_initial_payment_modes(self):
        unpaid = self._sale()
        self.assertEqual(unpaid.amount_paid, Decimal('0.00'))
        partial = self._sale(initial_payment_amount='40.00')
        self.assertEqual(partial.amount_paid, Decimal('40.00'))
        self.assertEqual(partial.balance_due, Decimal('60.00'))
        paid = self._sale(pay_full=True)
        self.assertEqual(paid.amount_paid, Decimal('100.00'))
        self.assertEqual(paid.balance_due, Decimal('0.00'))
        self.assertEqual(Payment.objects.filter(sale=partial).count(), 1)

    def test_invalid_partial_payment_rolls_back_sale_and_stock(self):
        before = self.product.quantity
        with self.assertRaises(ValidationError):
            self._sale(initial_payment_amount='100.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, before)
        self.assertFalse(Sale.objects.exists())

    def test_api_partial_payment_is_idempotent(self):
        api = APIClient()
        api.force_authenticate(self.user)
        payload = {
            'client': self.customer.pk, 'discount': '0.00', 'tax_rate': '0.00',
            'payment_type': 'cash', 'amount_received': '40.00',
            'items': [{'product': self.product.pk, 'quantity': 2, 'unit_price': '50.00'}],
        }
        first = api.post(reverse('api-sale-list'), payload, format='json', HTTP_IDEMPOTENCY_KEY='partial-sale-1')
        second = api.post(reverse('api-sale-list'), payload, format='json', HTTP_IDEMPOTENCY_KEY='partial-sale-1')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(Sale.objects.count(), 1)
        sale = Sale.objects.get()
        self.assertEqual(sale.amount_paid, Decimal('40.00'))
        self.assertEqual(sale.payments.count(), 1)

    def test_ticket_can_hide_legal_information_but_official_invoice_keeps_it(self):
        sale = self._sale(show_legal_information=False)
        ticket = self.client.get(reverse('sale_ticket_preview', args=[sale.pk, 80]))
        self.assertNotContains(ticket, 'NIF-SOCIETE')
        self.assertNotContains(ticket, 'NIF-CLIENT')
        invoice = self.client.get(reverse('sale_invoice_preview', args=[sale.pk]))
        self.assertContains(invoice, 'NIF-SOCIETE')
        self.assertContains(invoice, 'NIF-CLIENT')
        thermal = invoice_print_data(sale, paper_width=80, language='fr')
        self.assertFalse(thermal['show_legal_information'])

    def test_historical_and_new_documents_default_to_visible_legal_information(self):
        sale = self._sale()
        self.assertTrue(sale.show_legal_information)
