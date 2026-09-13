from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.translation import gettext, override
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.api.authentication import MobileTokenSerializer
from apps.inventory.models import Brand, LoadingOrder, LoadingOrderLine, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class LoadingOrderProductSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username='loading-ui-admin', password='StrongPass123!')
        cls.operator = User.objects.create_user(username='loading-ui-operator', password='StrongPass123!')
        cls.brand = Brand.objects.create(name='Cacao Maison')
        cls.chocolate = Product.objects.create(
            name='Chocolat Noir', brand=cls.brand, barcode='6130000000011',
            purchase_price=10, sale_price=20, quantity=50,
        )
        cls.biscuit = Product.objects.create(
            name='Biscuit Choco', barcode='6130000000028',
            purchase_price=5, sale_price=10, quantity=30,
        )
        cls.out_of_stock = Product.objects.create(
            name='Chocolat en rupture', purchase_price=5, sale_price=10, quantity=0,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    @staticmethod
    def management(total, initial=0):
        return {
            'lines-TOTAL_FORMS': str(total),
            'lines-INITIAL_FORMS': str(initial),
            'lines-MIN_NUM_FORMS': '1',
            'lines-MAX_NUM_FORMS': '1000',
        }

    def test_create_page_has_one_empty_ajax_picker_and_dynamic_template(self):
        response = self.client.get(reverse('loading_order_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="lines-0-product"', count=1)
        self.assertNotContains(response, 'name="lines-1-product"')
        self.assertContains(response, 'class="form-control product-search"')
        self.assertContains(response, 'id="add-loading-line"')
        self.assertContains(response, 'id="loading-line-template"')
        self.assertNotContains(response, self.chocolate.name)

    def test_multiple_lines_save_as_draft_without_changing_stock(self):
        data = {
            'operator': self.operator.pk,
            'notes': 'Tournée test',
            **self.management(2),
            'lines-0-product': self.chocolate.pk,
            'lines-0-quantity': '20',
            'lines-1-product': self.biscuit.pk,
            'lines-1-quantity': '10',
        }
        response = self.client.post(reverse('loading_order_create'), data)
        self.assertRedirects(response, reverse('loading_order_list'))
        order = LoadingOrder.objects.get(operator=self.operator)
        self.assertEqual(order.status, LoadingOrder.DRAFT)
        self.assertEqual(order.lines.count(), 2)
        self.chocolate.refresh_from_db()
        self.biscuit.refresh_from_db()
        self.assertEqual((self.chocolate.quantity, self.biscuit.quantity), (50, 30))

    def test_empty_invalid_quantity_and_duplicate_lines_are_rejected(self):
        cases = (
            ({**self.management(1), 'lines-0-product': '', 'lines-0-quantity': ''},
             'Le bon de chargement doit contenir au moins un produit.'),
            ({**self.management(1), 'lines-0-product': self.chocolate.pk, 'lines-0-quantity': '0'}, 'positive'),
            ({**self.management(1), 'lines-0-product': self.chocolate.pk, 'lines-0-quantity': '-2'}, 'positive'),
            ({
                **self.management(2),
                'lines-0-product': self.chocolate.pk, 'lines-0-quantity': '1',
                'lines-1-product': self.chocolate.pk, 'lines-1-quantity': '2',
            }, 'Ce produit est déjà présent dans le bon de chargement.'),
        )
        for line_data, expected in cases:
            with self.subTest(line_data=line_data):
                response = self.client.post(reverse('loading_order_create'), {
                    'operator': self.operator.pk, 'notes': '', **line_data,
                })
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected, html=False)
                self.assertFalse(LoadingOrder.objects.exists())

    def test_draft_lines_reload_and_persisted_line_can_be_deleted(self):
        order = LoadingOrder.objects.create(number='CHG-UI-EDIT', operator=self.operator, created_by=self.admin)
        first = LoadingOrderLine.objects.create(loading_order=order, product=self.chocolate, quantity=4)
        second = LoadingOrderLine.objects.create(loading_order=order, product=self.biscuit, quantity=3)
        url = reverse('loading_order_update', args=(order.pk,))

        response = self.client.get(url)
        self.assertContains(response, 'name="lines-0-product"', count=1)
        self.assertContains(response, 'name="lines-1-product"', count=1)
        self.assertNotContains(response, 'name="lines-2-product"')
        self.assertContains(response, self.chocolate.name)
        self.assertContains(response, self.biscuit.name)

        data = {
            'operator': self.operator.pk, 'notes': '', **self.management(2, initial=2),
            'lines-0-id': first.pk, 'lines-0-product': self.chocolate.pk, 'lines-0-quantity': '5',
            'lines-1-id': second.pk, 'lines-1-product': self.biscuit.pk, 'lines-1-quantity': '3',
            'lines-1-DELETE': 'on',
        }
        self.assertRedirects(self.client.post(url, data), reverse('loading_order_list'))
        self.assertEqual(list(order.lines.values_list('product_id', 'quantity')), [(self.chocolate.pk, 5)])

    def test_loading_search_is_partial_bounded_stock_aware_and_secured(self):
        url = reverse('commercial_product_search')
        for query in ('chOC', 'colat', self.chocolate.reference, self.chocolate.barcode, 'cacao'):
            with self.subTest(query=query):
                response = self.client.get(url, {'q': query, 'context': 'loading_order'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['results'][0]['id'], self.chocolate.pk)
        self.assertNotIn(self.out_of_stock.pk, [
            row['id'] for row in self.client.get(url, {'q': 'choc', 'context': 'loading_order'}).json()['results']
        ])
        self.assertEqual(self.client.get(url, {'q': 'x', 'context': 'loading_order'}).json(), {'results': []})

        Product.objects.bulk_create([
            Product(
                name=f'Choco chargeable {index}', reference=f'LOAD-SEARCH-{index}',
                barcode=f'LOAD-BC-{index}', purchase_price=1, sale_price=2, quantity=1,
            )
            for index in range(25)
        ])
        self.assertEqual(len(self.client.get(url, {'q': 'Choco', 'context': 'loading_order'}).json()['results']), 20)

        self.client.logout()
        self.assertEqual(self.client.get(url, {'q': 'choc', 'context': 'loading_order'}).status_code, 302)
        denied = User.objects.create_user(username='loading-search-denied', password='StrongPass123!')
        denied.groups.add(Group.objects.create(name='Loading search denied'))
        self.client.force_login(denied)
        self.assertEqual(self.client.get(url, {'q': 'choc', 'context': 'loading_order'}).status_code, 403)

    def test_android_api_search_and_multiple_line_payload(self):
        anonymous = APIClient()
        self.assertEqual(
            anonymous.get(reverse('api-product-search'), {'q': 'choc', 'context': 'loading_order'}).status_code,
            401,
        )
        denied = User.objects.create_user(username='loading-api-denied', password='StrongPass123!')
        denied.groups.add(Group.objects.create(name='Loading API denied'))
        anonymous.credentials(HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(denied).access_token}')
        self.assertEqual(
            anonymous.get(reverse('api-product-search'), {'q': 'choc', 'context': 'loading_order'}).status_code,
            403,
        )

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {MobileTokenSerializer.get_token(self.admin).access_token}')
        search = api.get(reverse('api-product-search'), {'q': 'choc', 'context': 'loading_order'})
        self.assertEqual(search.status_code, 200, search.data)
        self.assertEqual(search.json()['data']['results'][0]['id'], self.chocolate.pk)
        self.assertEqual(set(search.json()['data']['results'][0]), {'id', 'name', 'reference', 'stock'})

        payload = {
            'operator': self.operator.pk,
            'notes': 'Créé par API',
            'lines': [
                {'product': self.chocolate.pk, 'quantity': 7},
                {'product': self.biscuit.pk, 'quantity': 6},
            ],
        }
        created = api.post(reverse('api-loading-order-list'), payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(LoadingOrder.objects.get(pk=created.json()['data']['id']).lines.count(), 2)

        payload['lines'][1]['product'] = self.chocolate.pk
        duplicate = api.post(reverse('api-loading-order-list'), payload, format='json')
        self.assertEqual(duplicate.status_code, 400)

    def test_new_interface_labels_are_translated(self):
        with override('en'):
            self.assertEqual(gettext('Stock disponible'), 'Available stock')
            self.assertEqual(gettext('Ajouter un produit'), 'Add a product')
        with override('ar'):
            self.assertEqual(gettext('Stock disponible'), 'المخزون المتاح')
            self.assertEqual(gettext('Action'), 'الإجراء')
