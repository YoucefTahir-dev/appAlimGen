from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, Category, Brand, Unit, Client, Supplier, StockMovement


class InventoryViewTests(TestCase):
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

    def test_product_list_search(self):
        url = reverse('product_list')
        response = self.client.get(url, {'q': 'Test'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Product')

    def test_product_detail(self):
        url = reverse('product_detail', args=[self.product.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.reference)

    def test_product_update(self):
        url = reverse('product_update', args=[self.product.pk])
        response = self.client.post(url, {
            'barcode': '123',
            'name': 'Updated Product',
            'brand_text': self.brand.name,
            'purchase_price': '12.00',
            'sale_price': '18.00',
            'quantity': 5,
            'minimum_stock': 1,
            'description': 'Updated',
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Updated Product')

    def test_product_delete(self):
        url = reverse('product_delete', args=[self.product.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_client_list_page(self):
        url = reverse('client_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Liste des clients')

    def test_supplier_list_page(self):
        url = reverse('supplier_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Liste des fournisseurs')

    def test_client_create_update_delete(self):
        url = reverse('client_create')
        response = self.client.post(url, {
            'name': 'Client2',
            'phone': '0123456789',
            'address': 'Address 1',
            'wilaya': 'Alger',
            'email': 'client2@example.com',
            'tax_number': '123456',
            'balance': '100.00',
            'notes': 'Note client',
        })
        self.assertEqual(response.status_code, 302)
        client_obj = Client.objects.get(name='Client2')
        self.assertEqual(client_obj.phone, '0123456789')

        url = reverse('client_update', args=[client_obj.pk])
        response = self.client.post(url, {
            'name': 'Client2 updated',
            'phone': '0987654321',
            'address': 'Address 2',
            'wilaya': 'Oran',
            'email': 'client2b@example.com',
            'tax_number': '654321',
            'balance': '150.00',
            'notes': 'Updated note',
        })
        self.assertEqual(response.status_code, 302)
        client_obj.refresh_from_db()
        self.assertEqual(client_obj.name, 'Client2 updated')

        url = reverse('client_delete', args=[client_obj.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Client.objects.filter(pk=client_obj.pk).exists())

    def test_supplier_create_update_delete(self):
        url = reverse('supplier_create')
        response = self.client.post(url, {
            'name': 'Supplier2',
            'phone': '0123456789',
            'address': 'Address 1',
            'wilaya': 'Alger',
            'email': 'supplier2@example.com',
            'rc_number': 'RC123',
            'tax_number': '123456',
            'notes': 'Note Fournisseur',
        })
        self.assertEqual(response.status_code, 302)
        supplier_obj = Supplier.objects.get(name='Supplier2')
        self.assertEqual(supplier_obj.rc_number, 'RC123')

        url = reverse('supplier_update', args=[supplier_obj.pk])
        response = self.client.post(url, {
            'name': 'Supplier2 updated',
            'phone': '0987654321',
            'address': 'Address 2',
            'wilaya': 'Oran',
            'email': 'supplier2b@example.com',
            'rc_number': 'RC456',
            'tax_number': '654321',
            'notes': 'Updated note',
        })
        self.assertEqual(response.status_code, 302)
        supplier_obj.refresh_from_db()
        self.assertEqual(supplier_obj.name, 'Supplier2 updated')

        url = reverse('supplier_delete', args=[supplier_obj.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Supplier.objects.filter(pk=supplier_obj.pk).exists())

    def test_stock_movement_create_and_delete(self):
        url = reverse('stock_movement_create')
        response = self.client.post(url, {
            'product': self.product.pk,
            'movement_type': StockMovement.ENTRY,
            'quantity': 5,
            'reason': 'Restock',
        })
        self.assertEqual(response.status_code, 302)
        movement = StockMovement.objects.get(product=self.product, quantity=5)
        self.assertEqual(movement.movement_type, StockMovement.ENTRY)

        url = reverse('stock_movement_delete', args=[movement.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StockMovement.objects.filter(pk=movement.pk).exists())


class InventoryModelTests(TestCase):
    def setUp(self):
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

    def test_stock_movement_entry(self):
        movement = StockMovement.objects.create(product=self.product, movement_type=StockMovement.ENTRY, quantity=5, reason='Restock')
        self.assertEqual(movement.product, self.product)
        self.assertEqual(movement.movement_type, StockMovement.ENTRY)

    def test_stock_movement_exit(self):
        movement = StockMovement.objects.create(product=self.product, movement_type=StockMovement.EXIT, quantity=2, reason='Vente')
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(str(movement), f"{self.product.name} - {StockMovement.EXIT}")
