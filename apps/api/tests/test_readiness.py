from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import Permission, Group
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.commerce.models import Sale, Purchase, Payment
from apps.commerce.services import create_sale
from apps.expenses.models import ExpenseCategory
from apps.inventory.models import Client, Product, Supplier, StockMovement
from apps.inventory.services import record_stock_movement
from apps.api.serializers import ProductSerializer
from apps.api.authentication import MobileTokenSerializer


class AndroidReadinessTests(APITestCase):
    def test_openapi_documents_real_json_envelope_and_pdf(self):
        from drf_spectacular.generators import SchemaGenerator
        schema = SchemaGenerator().get_schema(public=True)
        product = schema['paths']['/api/v1/products/']['get']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(set(product['properties']), {'success', 'data'})
        pdf = schema['paths']['/api/v1/invoices/{id}/pdf/']['get']['responses']['200']['content']
        self.assertIn('application/pdf', pdf)
        self.assertNotIn('application/json', pdf)

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='audit-admin', password='StrongPass123!')
        cls.reader = User.objects.create_user(username='audit-reader', password='StrongPass123!')
        cls.reader.groups.add(Group.objects.create(name='Audit restricted'))
        cls.customer = Client.objects.create(name='Audit customer')
        cls.supplier = Supplier.objects.create(name='Audit supplier')
        cls.category = ExpenseCategory.objects.create(name='Audit expense')
        cls.product = Product.objects.create(name='Audit product', purchase_price=10, sale_price=20, quantity=0)
        record_stock_movement(product=cls.product, movement_type=StockMovement.ENTRY, quantity=100, user=cls.admin)

    def setUp(self):
        cache.clear()
        self.auth(self.admin)

    def auth(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(user).access_token}'
        )

    def refresh_for(self, user):
        return str(MobileTokenSerializer.get_token(user))

    def grant(self, *names):
        for name in names:
            app, code = name.split('.')
            self.reader.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))

    def sale(self, **kwargs):
        return create_sale(client=self.customer, lines=[{'product': self.product, 'quantity': 1}], user=self.admin, **kwargs)

    def test_invalid_tax_is_rejected_without_documents_or_stock_changes(self):
        for value in ('-100', '100.01'):
            for route, payload in (
                ('api-sale-list', {'client': self.customer.pk, 'items': [{'product': self.product.pk, 'quantity': 1}]}),
                ('api-purchase-list', {'reference': 'AUDIT', 'supplier': self.supplier.pk, 'items': [{'product': self.product.pk, 'quantity': 1, 'purchase_price': '10'}]}),
            ):
                with self.subTest(route=route, value=value):
                    response = self.client.post(reverse(route), {**payload, 'tax_rate': value}, format='json')
                    self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(Purchase.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 100)

    def test_non_positive_expenses_rejected(self):
        for amount in ('0', '-10'):
            response = self.client.post(reverse('api-expense-list'), {'category': self.category.pk, 'description': 'Test', 'amount': amount}, format='json')
            self.assertEqual(response.status_code, 400)

    def test_histories_require_commercial_permission(self):
        self.grant('inventory.view_client', 'inventory.view_supplier')
        self.auth(self.reader)
        for route, pk in [('api-client-history', self.customer.pk), ('api-supplier-history', self.supplier.pk)]:
            self.assertEqual(self.client.get(reverse(route, args=[pk])).status_code, 403)
        self.grant('commerce.view_sale', 'commerce.view_purchase')
        for route, pk in [('api-client-history', self.customer.pk), ('api-supplier-history', self.supplier.pk)]:
            self.assertEqual(self.client.get(reverse(route, args=[pk])).status_code, 200)

    def test_sale_cost_visibility_and_wire_contract(self):
        sale = self.sale()
        self.grant('commerce.view_sale', 'accounts.view_invoices')
        self.auth(self.reader)
        for route in ('api-sale-detail', 'api-invoice-detail'):
            response = self.client.get(reverse(route, args=[sale.pk]))
            payload = response.json()
            self.assertIs(payload['success'], True)
            data = payload['data']
            self.assertEqual(data['total'], '20.00')
            self.assertNotIn('unit_cost', data['lines'][0])
            self.assertEqual(data['lines'][0]['product']['id'], self.product.pk)
        self.auth(self.admin)
        self.assertEqual(self.client.get(reverse('api-sale-detail', args=[sale.pk])).json()['data']['lines'][0]['unit_cost'], '10.00')

    def test_stale_product_edit_does_not_restore_stock(self):
        stale = Product.objects.get(pk=self.product.pk)
        self.sale()
        serializer = ProductSerializer(stale, data={'description': 'Changed'}, partial=True, context={'request': SimpleNamespace(user=self.admin)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 99)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 2)

    def test_refresh_deleted_and_force_change_accounts_rejected(self):
        refresh = self.refresh_for(self.reader)
        self.reader.force_password_change = True
        self.reader.save(update_fields=['force_password_change'])
        self.assertEqual(self.client.post(reverse('api-refresh'), {'refresh': refresh}).status_code, 401)
        self.reader.delete()
        self.assertEqual(self.client.post(reverse('api-refresh'), {'refresh': refresh}).status_code, 401)

    def test_logout_cannot_blacklist_another_users_token(self):
        refresh = self.refresh_for(self.reader)
        self.assertEqual(self.client.post(reverse('api-logout'), {'refresh': refresh}).status_code, 400)
        self.assertEqual(self.client.post(reverse('api-refresh'), {'refresh': refresh}).status_code, 200)

    def test_password_change_revokes_old_access_and_refresh_tokens(self):
        login = self.client.post(
            reverse('api-login'),
            {'username': self.admin.username, 'password': 'StrongPass123!'},
            format='json',
        ).json()['data']
        old_access = login['access']
        old_refresh = login['refresh']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {old_access}')

        changed = self.client.post(
            reverse('api-password-change'),
            {
                'current_password': 'StrongPass123!',
                'new_password': 'NewStrongPass456!',
                'new_password_confirm': 'NewStrongPass456!',
            },
            format='json',
        )
        self.assertEqual(changed.status_code, 200, changed.data)

        rejected_access = self.client.get(reverse('api-me'))
        self.assertEqual(rejected_access.status_code, 401)
        self.assertEqual(rejected_access.json()['error']['code'], 'TOKEN_REVOKED')
        self.client.credentials()
        rejected_refresh = self.client.post(reverse('api-refresh'), {'refresh': old_refresh}, format='json')
        self.assertEqual(rejected_refresh.status_code, 401)
        self.assertEqual(rejected_refresh.json()['error']['code'], 'TOKEN_REVOKED')

        login_again = self.client.post(
            reverse('api-login'),
            {'username': self.admin.username, 'password': 'NewStrongPass456!'},
            format='json',
        )
        self.assertEqual(login_again.status_code, 200, login_again.data)
        new_tokens = login_again.json()['data']
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {new_tokens['access']}")
        self.assertEqual(self.client.get(reverse('api-me')).status_code, 200)
        self.client.credentials()
        self.assertEqual(
            self.client.post(reverse('api-refresh'), {'refresh': new_tokens['refresh']}, format='json').status_code,
            200,
        )

    def test_invalid_filters_return_structured_400(self):
        for route in ('api-sale-list', 'api-purchase-list', 'api-expense-list'):
            for query in ({'start_date': 'invalid'}, {'start_date': '2026-02-02', 'end_date': '2026-01-01'}):
                response = self.client.get(reverse(route), query)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()['success'])
        self.assertEqual(self.client.get(reverse('api-stock-movements'), {'product': 'bad'}).status_code, 400)

    def test_read_permissions_are_enforced_for_anonymous_and_invalid_ids(self):
        self.client.credentials()
        for route in ('api-product-list', 'api-client-list', 'api-sale-list', 'api-stock-list', 'api-printer-list'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 401)
        self.auth(self.admin)
        self.assertEqual(self.client.get(reverse('api-invoice-detail', args=[999999])).status_code, 404)

    def test_sale_generated_fields_cannot_be_assigned(self):
        response = self.client.post(reverse('api-sale-list'), {
            'client': self.customer.pk, 'items': [{'product': self.product.pk, 'quantity': 1}],
            'invoice_number': 'FORGED', 'total': '1', 'created_by': self.reader.pk,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        sale = Sale.objects.get(pk=response.json()['data']['id'])
        self.assertNotEqual(sale.invoice_number, 'FORGED')
        self.assertEqual(sale.created_by_id, self.admin.pk)
        self.assertEqual(sale.total, Decimal('20'))

    def test_populated_sale_list_query_count_stays_bounded(self):
        first = self.sale(pay_full=True)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(self.client.get(reverse('api-sale-list')).status_code, 200)
        for _ in range(8):
            self.sale(pay_full=True)
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(reverse('api-sale-list'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(large), len(small) + 1)
        self.assertEqual(response.json()['data']['results'][0]['balance_due'], '0.00')

    def test_alerts_do_not_bypass_document_permissions(self):
        self.sale()
        self.grant('accounts.view_dashboard')
        self.auth(self.reader)
        data = self.client.get(reverse('api-alerts')).json()['data']
        self.assertFalse(any(row['type'] in ('unpaid_invoice', 'supplier_payment', 'missing_receipt') for row in data['results']))
