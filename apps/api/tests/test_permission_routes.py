"""SEC-03/04 route contracts and authorization boundaries on disposable test data."""

from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.api.permissions import BusinessPermission
from apps.api.urls import router, urlpatterns
from apps.commerce.models import Payment
from apps.commerce.services import create_sale
from apps.inventory.models import (
    Client,
    LoadingOrder,
    LoadingOrderLine,
    OperatorStock,
    OperatorStockMovement,
    Product,
    StockMovement,
)
from apps.inventory.services import record_stock_movement, validate_loading_order


class ApiPermissionRouteContractTests(SimpleTestCase):
    """A newly routed action must not silently inherit an authorization policy."""

    def assert_declared(self, view_class, action):
        required = getattr(view_class, 'required_permissions', None)
        self.assertIsInstance(required, dict, view_class.__name__)
        self.assertNotIn('*', required, view_class.__name__)
        self.assertIn(action, required, f'{view_class.__name__}.{action}')

    def test_every_registered_viewset_declares_its_routed_actions_and_metadata(self):
        inspected = set()
        for pattern in router.urls:
            callback = pattern.callback
            view_class = getattr(callback, 'cls', None)
            actions = getattr(callback, 'actions', {})
            if not view_class or not actions:
                continue
            self.assertIn(BusinessPermission, view_class.permission_classes)
            for method, action in actions.items():
                self.assertIn(method, view_class.http_method_names, (view_class.__name__, method))
                if method == 'head':
                    continue  # DRF aliases HEAD to the GET action.
                with self.subTest(view=view_class.__name__, method=method, action=action):
                    self.assert_declared(view_class, action)
            self.assert_declared(view_class, 'metadata')
            inspected.add(view_class)
        self.assertEqual(inspected, {view_class for _prefix, view_class, _basename in router.registry})

    def test_business_apiviews_and_authenticated_auth_views_declare_http_methods(self):
        inspected = set()
        for pattern in urlpatterns:
            callback = getattr(pattern, 'callback', None)
            view_class = getattr(callback, 'cls', None)
            if not view_class or BusinessPermission not in view_class.permission_classes:
                continue  # Explicit AllowAny and IsAdminUser policies are independent.
            for method in view_class.http_method_names:
                if method in {'head', 'options'} or not hasattr(view_class, method):
                    continue
                with self.subTest(view=view_class.__name__, method=method):
                    self.assert_declared(view_class, method)
            self.assert_declared(view_class, 'options')
            inspected.add(view_class.__name__)
        self.assertTrue({'CurrentUserView', 'LogoutView', 'PasswordChangeView'} <= inspected)
        self.assertTrue({'CompanySettingsView', 'DashboardView', 'AlertsView', 'PrintingCapabilitiesView'} <= inspected)

    def test_router_root_has_an_explicit_authenticated_policy(self):
        roots = [pattern.callback.cls for pattern in router.urls if pattern.name == 'api-root']
        self.assertTrue(roots)
        for root in roots:
            self.assertIn(BusinessPermission, root.permission_classes)
            self.assert_declared(root, 'get')
            self.assert_declared(root, 'options')


