import io
import tempfile

import openpyxl
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, Category, Brand, Unit, Client, Supplier, StockMovement
from apps.inventory.services import record_stock_movement, reverse_stock_movement


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
        self.assertEqual(self.product.quantity, 5)
        movement = StockMovement.objects.get(source_type=StockMovement.SOURCE_PRODUCT)
        self.assertEqual(movement.applied_delta, -5)
        self.assertEqual(movement.balance_before, 10)
        self.assertEqual(movement.balance_after, 5)
        self.assertEqual(movement.created_by, self.user)

    def test_product_import_journals_new_and_existing_stock(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append([
            'Référence', 'Code-barres', 'Nom', 'Catégorie', 'Marque', 'Unité',
            "Prix d'achat", 'Prix de vente', 'Quantité', 'Stock minimum',
        ])
        sheet.append([
            self.product.reference, self.product.barcode, 'Produit existant',
            self.cat.name, self.brand.name, self.unit.name, 11, 16, 6, 2,
        ])
        sheet.append(['IMPORT-NEW', '', 'Produit importé', 'Cat2', 'Brand2', 'Unit2', 4, 7, 3, 1])
        payload = io.BytesIO()
        workbook.save(payload)
        uploaded = SimpleUploadedFile(
            'products.xlsx',
            payload.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse('product_import'), {'file': uploaded})

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        imported = Product.objects.get(reference='IMPORT-NEW')
        self.assertEqual(self.product.quantity, 6)
        self.assertEqual(imported.quantity, 3)
        movements = StockMovement.objects.filter(source_type=StockMovement.SOURCE_IMPORT).order_by('pk')
        self.assertEqual([movement.applied_delta for movement in movements], [-4, 3])
        self.assertTrue(all(movement.created_by == self.user for movement in movements))

    def test_product_import_rolls_back_all_rows_on_invalid_data(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append([
            'Référence', 'Code-barres', 'Nom', 'Catégorie', 'Marque', 'Unité',
            "Prix d'achat", 'Prix de vente', 'Quantité', 'Stock minimum',
        ])
        sheet.append(['AUDIT-IMPORT-1', '', 'Produit valide', '', '', '', 10, 15, 2, 1])
        sheet.append(['AUDIT-IMPORT-2', '', 'Produit invalide', '', '', '', 10, 15, 'incorrect', 1])
        payload = io.BytesIO()
        workbook.save(payload)
        uploaded = SimpleUploadedFile(
            'products.xlsx',
            payload.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse('product_import'), {'file': uploaded})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aucune donnée n’a été importée.')
        self.assertFalse(Product.objects.filter(reference__in=['AUDIT-IMPORT-1', 'AUDIT-IMPORT-2']).exists())

    def test_product_delete(self):
        url = reverse('product_delete', args=[self.product.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_product_delete_preserves_stock_history(self):
        record_stock_movement(
            product=self.product,
            movement_type=StockMovement.ENTRY,
            quantity=1,
            reason='Audited stock',
            user=self.user,
        )

        response = self.client.post(reverse('product_delete', args=[self.product.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'possède un historique de stock ou commercial')
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(StockMovement.objects.filter(product=self.product).exists())

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

    def test_stock_movement_create_and_reversal(self):
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
        self.assertEqual(movement.applied_delta, 5)
        self.assertEqual(movement.balance_before, 10)
        self.assertEqual(movement.balance_after, 15)
        self.assertEqual(movement.created_by, self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)

        url = reverse('stock_movement_delete', args=[movement.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(StockMovement.objects.filter(pk=movement.pk).exists())
        reversal = StockMovement.objects.get(reversal_of=movement)
        self.assertEqual(reversal.source_type, StockMovement.SOURCE_REVERSAL)
        self.assertEqual(reversal.applied_delta, -5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)

    def test_stock_movement_exit_rejects_insufficient_stock(self):
        response = self.client.post(reverse('stock_movement_create'), {
            'product': self.product.pk,
            'movement_type': StockMovement.EXIT,
            'quantity': 11,
            'reason': 'Invalid exit',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stock insuffisant')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertFalse(StockMovement.objects.exists())


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
        movement = record_stock_movement(
            product=self.product,
            movement_type=StockMovement.ENTRY,
            quantity=5,
            reason='Restock',
        )
        self.assertEqual(movement.product, self.product)
        self.assertEqual(movement.movement_type, StockMovement.ENTRY)
        self.assertEqual(movement.applied_delta, 5)
        self.assertEqual(movement.balance_before, 10)
        self.assertEqual(movement.balance_after, 15)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)

    def test_stock_movement_exit(self):
        movement = record_stock_movement(
            product=self.product,
            movement_type=StockMovement.EXIT,
            quantity=2,
            reason='Vente',
        )
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(movement.applied_delta, -2)
        self.assertEqual(str(movement), f"{self.product.name} - {StockMovement.EXIT}")
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)

    def test_stock_adjustment_sets_the_physical_balance(self):
        movement = record_stock_movement(
            product=self.product,
            movement_type=StockMovement.ADJUSTMENT,
            quantity=3,
            reason='Inventaire',
        )

        self.assertEqual(movement.applied_delta, -7)
        self.assertEqual(movement.balance_before, 10)
        self.assertEqual(movement.balance_after, 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 3)

    def test_stock_service_rolls_back_an_insufficient_exit(self):
        with self.assertRaises(ValidationError):
            record_stock_movement(
                product=self.product,
                movement_type=StockMovement.EXIT,
                quantity=11,
                reason='Invalid exit',
            )

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_stock_movement_is_immutable_and_requires_the_service(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                product=self.product,
                movement_type=StockMovement.ENTRY,
                quantity=1,
            )

        movement = record_stock_movement(
            product=self.product,
            movement_type=StockMovement.ENTRY,
            quantity=1,
        )
        movement.quantity = 2
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()
        with self.assertRaises(ValidationError):
            StockMovement.objects.filter(pk=movement.pk).delete()
        with self.assertRaises(ValidationError):
            StockMovement.objects.filter(pk=movement.pk).update(reason='Tampered')

    def test_reversal_is_append_only_and_protects_product_history(self):
        movement = record_stock_movement(
            product=self.product,
            movement_type=StockMovement.ENTRY,
            quantity=4,
        )
        reversal = reverse_stock_movement(movement)

        self.assertEqual(reversal.applied_delta, -4)
        self.assertEqual(reversal.reversal_of, movement)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        with self.assertRaises(ValidationError):
            reverse_stock_movement(movement)
        with self.assertRaises(ProtectedError):
            self.product.delete()
