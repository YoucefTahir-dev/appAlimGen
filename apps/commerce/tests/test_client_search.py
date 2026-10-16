from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.commerce.forms import SaleForm
from apps.commerce.models import Sale
from apps.inventory.models import Client


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SaleClientSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='client-search-manager', role=get_user_model().MANAGER,
        )
        cls.alpha = Client.objects.create(
            name='Alpha Distribution', phone='0550 10 20 30', tax_number='NIF-ALPHA',
            customer_type=Client.CustomerType.WHOLESALE,
        )
        cls.alpha_detail = Client.objects.create(name='Comptoir alpha détail')
        cls.duplicate_one = Client.objects.create(name='Client identique', phone='0600 00 00 01')
        cls.duplicate_two = Client.objects.create(name='Client identique', phone='0600 00 00 02')
        cls.hidden = Client.objects.create(name='Client sans correspondance', notes='secret-note')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)
        self.url = reverse('sale_client_search')

    def search(self, query):
        return self.client.get(self.url, {'q': query})

    def test_search_is_case_insensitive_partial_and_covers_phone_and_nif(self):
        response = self.search('ALpHa')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.json()['results']},
            {self.alpha.pk, self.alpha_detail.pk},
        )
        self.assertEqual(self.search('10 20').json()['results'][0]['id'], self.alpha.pk)
        self.assertEqual(self.search('nif-alpha').json()['results'][0]['id'], self.alpha.pk)

    def test_minimum_length_maximum_length_and_result_limit(self):
        self.assertEqual(self.search('').json(), {'results': []})
        self.assertEqual(self.search('a').json(), {'results': []})
        self.assertEqual(self.search('aucun resultat').json(), {'results': []})
        self.assertEqual(self.search('x' * 101).status_code, 400)
        Client.objects.bulk_create([Client(name=f'Client volume {index:02d}') for index in range(25)])
        self.assertEqual(len(self.search('Client volume').json()['results']), 20)

    def test_duplicate_names_remain_distinguishable_and_ids_are_returned(self):
        results = self.search('identique').json()['results']
        self.assertEqual({item['id'] for item in results}, {self.duplicate_one.pk, self.duplicate_two.pk})
        self.assertEqual({item['phone'] for item in results}, {'0600 00 00 01', '0600 00 00 02'})
        self.assertTrue(all(set(item) == {
            'id', 'name', 'phone', 'tax_number', 'customer_type',
        } for item in results))
        self.assertNotContains(self.search('identique'), 'secret-note')

    def test_endpoint_requires_sale_permission_and_disables_caching(self):
        response = self.search('Alpha')
        self.assertEqual(response['Cache-Control'], 'private, no-store')

        self.client.logout()
        self.assertEqual(self.search('Alpha').status_code, 302)
        unauthorized = get_user_model().objects.create_user(username='client-search-denied')
        unauthorized.groups.add(Group.objects.create(name='Client search denied'))
        self.client.force_login(unauthorized)
        self.assertEqual(self.search('Alpha').status_code, 403)

    @override_settings(SALE_CLIENT_SEARCH_RATE_LIMIT=2)
    def test_endpoint_is_rate_limited_per_user(self):
        self.assertEqual(self.search('Alpha').status_code, 200)
        self.assertEqual(self.search('Alpha').status_code, 200)
        limited = self.search('Alpha')
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited['Retry-After'], '60')

    def test_create_form_is_empty_and_does_not_embed_the_client_catalogue(self):
        response = self.client.get(reverse('sale_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="client-picker"')
        self.assertContains(response, 'id="id_client_search"')
        self.assertContains(response, 'product-autocomplete.js')
        self.assertNotContains(response, 'Alpha Distribution')
        self.assertNotContains(response, 'Client sans correspondance')

    def test_edit_form_preserves_only_the_selected_client(self):
        sale = Sale.objects.create(client=self.alpha, total='0.00', created_by=self.user)
        response = self.client.get(reverse('sale_update', args=[sale.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.alpha.pk}"')
        self.assertContains(response, 'Alpha Distribution — 0550 10 20 30')
        self.assertNotContains(response, 'Client sans correspondance')

    def test_typed_text_without_selection_is_rejected_but_selected_id_is_valid(self):
        invalid = SaleForm(data={
            'client': '', 'client_search': 'Alpha', 'discount': '0', 'tax_rate': '0',
        })
        self.assertFalse(invalid.is_valid())
        self.assertIn('client', invalid.errors)

        valid = SaleForm(data={
            'client': self.alpha.pk, 'client_search': 'Nom affiché',
            'discount': '0', 'tax_rate': '0',
        })
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data['client'], self.alpha)
