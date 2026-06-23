from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, Category, Brand, Unit, Client, Supplier
from apps.commerce.models import Sale, Purchase, SaleLine, PurchaseLine


class CommerceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pass')
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
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

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

    def test_sale_invoice_pdf(self):
        sale = Sale.objects.create(invoice_number='INV456', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price='15.00')
        url = reverse('sale_invoice_pdf', args=[sale.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

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
