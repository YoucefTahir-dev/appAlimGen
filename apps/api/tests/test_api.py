from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.models import Group, Permission
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.commerce.models import Purchase, Sale
from apps.expenses.models import ExpenseCategory
from apps.inventory.models import (
    Brand, Category, Client, Product, ProductPackaging, StockMovement, Supplier, Unit,
)


@override_settings(
    SECRET_KEY='api-tests-secret-key-abcdefghijklmnopqrstuvwxyz-1234567890',
    REST_FRAMEWORK={
        **settings.REST_FRAMEWORK,
        'DEFAULT_THROTTLE_CLASSES': (),
    }
)
class MobileApiTests(APITestCase):
    password = 'StrongPass123!'

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username='api-admin', email='api@example.com', password=cls.password
        )
        cls.client_record = Client.objects.create(name='Client mobile')
        cls.supplier = Supplier.objects.create(name='Fournisseur mobile')
        cls.category = Category.objects.create(name='Alimentaire API')
        cls.brand = Brand.objects.create(name='Marque API')
        cls.unit = Unit.objects.create(name='Pièce API')
        cls.product = Product.objects.create(
            name='Produit API', category=cls.category, brand=cls.brand, unit=cls.unit,
            purchase_price=Decimal('50.00'), sale_price=Decimal('80.00'),
            quantity=0, minimum_stock=2,
        )
        from apps.inventory.services import record_stock_movement

        record_stock_movement(
            product=cls.product, movement_type=StockMovement.ENTRY, quantity=20,
            reason='Fixture API', user=cls.admin,
            source_type=StockMovement.SOURCE_PRODUCT,
            source_reference=cls.product.reference,
        )
        cls.product.refresh_from_db()
        cls.expense_category = ExpenseCategory.objects.create(name='Transport API')

    def setUp(self):
        cache.clear()

    def authenticate(self, user=None):
        user = user or self.admin
        response = self.client.post(
            reverse('api-login'),
            {'username': user.username, 'password': self.password},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        access = response.data['access'] if 'access' in response.data else response.data['data']['access']
        refresh = response.data['refresh'] if 'refresh' in response.data else response.data['data']['refresh']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        return refresh

    def test_login_refresh_logout_and_revocation(self):
        refresh = self.authenticate()
        self.assertEqual(self.client.get(reverse('api-me')).status_code, status.HTTP_200_OK)

        refreshed = self.client.post(reverse('api-refresh'), {'refresh': refresh}, format='json')
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK, refreshed.data)
        rotated = refreshed.data.get('refresh') or refreshed.data['data']['refresh']
        logout = self.client.post(reverse('api-logout'), {'refresh': rotated}, format='json')
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        rejected = self.client.post(reverse('api-refresh'), {'refresh': rotated}, format='json')
        self.assertEqual(rejected.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid-token')
        response = self.client.get(reverse('api-product-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data['success'])

    def test_expired_token_is_rejected(self):
        token = AccessToken.for_user(self.admin)
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.get(reverse('api-product-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_force_password_change_blocks_mobile_login(self):
        user = User.objects.create_user(
            username='api-password-change', password=self.password, force_password_change=True
        )
        response = self.client.post(
            reverse('api-login'), {'username': user.username, 'password': self.password}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_reuses_dynamic_permissions_and_individual_denials(self):
        role = Group.objects.create(name='Lecture produits API')
        view_permission = Permission.objects.get(
            content_type__app_label='inventory', codename='view_product'
        )
        role.permissions.add(view_permission)
        user = User.objects.create_user(username='api-reader', password=self.password)
        user.groups.add(role)
        self.authenticate(user)
        self.assertEqual(self.client.get(reverse('api-product-list')).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.post(reverse('api-product-list'), {}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        user.denied_permissions.add(view_permission)
        self.assertEqual(self.client.get(reverse('api-product-list')).status_code, status.HTTP_403_FORBIDDEN)

    def test_product_list_search_barcode_and_sensitive_price_visibility(self):
        self.authenticate()
        listing = self.client.get(reverse('api-product-list'), {'search': self.product.reference})
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        results = listing.data['results'] if 'results' in listing.data else listing.data['data']['results']
        self.assertEqual(results[0]['id'], self.product.pk)
        barcode = self.client.get(reverse('api-product-barcode', args=[self.product.barcode]))
        self.assertEqual(barcode.status_code, status.HTTP_200_OK)
        payload = barcode.data.get('data', barcode.data)
        self.assertEqual(payload['reference'], self.product.reference)
        self.assertIn('purchase_price', payload)

    def test_product_creation_journals_initial_stock(self):
        self.authenticate()
        response = self.client.post(reverse('api-product-list'), {
            'name': 'Nouveau produit mobile', 'category': self.category.pk,
            'brand': self.brand.pk, 'unit': self.unit.pk,
            'purchase_price': '20.00', 'sale_price': '30.00',
            'quantity': 8, 'minimum_stock': 2,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        product = Product.objects.get(name='Nouveau produit mobile')
        self.assertEqual(product.quantity, 8)
        movement = product.movements.get(source_type=StockMovement.SOURCE_PRODUCT)
        self.assertEqual(movement.balance_after, 8)

    def test_valid_sale_is_atomic_numbered_and_updates_stock(self):
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk, 'discount': '5.00', 'tax_rate': '0.00',
            'payment_type': 'cash', 'pay_full': True,
            'items': [{'product': self.product.pk, 'quantity': 2, 'unit_price': '80.00'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        sale = Sale.objects.get()
        self.assertRegex(sale.invoice_number, r'^FAC-\d{4}-\d{6}$')
        self.assertRegex(sale.ticket_number, r'^TCK-\d{4}-\d{6}$')
        self.assertEqual(sale.total, Decimal('155.00'))
        self.assertEqual(sale.amount_paid, Decimal('155.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 18)
        self.assertTrue(StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).exists())

    def test_sale_with_insufficient_stock_rolls_back_everything(self):
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk, 'discount': '0', 'tax_rate': '0',
            'payment_type': 'cash',
            'items': [{'product': self.product.pk, 'quantity': 999, 'unit_price': '80'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(response.data['error']['code'], 'INSUFFICIENT_STOCK')
        self.assertFalse(Sale.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 20)

    def test_sale_below_purchase_price_is_rejected_without_stock_change(self):
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk, 'discount': '0', 'tax_rate': '0',
            'payment_type': 'cash',
            'items': [{'product': self.product.pk, 'quantity': 1, 'unit_price': '49.99'}],
        }, format='json', HTTP_ACCEPT_LANGUAGE='en')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(response.data['error']['code'], 'SALE_PRICE_BELOW_COST')
        self.assertFalse(Sale.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 20)

    def test_product_detail_exposes_active_and_inactive_packagings(self):
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Pack de 6', conversion_factor=6,
            default_sale_price='480.00', barcode='PACK-API-6', is_active=True,
        )
        ProductPackaging.objects.create(
            product=self.product, name='Ancien carton', conversion_factor=12,
            default_sale_price='960.00', barcode='OLD-API-12', is_active=False,
        )
        self.authenticate()
        response = self.client.get(reverse('api-product-detail', args=[self.product.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        payload = response.data.get('data', response.data)
        self.assertEqual(len(payload['conditionnements']), 2)
        self.assertEqual(payload['conditionnements'][0]['product'], self.product.pk)
        self.assertIn(packaging.pk, {item['id'] for item in payload['conditionnements']})

    def test_packaging_api_crud_uses_the_shared_product_model(self):
        self.authenticate()
        created = self.client.post(reverse('api-product-packaging-list'), {
            'product': self.product.pk,
            'name': 'Carton de 12',
            'conversion_factor': 12,
            'default_sale_price': '960.00',
            'barcode': 'CARTON-API-12',
            'is_active': True,
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        packaging_id = created.data.get('data', created.data)['id']

        updated = self.client.patch(
            reverse('api-product-packaging-detail', args=[packaging_id]),
            {'name': 'Carton standard', 'is_active': False},
            format='json',
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        packaging = ProductPackaging.objects.get(pk=packaging_id)
        self.assertEqual(packaging.name, 'Carton standard')
        self.assertFalse(packaging.is_active)

    def test_packaging_api_rejects_a_default_price_below_cost(self):
        self.authenticate()
        response = self.client.post(reverse('api-product-packaging-list'), {
            'product': self.product.pk,
            'name': 'Carton non rentable',
            'conversion_factor': 12,
            'default_sale_price': '599.99',
            'barcode': 'CARTON-LOW-12',
            'is_active': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertFalse(ProductPackaging.objects.filter(barcode='CARTON-LOW-12').exists())

    def test_sale_by_packaging_converts_to_base_stock_and_uses_default_price(self):
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Pack de 6', conversion_factor=6,
            default_sale_price='480.00', barcode='PACK-SALE-6',
        )
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk, 'discount': '0', 'tax_rate': '0',
            'payment_type': 'cash',
            'items': [{'product_id': self.product.pk, 'packaging_id': packaging.pk, 'quantity': 2}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        line = Sale.objects.get().lines.get()
        self.assertEqual(line.packaging_quantity, 2)
        self.assertEqual(line.packaging_factor, 6)
        self.assertEqual(line.quantity, 12)
        self.assertEqual(line.unit_price, Decimal('480.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)

    def test_sale_by_carton_converts_24_units(self):
        from apps.inventory.services import record_stock_movement

        record_stock_movement(
            product=self.product, movement_type=StockMovement.ENTRY, quantity=40,
            reason='Stock cartons', user=self.admin,
            source_type=StockMovement.SOURCE_PRODUCT, source_reference=self.product.reference,
        )
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Carton de 24', conversion_factor=24,
            default_sale_price='1920.00', barcode='CARTON-SALE-24',
        )
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk, 'discount': '0', 'tax_rate': '0',
            'items': [{
                'product': self.product.pk, 'packaging_id': packaging.pk,
                'quantity': 2, 'unit_price': '1920.00',
            }],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 12)

    def test_packaging_price_below_cost_is_rejected(self):
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Pack de 6', conversion_factor=6,
            default_sale_price='480.00', barcode='PACK-LOSS-6',
        )
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk,
            'items': [{
                'product': self.product.pk, 'packaging_id': packaging.pk,
                'quantity': 1, 'unit_price': '299.99',
            }],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(response.data['error']['code'], 'SALE_PRICE_BELOW_COST')
        self.assertFalse(Sale.objects.exists())

    def test_inactive_wrong_product_and_unknown_packagings_are_rejected(self):
        inactive = ProductPackaging.objects.create(
            product=self.product, name='Pack inactif', conversion_factor=2,
            default_sale_price='160.00', barcode='PACK-INACTIVE', is_active=False,
        )
        other = Product.objects.create(
            name='Autre produit API', purchase_price='10', sale_price='20', quantity=0,
        )
        wrong = ProductPackaging.objects.create(
            product=other, name='Pack autre', conversion_factor=2,
            default_sale_price='40.00', barcode='PACK-WRONG',
        )
        self.authenticate()
        for packaging_id in (inactive.pk, wrong.pk, 999999):
            response = self.client.post(reverse('api-sale-list'), {
                'client': self.client_record.pk,
                'items': [{
                    'product': self.product.pk, 'packaging_id': packaging_id,
                    'quantity': 1, 'unit_price': '160.00',
                }],
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertFalse(Sale.objects.exists())

    def test_packaging_sale_with_insufficient_base_stock_rolls_back(self):
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Carton de 24', conversion_factor=24,
            default_sale_price='1920.00', barcode='CARTON-NOSTOCK-24',
        )
        self.authenticate()
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk,
            'items': [{
                'product': self.product.pk, 'packaging_id': packaging.pk,
                'quantity': 1, 'unit_price': '1920.00',
            }],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(response.data['error']['code'], 'INSUFFICIENT_STOCK')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 20)

    def test_purchase_updates_stock_and_history(self):
        self.authenticate()
        response = self.client.post(reverse('api-purchase-list'), {
            'reference': 'ACH-API-001', 'supplier': self.supplier.pk, 'tax_rate': '10',
            'items': [{'product': self.product.pk, 'quantity': 3, 'purchase_price': '50'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(Purchase.objects.filter(reference='ACH-API-001').exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 23)
        history = self.client.get(reverse('api-supplier-history', args=[self.supplier.pk]))
        self.assertEqual(history.status_code, status.HTTP_200_OK)

    def test_invoice_pdf_ticket_stock_dashboard_expense_and_alerts(self):
        self.authenticate()
        sale_response = self.client.post(reverse('api-sale-list'), {
            'client': self.client_record.pk, 'discount': '0', 'tax_rate': '0',
            'payment_type': 'cash',
            'items': [{'product': self.product.pk, 'quantity': 1, 'unit_price': '80'}],
        }, format='json')
        sale = Sale.objects.get()
        self.assertEqual(sale_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.client.get(reverse('api-invoice-pdf', args=[sale.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('api-invoice-ticket', args=[sale.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('api-stock-movements')).status_code, 200)
        self.assertEqual(self.client.get(reverse('api-dashboard'), {'period': 'today'}).status_code, 200)
        expense = self.client.post(reverse('api-expense-list'), {
            'date': '2026-08-16', 'category': self.expense_category.pk,
            'description': 'Livraison mobile', 'amount': '100.00',
            'payment_method': 'cash', 'observation': '',
        }, format='json')
        self.assertEqual(expense.status_code, status.HTTP_201_CREATED, expense.data)
        self.assertEqual(self.client.get(reverse('api-alerts')).status_code, 200)

    def test_accept_language_is_honoured_for_validation_errors(self):
        self.authenticate()
        response = self.client.post(
            reverse('api-sale-list'),
            {'client': self.client_record.pk, 'items': []},
            format='json', HTTP_ACCEPT_LANGUAGE='en',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.headers.get('Content-Language'), 'en')
        self.assertEqual(
            response.data['error']['message'],
            'A sale or purchase must contain at least one product.',
        )

    def test_schema_and_docs_require_admin_authentication(self):
        self.assertEqual(self.client.get(reverse('api-schema')).status_code, 401)
        self.authenticate()
        self.assertEqual(self.client.get(reverse('api-schema')).status_code, 200)
        self.assertEqual(self.client.get(reverse('api-docs')).status_code, 200)

    def test_direct_invoice_access_without_permission_is_forbidden(self):
        sale = Sale.objects.create(
            invoice_number='FAC-API-IDOR', client=self.client_record, total='0',
            discount='0', tax_rate='0', payment_type='cash',
        )
        role = Group.objects.create(name='Sans facture API')
        role.permissions.add(Permission.objects.get(
            content_type__app_label='inventory', codename='view_product'
        ))
        user = User.objects.create_user(username='api-no-invoice', password=self.password)
        user.groups.add(role)
        self.authenticate(user)
        response = self.client.get(reverse('api-invoice-detail', args=[sale.pk]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_main_read_endpoints_have_bounded_query_counts(self):
        ProductPackaging.objects.create(
            product=self.product, name='Performance pack', conversion_factor=2,
            default_sale_price='160.00', barcode='PERF-PACK-2',
        )
        self.authenticate()
        limits = {
            reverse('api-product-list'): 15,
            reverse('api-dashboard'): 70,
            reverse('api-sale-list'): 15,
            reverse('api-invoice-list'): 15,
            reverse('api-stock-list'): 15,
        }
        for url, maximum in limits.items():
            with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertLessEqual(len(queries), maximum, f'{url}: {len(queries)} requêtes')
