from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Client


class ClientLegalInformationWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            username='client-legal-admin',
            password='StrongPass123!',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_existing_client_remains_compatible_without_legal_identifiers(self):
        customer = Client.objects.create(name='Client historique')

        self.assertEqual(customer.nis, '')
        self.assertEqual(customer.article_number, '')
        self.assertEqual(customer.trade_register_number, '')

        response = self.client.get(reverse('client_detail', args=[customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informations légales')
        self.assertContains(response, '—', count=4)

    def test_web_create_update_and_detail_legal_identifiers(self):
        create_response = self.client.post(
            reverse('client_create'),
            {
                'name': 'SARL Exemple',
                'customer_type': Client.CustomerType.WHOLESALE,
                'tax_number': 'NIF-001',
                'nis': '  NIS-001  ',
                'article_number': ' ART-001 ',
                'trade_register_number': ' RC-001 ',
                'balance': '0.00',
            },
        )
        self.assertEqual(create_response.status_code, 302)
        customer = Client.objects.get(name='SARL Exemple')
        self.assertEqual(customer.nis, 'NIS-001')
        self.assertEqual(customer.article_number, 'ART-001')
        self.assertEqual(customer.trade_register_number, 'RC-001')

        edit = self.client.get(reverse('client_update', args=[customer.pk]))
        self.assertContains(edit, 'value="NIS-001"')
        self.assertContains(edit, 'value="ART-001"')
        self.assertContains(edit, 'value="RC-001"')

        update_response = self.client.post(
            reverse('client_update', args=[customer.pk]),
            {
                'name': customer.name,
                'customer_type': Client.CustomerType.WHOLESALE,
                'tax_number': 'NIF-001',
                'nis': 'NIS-002',
                'article_number': 'ART-002',
                'trade_register_number': 'RC-002',
                'balance': '0.00',
            },
        )
        self.assertEqual(update_response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.nis, 'NIS-002')
        self.assertEqual(customer.article_number, 'ART-002')
        self.assertEqual(customer.trade_register_number, 'RC-002')

        detail = self.client.get(reverse('client_detail', args=[customer.pk]))
        self.assertContains(detail, 'Informations légales')
        self.assertContains(detail, 'NIS-002')
        self.assertContains(detail, 'ART-002')
        self.assertContains(detail, 'RC-002')

    def test_web_search_finds_each_legal_identifier(self):
        customer = Client.objects.create(
            name='Client identifiants',
            nis='NIS-SEARCH-77',
            article_number='ARTICLE-SEARCH-88',
            trade_register_number='RC-SEARCH-99',
        )

        for query in ('SEARCH-77', 'SEARCH-88', 'SEARCH-99'):
            with self.subTest(query=query):
                response = self.client.get(reverse('client_list'), {'q': query})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, customer.name)


class ClientLegalInformationApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            username='client-legal-api-admin',
            password='StrongPass123!',
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    @staticmethod
    def data(response):
        payload = response.json()
        return payload.get('data', payload)

    def test_api_post_get_patch_and_search_legal_identifiers(self):
        created = self.api.post(
            reverse('api-client-list'),
            {
                'name': 'Client légal API',
                'nis': 'NIS-API-001',
                'article_number': 'ARTICLE-API-001',
                'trade_register_number': 'RC-API-001',
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        created_data = self.data(created)
        customer_id = created_data['id']
        self.assertEqual(created_data['nis'], 'NIS-API-001')
        self.assertEqual(created_data['article_number'], 'ARTICLE-API-001')
        self.assertEqual(created_data['trade_register_number'], 'RC-API-001')

        detail = self.api.get(reverse('api-client-detail', args=[customer_id]))
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(self.data(detail)['nis'], 'NIS-API-001')

        updated = self.api.patch(
            reverse('api-client-detail', args=[customer_id]),
            {
                'nis': 'NIS-SEARCH-002',
                'article_number': 'ARTICLE-SEARCH-003',
                'trade_register_number': 'RC-SEARCH-004',
            },
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(self.data(updated)['nis'], 'NIS-SEARCH-002')
        self.assertEqual(self.data(updated)['article_number'], 'ARTICLE-SEARCH-003')
        self.assertEqual(self.data(updated)['trade_register_number'], 'RC-SEARCH-004')

        for query in ('SEARCH-002', 'SEARCH-003', 'SEARCH-004'):
            with self.subTest(query=query):
                searched = self.api.get(
                    reverse('api-client-list'),
                    {'search': query},
                )
                self.assertEqual(searched.status_code, 200, searched.data)
                results = self.data(searched)['results']
                self.assertEqual([item['id'] for item in results], [customer_id])
