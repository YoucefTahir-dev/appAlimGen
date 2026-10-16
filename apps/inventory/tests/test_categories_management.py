from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.inventory.forms import CategoryForm
from apps.inventory.models import Category, Product


class CategoryManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser('category-admin', 'category@example.com', 'pass')
        self.client.force_login(self.user)
        self.first = Category.objects.create(name='Chocolats')
        self.target = Category.objects.create(name='Confiserie')
        self.product = Product.objects.create(
            name='Chocolat Mina', category=self.first, purchase_price='10.00',
            super_wholesale_price='11.00', wholesale_price='12.00', retail_price='13.00',
            quantity=5, minimum_stock=1, unit_mode=Product.UnitMode.PIECE_ONLY,
        )

    def test_form_normalizes_name_and_rejects_case_insensitive_duplicate(self):
        form = CategoryForm({'name': '  Chocolats   et   confiseries  '}, instance=self.first)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().name, 'Chocolats et confiseries')
        duplicate = CategoryForm({'name': '  CONFISERIE '})
        self.assertFalse(duplicate.is_valid())

    def test_rename_keeps_product_relationship(self):
        response = self.client.post(reverse('category_update', args=[self.first.pk]), {'name': 'Chocolats fins'})
        self.assertRedirects(response, reverse('category_list'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.first.pk)
        self.assertEqual(self.product.category.name, 'Chocolats fins')

    def test_delete_used_category_requires_then_applies_reassignment(self):
        refused = self.client.post(reverse('category_delete', args=[self.first.pk]), {'replacement': ''})
        self.assertEqual(refused.status_code, 200)
        self.assertTrue(Category.objects.filter(pk=self.first.pk).exists())
        accepted = self.client.post(
            reverse('category_delete', args=[self.first.pk]), {'replacement': self.target.pk},
        )
        self.assertRedirects(accepted, reverse('category_list'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.category, self.target)

    def test_quick_create_returns_category_for_product_form(self):
        response = self.client.post(reverse('category_quick_create'), {'name': '  Biscuits  '})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['name'], 'Biscuits')

    def test_api_crud_and_reassign_delete(self):
        api = APIClient()
        api.force_authenticate(self.user)
        created = api.post(reverse('api-category-list'), {'name': 'Boissons'}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        category_id = created.data.get('data', created.data)['id']
        renamed = api.patch(reverse('api-category-detail', args=[category_id]), {'name': 'Boissons froides'}, format='json')
        self.assertEqual(renamed.status_code, 200, renamed.data)
        response = api.post(
            reverse('api-category-reassign-delete', args=[self.first.pk]),
            {'replacement': self.target.pk}, format='json',
        )
        self.assertEqual(response.status_code, 204, getattr(response, 'data', None))
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.target.pk)

    def test_category_management_requires_permissions_on_web_and_api(self):
        User = get_user_model()
        operator = User.objects.create_user(
            'category-operator', 'operator@example.com', 'pass', role=User.SELLER,
        )
        self.client.force_login(operator)
        self.assertEqual(self.client.get(reverse('category_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('category_create')).status_code, 403)

        api = APIClient()
        api.force_authenticate(operator)
        self.assertEqual(
            api.post(reverse('api-category-list'), {'name': 'Interdite'}, format='json').status_code,
            403,
        )
