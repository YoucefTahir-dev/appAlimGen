from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.commerce.forms import PurchaseLineForm, SaleLineForm
from apps.commerce.product_search import search_products
from apps.inventory.models import Brand, Client, Product, ProductPackaging


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ProductSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='buyer-search', role='manager')
        cls.brand = Brand.objects.create(name='Cacao')
        cls.product = Product.objects.create(name='Chocolat Noir', barcode='6131234567890',
            brand=cls.brand, purchase_price=10, sale_price=20, wholesale_price=18,
            super_wholesale_price=15, retail_price=20, quantity=100)
        cls.other = Product.objects.create(name='Biscuit', purchase_price=5, sale_price=10, quantity=40)
        cls.pack = ProductPackaging.objects.create(product=cls.product, name='Carton', conversion_factor=12, default_sale_price=240)
        cls.url = reverse('commercial_product_search')

    def setUp(self):
        self.client.force_login(self.user)

    def lookup(self, query, context='sale', **extra):
        return self.client.get(self.url, {'q': query, 'context': context, **extra})

    def test_name_reference_barcode_brand_and_empty_queries(self):
        for query in ('chOC', 'colat', self.product.reference, self.product.barcode, 'cacao'):
            with self.subTest(query=query):
                response = self.lookup(query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['results'][0]['id'], self.product.pk)
        for query in ('C', '', 'unknown-product'):
            self.assertEqual(self.lookup(query).json(), {'results': []})
        self.assertEqual(self.lookup('x' * 101).status_code, 400)

    def test_bounded_results_exact_barcode_first_and_one_product_query(self):
        Product.objects.bulk_create([Product(name=f'Choco {i}', reference=f'TEST-{i}', barcode=f'BC-{i}',
            purchase_price=1, sale_price=2) for i in range(25)])
        results = self.lookup('Choc').json()['results']
        self.assertEqual(len(results), 20)
        self.assertEqual(self.lookup('6131234567890').json()['results'][0]['id'], self.product.pk)
        # Warm permission caches before measuring the bounded product query.
        search_products(user=self.user, query='Choc', context='sale')
        with self.assertNumQueries(1):
            search_products(user=self.user, query='Choc', context='sale')

    def test_sale_only_exposes_current_tariff_not_cost_or_other_tariffs(self):
        for kind, price in (('RETAIL', '20.00'), ('WHOLESALE', '18.00'), ('SUPER_WHOLESALE', '15.00')):
            customer = Client.objects.create(name=kind, customer_type=kind)
            result = self.lookup('choc', client_id=customer.pk).json()['results'][0]
            self.assertEqual(result['price'], price)
            self.assertEqual(set(result), {'id', 'name', 'reference', 'stock', 'price'})
        self.assertEqual(self.lookup('choc', context='purchase').json()['results'][0]['purchase_price'], '10.00')
        self.assertEqual(self.lookup('choc', client_id='bad').status_code, 400)

    def test_authentication_and_context_permissions(self):
        self.client.logout()
        self.assertEqual(self.lookup('choc').status_code, 302)
        seller = get_user_model().objects.create_user(username='search-seller', role='seller')
        self.client.force_login(seller)
        self.assertEqual(self.lookup('choc').status_code, 200)
        self.assertEqual(self.lookup('choc', context='purchase').status_code, 403)
        self.assertEqual(self.lookup('choc', context='invalid').status_code, 403)
        group = Group.objects.create(name='No commercial access')
        seller.groups.add(group)
        self.assertEqual(self.lookup('choc').status_code, 403)

    def test_change_only_role_can_search_and_reprice(self):
        user = get_user_model().objects.create_user(username='editor-search')
        role = Group.objects.create(name='Invoice editor')
        role.permissions.add(Permission.objects.get(codename='change_sale', content_type__app_label='commerce'))
        user.groups.add(role)
        self.client.force_login(user)
        customer = Client.objects.create(name='Client')
        self.assertEqual(self.lookup('choc').status_code, 200)
        response = self.client.get(reverse('sale_price_lookup'), {'product_id': self.product.pk, 'client_id': customer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['packagings'][0]['price'], '240.00')

    def test_new_forms_do_not_render_catalogue_or_packaging_catalogue(self):
        for name in ('sale_create', 'purchase_create'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Chocolat Noir')
            self.assertNotContains(response, 'Biscuit')
            self.assertNotContains(response, 'Carton')
            self.assertContains(response, 'product-search')
            self.assertContains(response, 'name="lines-0-product"', count=1)

    def test_ids_validated_and_selected_label_restored_after_errors(self):
        for Form, price in ((PurchaseLineForm, 'purchase_price'), (SaleLineForm, 'unit_price')):
            invalid = Form(data={'product': '', 'product_search': 'Choc', 'quantity': '1', price: '20'})
            self.assertFalse(invalid.is_valid())
            self.assertIn('product', invalid.errors)
            valid = Form(data={'product': self.product.pk, 'quantity': '1', price: '20'})
            self.assertTrue(valid.is_valid(), valid.errors)
            self.assertIn('Chocolat Noir', str(valid['product']))
            self.assertNotIn('Biscuit', str(valid['product']))
        form = SaleLineForm(data={'product': self.other.pk, 'packaging': self.pack.pk, 'quantity': '1', 'unit_price': '20'})
        self.assertFalse(form.is_valid())
        self.assertIn('packaging', form.errors)
