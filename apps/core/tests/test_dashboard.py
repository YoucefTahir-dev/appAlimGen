from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, Category, Brand, Unit, Client, Supplier
from apps.commerce.models import Sale, Purchase


class DashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pass')
        self.client.login(username='tester', password='pass')
        cat = Category.objects.create(name='Cat1')
        brand = Brand.objects.create(name='Brand1')
        unit = Unit.objects.create(name='Unit1')
        self.product = Product.objects.create(
            barcode='123',
            name='Test Product',
            category=cat,
            brand=brand,
            unit=unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=10,
            minimum_stock=1,
        )
        self.client_obj = Client.objects.create(name='Client1')
        self.supplier_obj = Supplier.objects.create(name='Supplier1')
        self.sale = Sale.objects.create(invoice_number='INV123', client=self.client_obj, total='100', discount='0', tax_rate='10')
        self.purchase = Purchase.objects.create(reference='PO123', supplier=self.supplier_obj, total='50', tax_rate='10')

    def test_dashboard_loads(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tableau de bord')
        self.assertContains(response, 'Valeur du stock')

    def test_dashboard_loads_in_arabic_rtl(self):
        self.client.cookies['django_language'] = 'ar'

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, 'لوحة التحكم')
        self.assertContains(response, 'قيمة المخزون')

    def test_mobile_sidebar_uses_bootstrap_offcanvas(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bootstrap.bundle.min.js')
        self.assertContains(response, 'id="appSidebar"')
        self.assertContains(response, 'offcanvas-lg offcanvas-start')
        self.assertContains(response, 'data-bs-toggle="offcanvas"')
        self.assertContains(response, 'data-bs-target="#appSidebar"')
        self.assertContains(response, 'data-bs-dismiss="offcanvas"')
        self.assertNotContains(response, 'sidebar-open')
