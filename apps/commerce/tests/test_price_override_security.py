from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.models import Sale
from apps.commerce.services import create_sale, update_sale
from apps.core.models import AuditLog
from apps.inventory.models import Category, Client, Product


class SalePriceOverrideSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='price-seller', password='unused', role='seller',
        )
        self.customer = Client.objects.create(
            name='Client prix fictif', customer_type=Client.CustomerType.RETAIL,
        )
        category = Category.objects.create(name='Prix fictifs')
        self.product = Product.objects.create(
            name='Produit prix fictif', category=category,
            purchase_price='40.00', super_wholesale_price='60.00',
            wholesale_price='80.00', retail_price='100.00',
            quantity=100, minimum_stock=0,
            unit_mode=Product.UnitMode.PIECE_ONLY,
        )

    def _lines(self, price='100.00', quantity=1):
        return [{
            'product': self.product,
            'quantity': quantity,
            'unit_price': price,
        }]

    def _grant_override(self):
        permission = Permission.objects.get(
            content_type__app_label='commerce', codename='override_sale_price',
        )
        self.user.user_permissions.add(permission)
        if hasattr(self.user, '_perm_cache'):
            del self.user._perm_cache

    def test_authoritative_customer_tariff_is_accepted_without_override(self):
        sale = create_sale(
            client=self.customer, lines=self._lines(), user=self.user,
        )
        self.assertEqual(sale.lines.get().unit_price, Decimal('100.00'))
        self.assertFalse(AuditLog.objects.filter(action__startswith='sale.price_override').exists())

    def test_higher_and_lower_custom_prices_are_rejected_without_permission(self):
        for price in ('90.00', '110.00'):
            with self.subTest(price=price), self.assertRaises(ValidationError) as caught:
                create_sale(
                    client=self.customer, lines=self._lines(price), user=self.user,
                )
            self.assertIn('sale_price_override_forbidden', {
                error.code
                for errors in caught.exception.error_dict.values()
                for error in errors
            })
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 100)
        self.assertFalse(Sale.objects.exists())

    def test_web_and_api_cannot_bypass_the_server_control(self):
        payload = {
            'client': self.customer.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'payment_type': Sale.CASH,
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '90.00',
        }
        self.client.force_login(self.user)
        web = self.client.post(reverse('sale_create'), payload)
        self.assertEqual(web.status_code, 200)
        self.assertContains(web, "permission de modifier le tarif")

        api = APIClient()
        api.force_authenticate(self.user)
        response = api.post(reverse('api-sale-list'), {
            'client': self.customer.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'payment_type': Sale.CASH,
            'items': [{
                'product': self.product.pk,
                'quantity': 1,
                'unit_price': '90.00',
            }],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error']['code'], 'SALE_PRICE_OVERRIDE_FORBIDDEN')
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 100)

    def test_authorized_override_is_logged_with_invoice_actor_and_prices(self):
        self._grant_override()
        sale = create_sale(
            client=self.customer, lines=self._lines('90.00'), user=self.user,
        )
        audit = AuditLog.objects.get(action__startswith='sale.price_override')
        self.assertEqual(audit.user, self.user)
        self.assertIn(f'invoice={sale.invoice_number}', audit.action)
        self.assertIn(f'product={self.product.pk}', audit.action)
        self.assertIn('tariff=100.00', audit.action)
        self.assertIn('actual=90.00', audit.action)

    def test_override_permission_never_allows_sale_below_purchase_cost(self):
        self._grant_override()
        with self.assertRaises(ValidationError) as caught:
            create_sale(
                client=self.customer, lines=self._lines('39.99'), user=self.user,
            )
        self.assertIn('sale_price_below_cost', {
            error.code
            for errors in caught.exception.error_dict.values()
            for error in errors
        })
        self.assertFalse(AuditLog.objects.filter(action__startswith='sale.price_override').exists())

    def test_unchanged_historical_custom_line_can_be_saved_without_regrant(self):
        self._grant_override()
        sale = create_sale(
            client=self.customer, lines=self._lines('90.00'), user=self.user,
        )
        self.user.user_permissions.clear()
        self.user = get_user_model().objects.get(pk=self.user.pk)
        updated = update_sale(
            sale=sale, client=self.customer,
            lines=self._lines('90.00'), user=self.user,
            show_legal_information=False,
        )
        self.assertEqual(updated.lines.get().unit_price, Decimal('90.00'))
        self.assertEqual(
            AuditLog.objects.filter(action__startswith='sale.price_override').count(),
            1,
        )

    def test_changing_quantity_at_historical_custom_price_requires_permission(self):
        self._grant_override()
        sale = create_sale(
            client=self.customer, lines=self._lines('90.00'), user=self.user,
        )
        self.user.user_permissions.clear()
        self.user = get_user_model().objects.get(pk=self.user.pk)
        before_stock = Product.objects.get(pk=self.product.pk).quantity
        with self.assertRaises(ValidationError):
            update_sale(
                sale=sale, client=self.customer,
                lines=self._lines('90.00', quantity=2), user=self.user,
            )
        sale.refresh_from_db()
        self.assertEqual(sale.lines.get().packaging_quantity, 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, before_stock)
