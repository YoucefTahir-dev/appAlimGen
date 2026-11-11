"""Financial Django admin is an audit surface, never a business write path."""

from unittest.mock import Mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.commerce.models import (
    InvoiceSequence, OpeningBalancePayment, Payment, Purchase, PurchaseLine,
    Sale, SaleLine, TicketSequence,
)
from apps.inventory.models import (
    Brand, Category, Client, OperatorStockMovement, Product, StockMovement,
    Supplier, Unit,
)


class FinancialAdminSecurityTests(TestCase):
    models = (
        Purchase, PurchaseLine, Sale, SaleLine, Payment, OpeningBalancePayment,
        InvoiceSequence, TicketSequence,
    )

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(
            username='financial-auditor', password='Audit-test-pass-123!',
            role=User.SELLER, is_staff=True,
        )
        cls.staff.groups.clear()
        cls.staff.user_permissions.clear()
        cls.superuser = User.objects.create_superuser(
            username='financial-owner', password='Owner-test-pass-123!',
        )
        category = Category.objects.create(name='Audit category')
        brand = Brand.objects.create(name='Audit brand')
        unit = Unit.objects.create(name='Audit unit')
        cls.product = Product.objects.create(
            name='Audit product', category=category, brand=brand, unit=unit,
            purchase_price='5.00', sale_price='10.00', quantity=20,
        )
        cls.customer = Client.objects.create(name='Audit customer', balance='100.00')
        supplier = Supplier.objects.create(name='Audit supplier')
        purchase = Purchase.objects.create(
            reference='AUDIT-PURCHASE', supplier=supplier, total='100.00',
        )
        purchase_line = PurchaseLine.objects.create(
            purchase=purchase, product=cls.product, quantity=3, purchase_price='5.00',
        )
        sale = Sale.objects.create(
            invoice_number='AUDIT-SALE', ticket_number='AUDIT-TICKET',
            client=cls.customer, total='100.00',
        )
        sale_line = SaleLine.objects.create(
            sale=sale, product=cls.product, quantity=2, unit_price='10.00',
            unit_cost='5.00',
        )
        payment = Payment.objects.create(
            sale=sale, amount='25.00', payment_type=Payment.CASH,
            created_by=cls.superuser,
        )
        opening_payment = OpeningBalancePayment.objects.create(
            client=cls.customer, amount='10.00', payment_type=Payment.CASH,
            created_by=cls.superuser,
        )
        invoice_sequence = InvoiceSequence.objects.create(year=2041, last_number=17)
        ticket_sequence = TicketSequence.objects.create(year=2041, last_number=23)
        cls.objects_by_model = {
            Purchase: purchase, PurchaseLine: purchase_line,
            Sale: sale, SaleLine: sale_line, Payment: payment,
            OpeningBalancePayment: opening_payment,
            InvoiceSequence: invoice_sequence, TicketSequence: ticket_sequence,
        }

    def grant(self, *verbs):
        names = [f'{verb}_{model._meta.model_name}' for model in self.models for verb in verbs]
        self.staff.user_permissions.set(Permission.objects.filter(
            content_type__app_label='commerce', codename__in=names,
        ))
        self.client.force_login(self.staff)

    @staticmethod
    def url(model, action, obj=None):
        name = f'admin:commerce_{model._meta.model_name}_{action}'
        return reverse(name, args=[obj.pk] if obj is not None else None)

    def financial_state(self):
        # Catch changes to totals, line snapshots, stock journals, paid amounts,
        # opening debt and numbering -- not merely a changed row count.
        return {
            model._meta.label_lower: list(model.objects.order_by('pk').values())
            for model in (*self.models, Product, Client, StockMovement, OperatorStockMovement)
        }

    def test_view_permission_allows_readonly_lists_details_and_history(self):
        self.grant('view')
        before = self.financial_state()
        for model, obj in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(self.url(model, 'changelist')).status_code, 200)
                response = self.client.get(self.url(model, 'change', obj))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['has_add_permission'])
                self.assertFalse(response.context['has_change_permission'])
                self.assertFalse(response.context['has_delete_permission'])
                self.assertEqual(response.context['adminform'].form.fields, {})
                self.assertEqual(response.context['inline_admin_formsets'], [])
                self.assertEqual(self.client.get(self.url(model, 'history', obj)).status_code, 200)
        self.assertEqual(self.financial_state(), before)

    def test_change_permission_retains_django_audit_view_semantics(self):
        self.grant('change')
        for model, obj in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                response = self.client.get(self.url(model, 'change', obj))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['has_change_permission'])

    def test_staff_without_model_view_or_change_is_forbidden(self):
        self.client.force_login(self.staff)
        for model, obj in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(self.url(model, 'changelist')).status_code, 403)
                self.assertEqual(self.client.get(self.url(model, 'change', obj)).status_code, 403)
                self.assertEqual(self.client.get(self.url(model, 'history', obj)).status_code, 403)

    def test_explicit_model_permission_denials_are_honored(self):
        self.grant('view', 'change')
        self.staff.denied_permissions.set(self.staff.user_permissions.all())
        self.client.force_login(self.staff)
        for model, obj in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(self.url(model, 'changelist')).status_code, 403)
                self.assertEqual(self.client.get(self.url(model, 'change', obj)).status_code, 403)

    def test_denied_view_without_change_permission_is_forbidden(self):
        self.grant('view')
        self.staff.denied_permissions.set(self.staff.user_permissions.all())
        self.client.force_login(self.staff)
        for model, obj in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(self.url(model, 'changelist')).status_code, 403)
                self.assertEqual(self.client.get(self.url(model, 'change', obj)).status_code, 403)

    def test_nonstaff_cannot_access_audit_even_with_model_permissions(self):
        self.grant('view')
        get_user_model().objects.filter(pk=self.staff.pk).update(is_staff=False)
        for model in self.models:
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(self.url(model, 'changelist')).status_code, 302)

    def test_every_financial_admin_rejects_forged_writes_even_for_superuser(self):
        self.grant('add', 'view', 'change', 'delete')
        payload = {
            'reference': 'FORGED', 'invoice_number': 'FORGED',
            'ticket_number': 'FORGED', 'total': '9999.00', 'amount': '9999.00',
            'quantity': '999', 'purchase_price': '999.00', 'unit_price': '999.00',
            'last_number': '999999', 'client': self.customer.pk,
            'product': self.product.pk, '_save': 'Save',
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0',
            'lines-0-product': self.product.pk, 'lines-0-quantity': '999',
            'lines-0-unit_price': '999.00',
        }
        before = self.financial_state()
        for user in (self.staff, self.superuser):
            self.client.force_login(user)
            for model, obj in self.objects_by_model.items():
                with self.subTest(user=user.username, model=model.__name__):
                    for action in ('add', 'delete'):
                        target = obj if action == 'delete' else None
                        self.assertEqual(self.client.get(self.url(model, action, target)).status_code, 403)
                    self.assertEqual(self.client.post(self.url(model, 'add'), payload).status_code, 403)
                    self.assertEqual(self.client.post(self.url(model, 'change', obj), payload).status_code, 403)
                    self.assertEqual(self.client.post(
                        self.url(model, 'delete', obj), {'post': 'yes'},
                    ).status_code, 403)
                    for action in ('delete_selected', 'forged_action'):
                        self.assertEqual(self.client.post(self.url(model, 'changelist'), {
                            'action': action, '_selected_action': [obj.pk],
                            'index': 0, 'select_across': 0,
                        }).status_code, 403)
                    self.assertEqual(self.client.post(
                        self.url(model, 'changelist'), {**payload, '_save': 'Save'},
                    ).status_code, 403)
                    self.assertEqual(self.financial_state(), before)

    def test_all_fields_are_readonly_with_no_actions_inlines_or_autocomplete(self):
        self.assertEqual(
            {model for model in admin.site._registry if model._meta.app_label == 'commerce'},
            set(self.models),
        )
        request = RequestFactory().get('/admin/')
        request.user = self.superuser
        for model, obj in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                field_names = {field.name for field in (
                    *model._meta.concrete_fields, *model._meta.many_to_many,
                )}
                self.assertTrue(field_names <= set(model_admin.get_readonly_fields(request, obj)))
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request, obj))
                self.assertFalse(model_admin.has_delete_permission(request, obj))
                self.assertEqual(model_admin.get_actions(request), {})
                self.assertEqual(model_admin.get_inline_instances(request, obj), [])
                self.assertEqual(model_admin.get_autocomplete_fields(request), ())
                self.assertEqual(model_admin.list_editable, ())

    def test_direct_admin_write_hooks_fail_closed_without_queries(self):
        request = RequestFactory().post('/admin/')
        request.user = self.superuser
        before = self.financial_state()
        for model, obj in self.objects_by_model.items():
            model_admin = admin.site._registry[model]
            hooks = (
                lambda: model_admin.save_model(request, obj, Mock(), False),
                lambda: model_admin.save_model(request, obj, Mock(), True),
                lambda: model_admin.save_related(request, Mock(), [], True),
                lambda: model_admin.delete_model(request, obj),
                lambda: model_admin.delete_queryset(request, model.objects.all()),
            )
            for hook in hooks:
                with self.subTest(model=model.__name__, hook=hook):
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        hook()
        self.assertEqual(self.financial_state(), before)
