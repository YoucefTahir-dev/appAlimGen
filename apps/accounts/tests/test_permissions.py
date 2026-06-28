from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.commerce.models import Purchase, PurchaseLine, Sale, SaleLine
from apps.inventory.models import Brand, Category, Client, Product, StockMovement, Supplier, Unit


class InventoryPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_test',
            password='Pass12345!',
            role=User.ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.manager = User.objects.create_user(username='manager_test', password='Pass12345!', role=User.MANAGER)
        self.seller = User.objects.create_user(username='seller_test', password='Pass12345!', role=User.SELLER)

        self.category = Category.objects.create(name='Cat')
        self.brand = Brand.objects.create(name='Brand')
        self.unit = Unit.objects.create(name='Unit')
        self.product = Product.objects.create(
            name='Produit permission',
            category=self.category,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=10,
            minimum_stock=1,
        )
        self.client_obj = Client.objects.create(name='Client permission')
        self.supplier = Supplier.objects.create(name='Supplier permission')
        self.movement = StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.ENTRY,
            quantity=1,
            reason='Test',
        )
        self.sale = Sale.objects.create(
            invoice_number='FAC-PERM-001',
            client=self.client_obj,
            total='15.00',
            discount='0.00',
            tax_rate='0.00',
        )
        SaleLine.objects.create(sale=self.sale, product=self.product, quantity=1, unit_price='15.00')
        self.purchase = Purchase.objects.create(
            reference='PO-PERM-001',
            supplier=self.supplier,
            total='10.00',
            tax_rate='0.00',
        )
        PurchaseLine.objects.create(
            purchase=self.purchase,
            product=self.product,
            quantity=1,
            purchase_price='10.00',
        )

    def login_as(self, user):
        self.client.force_login(user)

    def assert_forbidden(self, method, url):
        response = getattr(self.client, method)(url)
        self.assertEqual(response.status_code, 403)

    def test_seller_can_only_consult_stock_products_and_create_clients(self):
        self.login_as(self.seller)

        allowed_gets = [
            reverse('dashboard'),
            reverse('product_list'),
            reverse('product_detail', args=[self.product.pk]),
            reverse('stock_movement_list'),
            reverse('client_create'),
        ]
        for url in allowed_gets:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        forbidden_gets = [
            reverse('product_create'),
            reverse('product_update', args=[self.product.pk]),
            reverse('product_export'),
            reverse('product_import'),
            reverse('client_list'),
            reverse('client_update', args=[self.client_obj.pk]),
            reverse('supplier_list'),
            reverse('supplier_create'),
            reverse('stock_movement_create'),
        ]
        for url in forbidden_gets:
            with self.subTest(url=url):
                self.assert_forbidden('get', url)

        forbidden_posts = [
            reverse('product_delete', args=[self.product.pk]),
            reverse('client_delete', args=[self.client_obj.pk]),
            reverse('supplier_delete', args=[self.supplier.pk]),
            reverse('stock_movement_delete', args=[self.movement.pk]),
        ]
        for url in forbidden_posts:
            with self.subTest(url=url):
                self.assert_forbidden('post', url)

        product_list_response = self.client.get(reverse('product_list'))
        self.assertNotContains(product_list_response, 'Ajouter produit')
        self.assertNotContains(product_list_response, 'Import Excel')
        self.assertNotContains(product_list_response, 'Modifier')
        self.assertNotContains(product_list_response, 'Supprimer')

    def test_manager_can_manage_inventory_but_cannot_access_django_admin(self):
        self.login_as(self.manager)

        allowed_gets = [
            reverse('dashboard'),
            reverse('product_list'),
            reverse('product_create'),
            reverse('product_update', args=[self.product.pk]),
            reverse('product_export'),
            reverse('product_import'),
            reverse('client_list'),
            reverse('client_create'),
            reverse('client_update', args=[self.client_obj.pk]),
            reverse('supplier_list'),
            reverse('supplier_create'),
            reverse('supplier_update', args=[self.supplier.pk]),
            reverse('stock_movement_list'),
            reverse('stock_movement_create'),
        ]
        for url in allowed_gets:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        response = self.client.get('/admin/')
        self.assertIn(response.status_code, (302, 403))

    def test_admin_can_access_inventory_and_django_admin(self):
        self.login_as(self.admin)

        self.assertEqual(self.client.get(reverse('product_create')).status_code, 200)
        self.assertEqual(self.client.get(reverse('supplier_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('stock_movement_create')).status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse('product_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_seller_can_create_sales_and_download_invoices_but_cannot_manage_purchases(self):
        self.login_as(self.seller)

        allowed_gets = [
            reverse('sale_list'),
            reverse('sale_create'),
            reverse('sale_invoice_pdf', args=[self.sale.pk]),
        ]
        for url in allowed_gets:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        forbidden_gets = [
            reverse('sale_update', args=[self.sale.pk]),
            reverse('purchase_list'),
            reverse('purchase_create'),
            reverse('purchase_update', args=[self.purchase.pk]),
        ]
        for url in forbidden_gets:
            with self.subTest(url=url):
                self.assert_forbidden('get', url)

        forbidden_posts = [
            reverse('sale_delete', args=[self.sale.pk]),
            reverse('purchase_delete', args=[self.purchase.pk]),
        ]
        for url in forbidden_posts:
            with self.subTest(url=url):
                self.assert_forbidden('post', url)

        sale_list_response = self.client.get(reverse('sale_list'))
        self.assertContains(sale_list_response, 'PDF')
        self.assertNotContains(sale_list_response, 'Modifier')
        self.assertNotContains(sale_list_response, 'Supprimer')

    def test_manager_can_manage_sales_and_purchases(self):
        self.login_as(self.manager)

        allowed_gets = [
            reverse('sale_list'),
            reverse('sale_create'),
            reverse('sale_update', args=[self.sale.pk]),
            reverse('sale_invoice_pdf', args=[self.sale.pk]),
            reverse('purchase_list'),
            reverse('purchase_create'),
            reverse('purchase_update', args=[self.purchase.pk]),
        ]
        for url in allowed_gets:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
