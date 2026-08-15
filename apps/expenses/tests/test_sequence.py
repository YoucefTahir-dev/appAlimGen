from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.expenses.models import Expense, ExpenseCategory, ExpenseSequence


class ExpenseSequenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='expense-sequence-user')
        self.category = ExpenseCategory.objects.create(name='Séquence')

    def create_expense(self, description):
        return Expense.objects.create(
            category=self.category,
            description=description,
            amount='10.00',
            created_by=self.user,
        )

    def test_expense_number_is_not_reused_after_deletion(self):
        first = self.create_expense('Première')
        first_number = int(first.number.rsplit('-', 1)[1])
        year = int(first.number.split('-')[1])
        first.delete()

        second = self.create_expense('Deuxième')

        self.assertEqual(int(second.number.rsplit('-', 1)[1]), first_number + 1)
        self.assertEqual(
            ExpenseSequence.objects.get(year=year).last_number,
            first_number + 1,
        )
