from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.expenses.models import Expense, ExpenseCategory


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class ExpensePaginationTests(TestCase):
    def test_expense_list_is_paginated_but_total_covers_the_full_filter(self):
        User = get_user_model()
        user = User.objects.create_user(
            username='expense-pagination-manager',
            password='StrongPass123!',
            role=User.MANAGER,
        )
        self.client.force_login(user)
        category = ExpenseCategory.objects.create(name='Pagination')
        Expense.objects.bulk_create(
            [
                Expense(
                    number=f'CHG-PAGE-{index:03d}',
                    category=category,
                    description='Charge paginée',
                    amount='10.00',
                    created_by=user,
                )
                for index in range(30)
            ]
        )

        response = self.client.get(reverse('expense_list'), {'page': 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['expenses']), 5)
        self.assertEqual(response.context['total_amount'], 300)
