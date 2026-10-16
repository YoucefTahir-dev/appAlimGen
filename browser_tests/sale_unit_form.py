"""Run explicitly: manage.py test browser_tests.sale_unit_form."""

import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from apps.inventory.models import Category, Client, Product, ProductPackaging, Unit


@override_settings(
    DEBUG=True,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    SECURE_SSL_REDIRECT=False,
    LANGUAGE_COOKIE_SECURE=False,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class SaleUnitFormBrowserTests(StaticLiveServerTestCase):
    def test_mixed_carton_and_piece_totals_are_updated_instantly(self):
        user = get_user_model().objects.create_user(
            username='sale-unit-browser', password='StrongPass123!', role='manager',
        )
        customer = Client.objects.create(name='Client navigateur')
        unit = Unit.objects.create(name='Pièce navigateur vente')
        product = Product.objects.create(
            name='Produit vente navigateur',
            category=Category.objects.create(name='Catégorie vente navigateur'),
            unit=unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('100.00'),
            sale_price=Decimal('220.00'),
            super_wholesale_price=Decimal('205.00'),
            wholesale_price=Decimal('210.00'),
            retail_price=Decimal('220.00'),
            quantity=100,
        )
        carton = ProductPackaging.objects.create(
            product=product, name='Carton 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price=Decimal('4800.00'),
            super_wholesale_price=Decimal('4500.00'),
            wholesale_price=Decimal('4650.00'), retail_price=Decimal('4800.00'),
        )
        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'),
            )
            try:
                page = browser.new_page()
                page.context.add_cookies([{
                    'name': settings.SESSION_COOKIE_NAME,
                    'value': session_cookie,
                    'url': self.live_server_url,
                }])
                page.goto(f'{self.live_server_url}/commerce/sales/new/')
                page.select_option('#id_client', str(customer.pk))
                selected_product = {
                    'id': product.pk,
                    'name': product.name,
                    'reference': product.reference,
                    'stock': 100,
                    'price': '220.00',
                }
                page.evaluate(
                    """product => window.ProductAutocomplete.select(
                        document.querySelector('.product-picker'), product
                    )""",
                    selected_product,
                )

                expect(page.locator('#id_lines-0-packaging')).to_be_enabled()
                expect(page.locator(f'#id_lines-0-packaging option[value="{carton.pk}"]')).to_have_count(1)
                page.select_option('#id_lines-0-packaging', str(carton.pk))
                expect(page.locator('#id_lines-0-unit_price')).to_have_value('4800.00')
                page.fill('#id_lines-0-quantity', '2')
                expect(page.locator('.sale-line-row').nth(0).locator('.sale-stock-quantity')).to_have_text('48')
                expect(page.locator('.sale-line-row').nth(0).locator('.sale-line-total')).to_contain_text('9')

                page.click('#add-sale-line')
                page.evaluate(
                    """product => window.ProductAutocomplete.select(
                        document.querySelectorAll('.product-picker')[1], product
                    )""",
                    selected_product,
                )
                expect(page.locator('#id_lines-1-unit_price')).to_have_value('220.00')
                page.fill('#id_lines-1-quantity', '5')
                expect(page.locator('.sale-line-row').nth(1).locator('.sale-stock-quantity')).to_have_text('5')
                expect(page.locator('#sale-total-ht')).to_have_attribute('data-cents', '1070000')

                page.fill('#id_discount', '100.00')
                page.fill('#id_tax_rate', '19.00')
                expect(page.locator('#sale-tax-total')).to_have_attribute('data-cents', '203300')
                expect(page.locator('#sale-discount-total')).to_have_attribute('data-cents', '10000')
                expect(page.locator('#sale-total-ttc')).to_have_attribute('data-cents', '1263300')
                expect(page.locator('#sale-paid-total')).to_have_attribute('data-cents', '1263300')
                expect(page.locator('#sale-due-total')).to_have_attribute('data-cents', '0')

                page.select_option('#id_settlement_action', 'pay_partial')
                expect(page.locator('#sale-amount-received-field')).to_be_visible()
                page.fill('#id_amount_received', '5000.00')
                expect(page.locator('#sale-paid-total')).to_have_attribute('data-cents', '500000')
                expect(page.locator('#sale-due-total')).to_have_attribute('data-cents', '763300')

                page.select_option('#id_settlement_action', 'no_payment')
                expect(page.locator('#sale-amount-received-field')).to_be_hidden()
                expect(page.locator('#sale-paid-total')).to_have_attribute('data-cents', '0')
                expect(page.locator('#sale-due-total')).to_have_attribute('data-cents', '1263300')

                submit_guard = page.evaluate(
                    """() => {
                        const form = document.querySelector('#sale-form');
                        const first = form.dispatchEvent(new Event('submit', {cancelable: true}));
                        const second = form.dispatchEvent(new Event('submit', {cancelable: true}));
                        return {first, second};
                    }"""
                )
                self.assertEqual(submit_guard, {'first': True, 'second': False})
            finally:
                browser.close()
