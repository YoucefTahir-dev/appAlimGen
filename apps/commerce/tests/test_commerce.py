from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, Category, Brand, Unit, Client, Supplier
from apps.commerce.models import InvoiceSequence, Sale, Purchase, SaleLine, PurchaseLine


class CommerceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pass', role=User.MANAGER)
        self.client.login(username='tester', password='pass')
        self.cat = Category.objects.create(name='Cat1')
        self.brand = Brand.objects.create(name='Brand1')
        self.unit = Unit.objects.create(name='Unit1')
        self.product = Product.objects.create(
            barcode='123',
            name='Test Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=10,
            minimum_stock=1,
        )
        self.client_obj = Client.objects.create(name='Client1')
        self.supplier_obj = Supplier.objects.create(name='Supplier1')
    def test_purchase_create_and_stock_update(self):
        url = reverse('purchase_create')
        response = self.client.post(url, {
            'reference': 'PO123',
            'supplier': self.supplier_obj.pk,
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '5',
            'lines-0-purchase_price': '20.00',
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)

    def test_sale_create_and_stock_decrement(self):
        url = reverse('sale_create')
        response = self.client.post(url, {
            'invoice_number': 'INV123',
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '3',
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest('pk')
        self.assertRegex(sale.invoice_number, r'^FAC-\d{4}-000001$')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

    def test_invoice_number_is_sequential_and_not_reused_after_delete(self):
        url = reverse('sale_create')
        payload = {
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '15.00',
        }

        first_response = self.client.post(url, payload)
        self.assertEqual(first_response.status_code, 302)
        first_sale = Sale.objects.latest('pk')
        first_number = first_sale.invoice_number

        self.client.post(reverse('sale_delete', args=[first_sale.pk]))

        second_response = self.client.post(url, payload)
        self.assertEqual(second_response.status_code, 302)
        second_sale = Sale.objects.latest('pk')

        self.assertRegex(first_number, r'^FAC-\d{4}-000001$')
        self.assertRegex(second_sale.invoice_number, r'^FAC-\d{4}-000002$')
        self.assertNotEqual(first_number, second_sale.invoice_number)
        self.assertEqual(InvoiceSequence.objects.get(year=second_sale.created_at.year).last_number, 2)

    def test_sale_rejects_price_below_purchase_price(self):
        initial_quantity = self.product.quantity
        url = reverse('sale_create')
        response = self.client.post(url, {
            'invoice_number': 'INV-LOSS-001',
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '5.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Le prix de vente est inférieur au coût")
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Sale.objects.filter(invoice_number='INV-LOSS-001').exists())

    def test_sale_create_rejects_insufficient_stock(self):
        initial_quantity = self.product.quantity
        url = reverse('sale_create')
        response = self.client.post(url, {
            'invoice_number': 'INV-STOCK-FAIL',
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': str(initial_quantity + 1),
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stock insuffisant')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Sale.objects.filter(invoice_number='INV-STOCK-FAIL').exists())

    def test_purchase_update_and_stock_adjust(self):
        purchase = Purchase.objects.create(reference='PO123', supplier=self.supplier_obj, total='0', tax_rate='10')
        line = PurchaseLine.objects.create(purchase=purchase, product=self.product, quantity=5, purchase_price='20.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)

        url = reverse('purchase_update', args=[purchase.pk])
        response = self.client.post(url, {
            'reference': purchase.reference,
            'supplier': self.supplier_obj.pk,
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '1',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-id': line.pk,
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '8',
            'lines-0-purchase_price': '20.00',
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 18)

    def test_sale_update_and_stock_adjust(self):
        sale = Sale.objects.create(invoice_number='INV123', client=self.client_obj, total='0', discount='0', tax_rate='10')
        line = SaleLine.objects.create(sale=sale, product=self.product, quantity=3, unit_price='15.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

        url = reverse('sale_update', args=[sale.pk])
        response = self.client.post(url, {
            'invoice_number': sale.invoice_number,
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '1',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-id': line.pk,
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 9)

    def test_sale_update_rejects_insufficient_stock(self):
        sale = Sale.objects.create(invoice_number='INV-STOCK-UPD', client=self.client_obj, total='0', discount='0', tax_rate='0')
        line = SaleLine.objects.create(sale=sale, product=self.product, quantity=3, unit_price='15.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

        url = reverse('sale_update', args=[sale.pk])
        response = self.client.post(url, {
            'invoice_number': sale.invoice_number,
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '1',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-id': line.pk,
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '11',
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stock insuffisant')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 3)

    def test_sale_invoice_pdf(self):
        sale = Sale.objects.create(invoice_number='INV456', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price='15.00')
        url = reverse('sale_invoice_pdf', args=[sale.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_sale_invoice_preview(self):
        sale = Sale.objects.create(invoice_number='FAC-2026-000100', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price='15.00')
        response = self.client.get(reverse('sale_invoice_preview', args=[sale.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FACTURE')
        self.assertContains(response, sale.invoice_number)
        self.assertContains(response, 'الأمين للمواد الغذائية و غير الغذائية')
        self.assertContains(response, 'Télécharger PDF')

    def test_sale_delete_reverts_stock(self):
        initial_quantity = self.product.quantity
        sale = Sale.objects.create(invoice_number='INV999', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=4, unit_price='15.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity - 4)

        url = reverse('sale_delete', args=[sale.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('sale_list'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())

    def test_purchase_delete_reverts_stock(self):
        initial_quantity = self.product.quantity
        purchase = Purchase.objects.create(reference='PO999', supplier=self.supplier_obj, total='0', tax_rate='10')
        PurchaseLine.objects.create(purchase=purchase, product=self.product, quantity=6, purchase_price='20.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity + 6)

        url = reverse('purchase_delete', args=[purchase.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('purchase_list'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Purchase.objects.filter(pk=purchase.pk).exists())
