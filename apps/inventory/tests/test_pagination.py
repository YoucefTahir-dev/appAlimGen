from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.inventory.models import Client, Supplier


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class InventoryPaginationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='pagination-manager',
            password='StrongPass123!',
            role=User.MANAGER,
        )
        self.client.force_login(self.user)

    def test_client_list_is_paginated_and_keeps_search_filter(self):
        Client.objects.bulk_create(
            [Client(name=f'Client pagination {index:02d}') for index in range(30)]
        )

        first_page = self.client.get(reverse('client_list'), {'q': 'pagination'})
        second_page = self.client.get(
            reverse('client_list'),
            {'q': 'pagination', 'page': 2},
        )

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.context['clients']), 25)
        self.assertContains(first_page, 'q=pagination&amp;page=2')
        self.assertEqual(len(second_page.context['clients']), 5)

    def test_supplier_list_is_paginated(self):
        Supplier.objects.bulk_create(
            [Supplier(name=f'Fournisseur {index:02d}') for index in range(26)]
        )

        response = self.client.get(reverse('supplier_list'), {'page': 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['suppliers']), 1)