class ApiPermissionBoundaryTests(APITestCase):
    """Dynamic groups avoid the compatibility permissions of legacy SELLER roles."""

    password = 'RouteBoundaryTests-StrongPass123!'

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='routes-admin', password=cls.password)
        cls.operator = User.objects.create_user(username='routes-operator-a', password=cls.password)
        cls.other = User.objects.create_user(username='routes-operator-b', password=cls.password)
        cls.group = Group.objects.create(name='Routes A explicit permissions')
        cls.operator.groups.add(cls.group)
        cls.other.groups.add(Group.objects.create(name='Routes B explicit permissions'))
        cls.customer = Client.objects.create(name='Route boundary customer')
        cls.product = Product.objects.create(
            name='Route boundary product', purchase_price=Decimal('10'),
            sale_price=Decimal('20'), quantity=0,
        )
        record_stock_movement(
            product=cls.product, movement_type=StockMovement.ENTRY,
            quantity=30, user=cls.admin,
        )
        cls.order = LoadingOrder.objects.create(
            number='ROUTES-ORDER-A', operator=cls.operator, created_by=cls.admin,
        )
        cls.other_order = LoadingOrder.objects.create(
            number='ROUTES-ORDER-B', operator=cls.other, created_by=cls.admin,
        )
        for order, quantity in ((cls.order, 5), (cls.other_order, 7)):
            LoadingOrderLine.objects.create(loading_order=order, product=cls.product, quantity=quantity)
            validate_loading_order(order, user=cls.admin)
            order.refresh_from_db()
        cls.stock = OperatorStock.objects.get(operator=cls.operator, product=cls.product)
        cls.other_stock = OperatorStock.objects.get(operator=cls.other, product=cls.product)
        cls.movement = OperatorStockMovement.objects.get(operator=cls.operator)
        cls.other_movement = OperatorStockMovement.objects.get(operator=cls.other)

    def setUp(self):
        self.authenticate()

    def authenticate(self):
        # Reload to discard Django's per-instance permission caches after a grant/refusal.
        self.operator = User.objects.get(pk=self.operator.pk)
        self.client.force_authenticate(self.operator)

    @staticmethod
    def permission(name):
        app_label, codename = name.split('.', 1)
        return Permission.objects.get(content_type__app_label=app_label, codename=codename)

    def grant(self, *names):
        self.group.permissions.add(*(self.permission(name) for name in names))
        self.authenticate()

    def deny(self, *names):
        self.operator.denied_permissions.add(*(self.permission(name) for name in names))
        self.authenticate()

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        data = response.json()['data']
        return data['results'] if isinstance(data, dict) and 'results' in data else data

    def read_routes(self):
        return (
            reverse('api-operator-stock-list'),
            reverse('api-operator-stock-detail', args=[self.stock.pk]),
            reverse('api-operator-stock-movements'),
            reverse('api-loading-order-list'),
            reverse('api-loading-order-detail', args=[self.order.pk]),
            reverse('api-loading-order-current'),
        )

    def test_declared_permission_names_exist_and_empty_policies_are_allowlisted(self):
        classes = {view_class for _prefix, view_class, _basename in router.registry}
        for pattern in (*urlpatterns, *router.urls):
            callback = getattr(pattern, 'callback', None)
            view_class = getattr(callback, 'cls', None)
            if view_class and BusinessPermission in view_class.permission_classes:
                classes.add(view_class)
        real_permissions = {
            f'{app_label}.{codename}'
            for app_label, codename in Permission.objects.values_list('content_type__app_label', 'codename')
        }
        explicit_exceptions = {
            ('AuthenticatedAPIRootView', 'get'),
            ('CurrentUserView', 'get'),
            ('LogoutView', 'post'),
            ('PasswordChangeView', 'post'),
            ('ProductViewSet', 'search'),
            ('PaymentViewSet', 'list'),
            ('PaymentViewSet', 'retrieve'),
            ('PaymentViewSet', 'create'),
            ('PaymentViewSet', 'cancel'),
        }
        observed_exceptions = set()
        for view_class in classes:
            required = view_class.required_permissions
            self.assertNotIn('*', required, view_class.__name__)
            for action, names in required.items():
                names = (names,) if isinstance(names, str) else names
                with self.subTest(view=view_class.__name__, action=action):
                    self.assertIsInstance(names, (tuple, list))
                    if names:
                        self.assertTrue(set(names) <= real_permissions, set(names) - real_permissions)
                    elif action not in {'metadata', 'options'}:
                        exception = (view_class.__name__, action)
                        self.assertIn(exception, explicit_exceptions)
                        observed_exceptions.add(exception)
            for action in getattr(view_class, 'any_permission_actions', ()):
                with self.subTest(or_view=view_class.__name__, action=action):
                    self.assertIn(action, required)
                    self.assertTrue(required[action])
        self.assertEqual(observed_exceptions, explicit_exceptions)

    def test_operator_and_loading_reads_require_a_consultation_permission(self):
        for url in self.read_routes():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        # Global depot stock is a different module from the operator's loadings.
        self.grant('accounts.view_stock')
        for url in self.read_routes():
            with self.subTest(url=url, unrelated_permission=True):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_own_loading_permission_allows_only_own_operator_stock_and_movements(self):
        self.grant('inventory.view_loadingorder')
        for operator_parameter in (None, self.other.pk, 'not-an-operator'):
            query = {} if operator_parameter is None else {'operator': operator_parameter}
            with self.subTest(operator=operator_parameter):
                stocks = self.rows(self.client.get(reverse('api-operator-stock-list'), query))
                movements = self.rows(self.client.get(reverse('api-operator-stock-movements'), query))
                self.assertEqual([row['id'] for row in stocks], [self.stock.pk])
                self.assertEqual([row['id'] for row in movements], [self.movement.pk])
        self.assertEqual(self.client.get(reverse('api-operator-stock-detail', args=[self.stock.pk])).status_code, 200)
        self.assertEqual(self.client.get(
            reverse('api-operator-stock-detail', args=[self.other_stock.pk]), {'operator': self.other.pk},
        ).status_code, 404)

    def test_global_loading_permission_alone_allows_all_operator_stock_and_filtering(self):
        self.grant('inventory.view_all_loadingorders')
        stocks = self.rows(self.client.get(reverse('api-operator-stock-list')))
        self.assertEqual({row['id'] for row in stocks}, {self.stock.pk, self.other_stock.pk})
        for route, expected in (
            ('api-operator-stock-list', self.other_stock.pk),
            ('api-operator-stock-movements', self.other_movement.pk),
        ):
            with self.subTest(route=route):
                rows = self.rows(self.client.get(reverse(route), {'operator': self.other.pk}))
                self.assertEqual([row['id'] for row in rows], [expected])
                self.assertEqual(self.client.get(reverse(route), {'operator': 'invalid'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('api-operator-stock-detail', args=[self.other_stock.pk])).status_code, 200)

    def test_individual_refusal_overrides_group_permission(self):
        self.grant('inventory.view_loadingorder')
        self.deny('inventory.view_loadingorder')
        for url in self.read_routes():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_refused_global_permission_falls_back_to_own_scope_not_global_scope(self):
        self.grant('inventory.view_loadingorder', 'inventory.view_all_loadingorders')
        self.deny('inventory.view_all_loadingorders')
        stocks = self.rows(self.client.get(reverse('api-operator-stock-list'), {'operator': self.other.pk}))
        self.assertEqual([row['id'] for row in stocks], [self.stock.pk])
        self.assertEqual(self.client.get(reverse('api-loading-order-detail', args=[self.other_order.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('api-operator-stock-detail', args=[self.other_stock.pk])).status_code, 404)

    def test_own_loading_reads_keep_object_scope_despite_foreign_operator_parameter(self):
        self.grant('inventory.view_loadingorder')
        orders = self.rows(self.client.get(reverse('api-loading-order-list')))
        self.assertEqual([row['id'] for row in orders], [self.order.pk])
        self.assertEqual(self.rows(self.client.get(
            reverse('api-loading-order-list'), {'operator': self.other.pk},
        )), [])
        current = self.client.get(reverse('api-loading-order-current'), {'operator': self.other.pk})
        self.assertEqual(current.status_code, 200, current.data)
        self.assertEqual(current.json()['data']['id'], self.order.pk)
        self.assertEqual(self.client.get(reverse('api-loading-order-detail', args=[self.order.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('api-loading-order-detail', args=[self.other_order.pk])).status_code, 404)

    def test_global_loading_permission_alone_allows_loading_reads(self):
        self.grant('inventory.view_all_loadingorders')
        orders = self.rows(self.client.get(reverse('api-loading-order-list')))
        self.assertEqual({row['id'] for row in orders}, {self.order.pk, self.other_order.pk})
        selected = self.rows(self.client.get(reverse('api-loading-order-list'), {'operator': self.other.pk}))
        self.assertEqual([row['id'] for row in selected], [self.other_order.pk])
        self.assertEqual(self.client.get(reverse('api-loading-order-detail', args=[self.other_order.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('api-loading-order-current')).status_code, 200)

    def test_loading_consultation_does_not_grant_mutation_permissions(self):
        self.grant('inventory.view_loadingorder')
        before_status = self.order.status
        for route in ('api-loading-order-validate', 'api-loading-order-close', 'api-loading-order-cancel'):
            with self.subTest(route=route):
                self.assertEqual(self.client.post(reverse(route, args=[self.order.pk]), {}, format='json').status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, before_status)

    def test_product_search_preserves_context_permissions_without_requiring_product_view(self):
        url = reverse('api-product-search')
        self.assertEqual(self.client.get(url, {'q': 'Route', 'context': 'sale'}).status_code, 403)
        self.grant('commerce.add_sale')
        self.assertEqual(self.client.get(reverse('api-product-list')).status_code, 403)
        search = self.client.get(url, {'q': 'Route', 'context': 'sale', 'client_id': self.customer.pk})
        self.assertEqual(search.status_code, 200, search.data)
        self.assertEqual([row['id'] for row in search.json()['data']['results']], [self.product.pk])
        for context in ('purchase', 'loading_order'):
            with self.subTest(context=context):
                self.assertEqual(self.client.get(url, {'q': 'Route', 'context': context}).status_code, 403)
        self.grant('commerce.change_purchase', 'inventory.add_loadingorder')
        for context in ('purchase', 'loading_order'):
            with self.subTest(context=context):
                self.assertEqual(self.client.get(url, {'q': 'Route', 'context': context}).status_code, 200)

    def test_product_references_retain_their_existing_view_permission(self):
        routes = ('api-category-list', 'api-brand-list', 'api-unit-list')
        for route in routes:
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
        self.grant('inventory.view_product')
        for route in routes:
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)

    def test_payment_creation_and_cancellation_keep_target_specific_controls(self):
        sale = create_sale(
            client=self.customer, lines=[{'product': self.product, 'quantity': 1}],
            user=self.operator, pay_full=False,
        )
        payload = {'sale': sale.pk, 'amount': '1.00', 'payment_type': 'cash'}
        self.assertEqual(self.client.post(reverse('api-payment-list'), payload, format='json').status_code, 403)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())
        self.grant('commerce.add_customerpayment')
        created = self.client.post(reverse('api-payment-list'), payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        payment_id = created.json()['data']['id']
        # Creation is allowed independently of consultation; list data stays filtered.
        self.assertEqual(self.rows(self.client.get(reverse('api-payment-list'))), [])
        self.grant('commerce.view_sale')
        cancel_url = reverse('api-payment-cancel', args=[payment_id])
        self.assertEqual(self.client.post(cancel_url, {'reason': 'Permission boundary'}, format='json').status_code, 403)
        self.grant('commerce.cancel_customerpayment')
        cancelled = self.client.post(cancel_url, {'reason': 'Permission boundary'}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertTrue(cancelled.json()['data']['is_cancelled'])

    def test_authenticated_profile_root_and_logout_need_no_business_permission(self):
        self.client.force_authenticate(None)
        login = self.client.post(reverse('api-login'), {
            'username': self.operator.username, 'password': self.password,
        }, format='json')
        self.assertEqual(login.status_code, 200, login.data)
        tokens = login.json()['data']
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        profile = self.client.get(reverse('api-me'))
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(profile.json()['data']['permissions'], [])
        self.assertEqual(self.client.get(reverse('api-root')).status_code, 200)
        logout = self.client.post(reverse('api-logout'), {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(logout.status_code, 200, logout.data)
        self.client.credentials()
        self.assertEqual(self.client.post(reverse('api-refresh'), {'refresh': tokens['refresh']}, format='json').status_code, 401)

    def test_head_reuses_get_permissions_and_explicit_metadata_remains_authenticated(self):
        self.assertEqual(self.client.head(reverse('api-printing-capabilities')).status_code, 403)
        self.grant('accounts.print_invoice', 'inventory.view_loadingorder')
        for url in (
            reverse('api-me'), reverse('api-printing-capabilities'),
            reverse('api-operator-stock-list'), reverse('api-loading-order-current'),
        ):
            with self.subTest(url=url):
                response = self.client.head(url)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.content, b'')
        for url in (reverse('api-product-list'), reverse('api-me'), reverse('api-root')):
            with self.subTest(metadata_url=url):
                self.assertEqual(self.client.options(url).status_code, 200)
        self.client.force_authenticate(None)
        for url in (reverse('api-product-list'), reverse('api-me'), reverse('api-root')):
            with self.subTest(anonymous_url=url):
                self.assertEqual(self.client.options(url).status_code, 401)
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_metadata_does_not_advertise_write_actions_without_their_permissions(self):
        collection = reverse('api-product-list')
        detail = reverse('api-product-detail', args=[self.product.pk])
        for url, method in ((collection, 'POST'), (detail, 'PUT')):
            with self.subTest(url=url, allowed=False):
                metadata = self.client.options(url)
                self.assertEqual(metadata.status_code, 200, metadata.data)
                self.assertNotIn(method, metadata.json()['data'].get('actions', {}))
        self.grant('inventory.add_product', 'inventory.change_product')
        for url, method in ((collection, 'POST'), (detail, 'PUT')):
            with self.subTest(url=url, allowed=True):
                metadata = self.client.options(url)
                self.assertEqual(metadata.status_code, 200, metadata.data)
                self.assertIn(method, metadata.json()['data'].get('actions', {}))

    def test_metadata_write_introspection_preserves_loading_object_scope(self):
        self.grant('inventory.view_loadingorder', 'inventory.change_loadingorder')
        before_statuses = (self.order.status, self.other_order.status)
        own_metadata = self.client.options(reverse('api-loading-order-detail', args=[self.order.pk]))
        foreign_metadata = self.client.options(reverse('api-loading-order-detail', args=[self.other_order.pk]))
        self.assertEqual(own_metadata.status_code, 200, own_metadata.data)
        self.assertEqual(foreign_metadata.status_code, 200, foreign_metadata.data)
        self.assertIn('PUT', own_metadata.json()['data'].get('actions', {}))
        self.assertNotIn('PUT', foreign_metadata.json()['data'].get('actions', {}))
        self.order.refresh_from_db()
        self.other_order.refresh_from_db()
        self.assertEqual((self.order.status, self.other_order.status), before_statuses)

    def test_forced_password_change_keeps_existing_authenticated_route_restriction(self):
        self.operator.force_password_change = True
        self.operator.save(update_fields=['force_password_change'])
        self.authenticate()
        for url in (reverse('api-me'), reverse('api-root'), reverse('api-operator-stock-list')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.options(url).status_code, 403)
        self.assertEqual(self.client.post(reverse('api-password-change'), {}, format='json').status_code, 403)
