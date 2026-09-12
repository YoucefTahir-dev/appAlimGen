from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from apps.commerce.models import Purchase
from apps.core.models import AuditLog
from apps.inventory.models import Product, StockMovement, Supplier


class QuickProductTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='buyer', role=User.MANAGER)
        self.client.force_login(self.user)
        self.url = reverse('product_quick_create')
        self.data = {
            'quick-name': 'Sprite 1L', 'quick-brand_text': 'Sprite',
            'quick-purchase_price': '80.00', 'quick-super_wholesale_price': '90.00',
            'quick-wholesale_price': '100.00', 'quick-retail_price': '110.00',
        }

    def test_create_returns_product_with_identifiers_zero_stock_and_audit(self):
        response = self.client.post(self.url, {
            **self.data, 'quantity': '100', 'quick-quantity': '100',
            'quick-barcode': 'forged', 'quick-reference': 'forged',
        })
        self.assertEqual(response.status_code, 201, response.content)
        product = Product.objects.get(pk=response.json()['product']['id'])
        self.assertRegex(product.reference, r'^PRD-\d{4}-\d+$')
        self.assertTrue(product.barcode)
        self.assertNotEqual(product.barcode, 'forged')
        self.assertEqual(product.quantity, 0)
        self.assertEqual(product.brand.name, 'Sprite')
        self.assertEqual(product.sale_price, Decimal('110'))
        self.assertEqual(response.json()['product']['purchase_price'], '80.00')
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertTrue(AuditLog.objects.filter(user=self.user, path=self.url, status_code=201).exists())

    def test_invalid_prices_and_name_return_field_errors_without_writes(self):
        for field, value in [('name', ''), ('retail_price', '79'), ('purchase_price', '-1')]:
            with self.subTest(field=field):
                response = self.client.post(self.url, {**self.data, f'quick-{field}': value})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json()['errors'])
                self.assertFalse(Product.objects.exists())

    def test_generated_barcode_collision_returns_validation_error(self):
        first = self.client.post(self.url, self.data)
        product = Product.objects.get(pk=first.json()['product']['id'])
        with patch.object(Product, '_generate_barcode', return_value=product.barcode):
            response = self.client.post(self.url, {**self.data, 'quick-name': 'Other'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Product.objects.count(), 1)

    def test_purchase_permission_does_not_grant_product_creation(self):
        group = Group.objects.create(name='Purchase only')
        group.permissions.add(Permission.objects.get(content_type__app_label='commerce', codename='add_purchase'))
        self.user.groups.add(group)
        response = self.client.get(reverse('purchase_create'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'quick-product-open')
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(Product.objects.exists())

    def test_csrf_login_and_method_enforced(self):
        csrf_client = HttpClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, self.data).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.assertFalse(Product.objects.exists())

    def test_both_dynamic_permissions_are_required(self):
        group = Group.objects.create(name='Product only')
        group.permissions.add(Permission.objects.get(content_type__app_label='inventory', codename='add_product'))
        self.user.groups.add(group)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        group.permissions.add(Permission.objects.get(content_type__app_label='commerce', codename='add_purchase'))
        self.assertEqual(self.client.post(self.url, self.data).status_code, 201)

    def test_dynamic_purchase_records_new_product_stock_once(self):
        first = self.client.post(self.url, {**self.data, 'quick-name': 'Existing'})
        second = self.client.post(self.url, self.data)
        supplier = Supplier.objects.create(name='ABC Distribution')
        ids = [first.json()['product']['id'], second.json()['product']['id']]
        response = self.client.post(reverse('purchase_create'), {
            'reference': 'QA-PURCHASE', 'supplier': supplier.pk, 'tax_rate': '19',
            'lines-TOTAL_FORMS': '2', 'lines-INITIAL_FORMS': '0',
            'lines-0-product': ids[0], 'lines-0-quantity': '20', 'lines-0-purchase_price': '80',
            'lines-1-product': ids[1], 'lines-1-quantity': '100', 'lines-1-purchase_price': '81',
        })
        self.assertEqual(response.status_code, 302, response.content)
        purchase = Purchase.objects.get(reference='QA-PURCHASE')
        self.assertEqual(purchase.lines.count(), 2)
        product = Product.objects.get(pk=ids[1])
        self.assertEqual(product.quantity, 100)
        self.assertEqual(product.purchase_price, Decimal('80'))
        movements = StockMovement.objects.filter(product=product)
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.get().applied_delta, 100)
        self.assertEqual(movements.get().source_type, StockMovement.SOURCE_PURCHASE)
