from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.commerce.models import Sale, SaleLine
from apps.expenses.models import Expense, ExpenseCategory
from apps.inventory.models import Brand, Category, Client, Product, Supplier, Unit


class ExpenseTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username='manager_exp', password='Pass12345!', role=User.MANAGER)
        self.seller = User.objects.create_user(username='seller_exp', password='Pass12345!', role=User.SELLER)
        self.client.force_login(self.manager)
        self.category, _ = ExpenseCategory.objects.get_or_create(name='Internet')
        self.supplier = Supplier.objects.create(name='Fournisseur charge')

    def expense_payload(self, **overrides):
        payload = {
            'date': '2026-08-14',
            'category': self.category.pk,
            'description': 'Abonnement internet',
            'amount': '1200.00',
            'payment_method': Expense.CASH,
            'supplier': self.supplier.pk,
            'observation': 'Payé',
        }
        payload.update(overrides)
        return payload

    def test_expense_create_update_search_and_delete(self):
        response = self.client.post(reverse('expense_create'), self.expense_payload())
        self.assertEqual(response.status_code, 302)
        expense = Expense.objects.latest('pk')
        self.assertRegex(expense.number, r'^CHG-\d{4}-000001$')
        self.assertEqual(expense.created_by, self.manager)

        response = self.client.get(reverse('expense_list'), {'q': 'internet'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, expense.number)

        response = self.client.post(reverse('expense_update', args=[expense.pk]), self.expense_payload(amount='1500.00'))
        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(str(expense.amount), '1500.00')

        response = self.client.post(reverse('expense_delete', args=[expense.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())

    def test_expense_exports_and_print(self):
        Expense.objects.create(
            category=self.category,
            description='Rapport export',
            amount='500.00',
            payment_method=Expense.TRANSFER,
            supplier=self.supplier,
            created_by=self.manager,
        )

        excel = self.client.get(reverse('expense_export_excel'))
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(excel['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        pdf = self.client.get(reverse('expense_export_pdf'))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))

        printable = self.client.get(reverse('expense_print'))
        self.assertEqual(printable.status_code, 200)
        self.assertContains(printable, 'Rapport des charges')

    def test_seller_cannot_access_expenses(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse('expense_list'))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_includes_expenses_and_net_profit(self):
        product_category = Category.objects.create(name='Cat')
        brand = Brand.objects.create(name='Brand')
        unit = Unit.objects.create(name='Unit')
        product = Product.objects.create(
            name='Produit bénéfice',
            category=product_category,
            brand=brand,
            unit=unit,
            purchase_price='10.00',
            sale_price='20.00',
            quantity=10,
            minimum_stock=1,
        )
        client_obj = Client.objects.create(name='Client bénéfice')
        sale = Sale.objects.create(invoice_number='FAC-DASH-001', client=client_obj, total='40.00', discount='0.00', tax_rate='0.00')
        SaleLine.objects.create(sale=sale, product=product, quantity=2, unit_price='20.00')
        Expense.objects.create(category=self.category, description='Charge bénéfice', amount='5.00', created_by=self.manager)

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Total des charges')
        self.assertContains(response, 'Gain brut (avant charges)')
        self.assertContains(response, 'Gain net (après charges)')
