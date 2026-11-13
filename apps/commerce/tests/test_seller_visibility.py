from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.models import Sale, Payment
from apps.commerce.receivables import receivable_account_page, allocate_client_payment
from apps.commerce.views import visible_sales
from apps.inventory.models import Client
from apps.core.dashboard import get_period_bounds


class SellerWithoutLoadingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller = get_user_model().objects.create_user(username='seller-no-loading', role='seller')
        cls.other = get_user_model().objects.create_user(username='other-no-loading', role='seller')
        cls.admin = get_user_model().objects.create_superuser(username='scope-controller')
        cls.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='commerce', codename='change_sale',
        ))
        cls.customer = Client.objects.create(name='Synthetic shared debt', balance=Decimal('50.00'))
        cls.own = Sale.objects.create(invoice_number='SYN-OWN', client=cls.customer, total=Decimal('100.00'), created_by=cls.seller)
        cls.foreign = Sale.objects.create(invoice_number='SYN-FOREIGN', client=cls.customer, total=Decimal('200.00'), created_by=cls.other)

    def test_no_loading_never_grants_global_sales_and_admin_keeps_access(self):
        self.assertEqual(list(visible_sales(self.seller).values_list('pk', flat=True)), [self.own.pk])
        self.assertEqual(visible_sales(self.admin).count(), 2)
        for user, expected in ((self.seller, [self.own.pk]), (self.other, [self.foreign.pk])):
            api = APIClient()
            api.force_authenticate(user)
            response = api.get(reverse('api-sale-list'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row['id'] for row in response.data['results']], expected)
            foreign = self.foreign if user == self.seller else self.own
            self.assertEqual(api.get(reverse('api-sale-detail', args=[foreign.pk])).status_code, 404)
            self.assertEqual(api.get(reverse('api-invoice-detail', args=[foreign.pk])).status_code, 404)

    def test_web_foreign_document_refused_and_own_list_visible(self):
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse('sale_update', args=[self.foreign.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('sale_payment_list', args=[self.foreign.pk])).status_code, 404)
        response = self.client.get(reverse('sale_list'))
        self.assertContains(response, 'SYN-OWN')
        self.assertNotContains(response, 'SYN-FOREIGN')

    def test_dashboard_cannot_be_used_to_select_another_seller(self):
        request = RequestFactory().get('/', {'period': 'today', 'user': self.other.pk})
        request.user = self.seller
        bounds = get_period_bounds(request)
        self.assertFalse(bounds['can_filter_users'])
        self.assertEqual(bounds['selected_user'].pk, self.seller.pk)

    def test_receivables_keep_shared_opening_debt_but_no_foreign_invoice(self):
        account = receivable_account_page(self.seller, self.customer)
        self.assertEqual([row['id'] for row in account['sales']], [self.own.pk])
        self.assertEqual(account['total_invoiced'], Decimal('150.00'))
        allocations, opening = allocate_client_payment(client=self.customer, user=self.seller,
            amount=Decimal('125.00'), payment_type=Payment.CASH)
        self.assertEqual([p.sale_id for p in allocations], [self.own.pk])
        self.assertEqual(opening.amount, Decimal('25.00'))
        self.assertFalse(Payment.objects.filter(sale=self.foreign).exists())

    def test_paged_api_requires_read_permission_and_keeps_object_scope(self):
        api = APIClient()
        url = reverse('api-receivable-account-page', args=[self.customer.pk])
        self.assertIn(api.get(url).status_code, (401, 403))
        api.force_authenticate(self.seller)
        self.assertEqual(api.get(url).status_code, 403)
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='commerce', codename='view_receivables',
        ))
        seller = get_user_model().objects.get(pk=self.seller.pk)
        api.force_authenticate(seller)
        response = api.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data['invoices']], [self.own.pk])
        self.assertEqual(Decimal(response.data['opening_balance']), Decimal('50.00'))

    def test_explicit_global_delegation_and_denial_remain_effective(self):
        permission = Permission.objects.get(content_type__app_label='inventory', codename='view_all_loadingorders')
        self.seller.user_permissions.add(permission)
        seller = get_user_model().objects.get(pk=self.seller.pk)
        self.assertEqual(visible_sales(seller).count(), 2)
        seller.denied_permissions.add(permission)
        seller = get_user_model().objects.get(pk=self.seller.pk)
        self.assertEqual(visible_sales(seller).count(), 1)
