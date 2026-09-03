from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.commerce.models import Sale, SaleLine
from apps.inventory.models import Client, Product, ProductPackaging, StockMovement
from apps.inventory.services import record_stock_movement

from ..models import PrinterProfile, PrintProfile, UserPrinterPreference
from ..services import MockPrinterTransport, printer_test_payload


class PrinterDomainTests(TestCase):
    def printer(self, **overrides):
        values = {
            'name': 'Caisse principale', 'connection_mode': PrinterProfile.BLUETOOTH,
            'manufacturer': 'Generic', 'model_name': 'RPP02N', 'paper_width': 80,
            'protocol': PrinterProfile.GENERIC_ESCPOS, 'characters_per_line': 48,
        }
        values.update(overrides)
        return PrinterProfile.objects.create(**values)

    def test_only_one_company_default_is_kept(self):
        first = self.printer(is_default=True)
        second = self.printer(name='Caisse secondaire', is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_58mm_profile_rejects_excessive_line_width(self):
        with self.assertRaisesMessage(Exception, '42 caractères'):
            self.printer(name='Petit ticket', paper_width=58, characters_per_line=48)

    def test_rpp02n_diagnostic_is_transport_agnostic_and_testable(self):
        printer = self.printer()
        result = printer_test_payload(printer)
        transport = MockPrinterTransport()
        transport.send(result.payload)
        self.assertEqual(result.protocol, 'rpp02n_diagnostic')
        self.assertTrue(result.payload.startswith(b'\x1b\x40'))
        self.assertIn(b'EL AMINE ERP', result.payload)
        self.assertTrue(result.raster_arabic_recommended)
        self.assertEqual(transport.payloads, [result.payload])

    def test_seeded_print_profile_types_are_valid(self):
        PrintProfile.objects.create(
            name='Ticket magasin', document_type=PrintProfile.TICKET_80,
            paper_width=80, copies=1, language='bilingual',
        )
        self.assertTrue(PrintProfile.objects.filter(document_type='ticket_80').exists())


class PrintingApiTests(APITestCase):
    password = 'StrongPass123!'

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='print-admin', password=cls.password)
        cls.client_record = Client.objects.create(name='Client impression', phone='0555000000')
        cls.product = Product.objects.create(
            name='Boisson', purchase_price=Decimal('50'), sale_price=Decimal('80'), quantity=0,
        )
        record_stock_movement(
            product=cls.product, movement_type=StockMovement.ENTRY, quantity=100,
            reason='Stock test impression', user=cls.admin,
            source_type=StockMovement.SOURCE_PRODUCT, source_reference=cls.product.reference,
        )

    def authenticate(self, user=None):
        user = user or self.admin
        response = self.client.post(reverse('api-login'), {'username': user.username, 'password': self.password}, format='json')
        token = response.data.get('access') or response.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_printer_api_requires_jwt_and_permissions(self):
        self.assertEqual(self.client.get(reverse('api-printer-list')).status_code, 401)
        user = User.objects.create_user(username='no-print', password=self.password)
        user.groups.add(Group.objects.create(name='Sans impression'))
        self.authenticate(user)
        self.assertEqual(self.client.get(reverse('api-printer-list')).status_code, 403)

    def test_printer_crud_default_and_test_payload(self):
        self.authenticate()
        response = self.client.post(reverse('api-printer-list'), {
            'name': 'RPP02N caisse', 'printer_type': 'thermal', 'manufacturer': 'Rongta',
            'model_name': 'RPP02N', 'connection_mode': 'bluetooth', 'paper_width': 80,
            'protocol': 'generic_escpos', 'characters_per_line': 48, 'encoding': 'cp858',
            'is_default': True, 'is_active': True,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        printer = PrinterProfile.objects.get()
        default = self.client.get(reverse('api-printer-default'))
        self.assertEqual(default.status_code, 200, default.data)
        test_payload = self.client.get(reverse('api-printer-test-payload', args=[printer.pk]))
        self.assertEqual(test_payload.status_code, 200, test_payload.data)
        payload = test_payload.data.get('data', test_payload.data)
        self.assertEqual(payload['transport'], 'client-side')
        self.assertTrue(payload['raster_arabic_recommended'])
        disabled = self.client.patch(reverse('api-printer-detail', args=[printer.pk]), {'is_active': False}, format='json')
        self.assertEqual(disabled.status_code, 200, disabled.data)

    def test_invoice_print_data_uses_packaging_snapshots_in_all_languages(self):
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Pack de 6', conversion_factor=6,
            default_sale_price=Decimal('480'), barcode='PRINT-PACK-6',
        )
        sale = Sale.objects.create(
            invoice_number='FAC-2026-999001', ticket_number='TCK-2026-999001',
            client=self.client_record, total=Decimal('960'), discount=0, tax_rate=0,
        )
        line = SaleLine(
            sale=sale, product=self.product, packaging=packaging,
            packaging_quantity=2, quantity=12, unit_price=Decimal('480'),
        )
        line._stock_user = self.admin
        line.save()
        printer = PrinterProfile.objects.create(
            name='RPP02N', model_name='RPP02N', connection_mode='bluetooth',
            paper_width=80, protocol='generic_escpos', characters_per_line=48,
            is_default=True,
        )
        UserPrinterPreference.objects.create(user=self.admin, printer=printer)
        self.authenticate()
        for language in ('fr', 'ar', 'en', 'bilingual'):
            response = self.client.get(
                reverse('api-invoice-print-data', args=[sale.pk]),
                {'language': language, 'paper_width': 80},
            )
            self.assertEqual(response.status_code, 200, response.data)
            payload = response.data.get('data', response.data)
            self.assertEqual(payload['items'][0]['packaging'], 'Pack de 6')
            self.assertEqual(payload['items'][0]['quantity'], 2)
            self.assertEqual(payload['items'][0]['stock_quantity'], 12)
            self.assertEqual(payload['paper_width'], 80)
            self.assertEqual(payload['language'], language)
            self.assertEqual(payload['printer']['id'], printer.pk)

    def test_print_data_rejects_invalid_width_and_language(self):
        sale = Sale.objects.create(
            invoice_number='FAC-2026-999002', client=self.client_record, total=0, discount=0, tax_rate=0,
        )
        self.authenticate()
        self.assertEqual(
            self.client.get(reverse('api-invoice-print-data', args=[sale.pk]), {'paper_width': 42}).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(reverse('api-invoice-print-data', args=[sale.pk]), {'language': 'xx'}).status_code,
            400,
        )
