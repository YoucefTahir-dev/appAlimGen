"""Run explicitly: manage.py test browser_tests.purchase_unit_form."""

import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from apps.inventory.models import Brand, Category, Product, ProductPackaging, Unit


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
class PurchaseUnitFormBrowserTests(StaticLiveServerTestCase):
    def test_carton_selection_converts_stock_and_preserves_negotiated_price(self):
        user = get_user_model().objects.create_user(
            username='purchase-unit-browser', password='StrongPass123!', role='manager',
        )
        unit = Unit.objects.create(name='Pièce navigateur')
        product = Product.objects.create(
            name='Produit achat navigateur',
            category=Category.objects.create(name='Catégorie achat navigateur'),
            brand=Brand.objects.create(name='Marque achat navigateur'),
            unit=unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=Decimal('220.00'),
            sale_price=Decimal('300.00'),
            quantity=0,
        )
        carton = ProductPackaging.objects.create(
            product=product,
            name='Carton 24',
            kind=ProductPackaging.CARTON,
            conversion_factor=24,
            reference_purchase_price=Decimal('4000.00'),
            default_sale_price=Decimal('7200.00'),
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
                page.goto(f'{self.live_server_url}/commerce/purchases/new/')
                page.evaluate(
                    """product => window.ProductAutocomplete.select(
                        document.querySelector('.product-picker'), product
                    )""",
                    {
                        'id': product.pk,
                        'name': product.name,
                        'purchase_price': '220.00',
                        'purchase_units': [
                            {
                                'id': None,
                                'name': unit.name,
                                'factor': 1,
                                'purchase_price': '220.00',
                            },
                            {
                                'id': carton.pk,
                                'name': carton.name,
                                'factor': 24,
                                'purchase_price': '4000.00',
                            },
                        ],
                    },
                )

                page.select_option('#id_lines-0-packaging', str(carton.pk))
                page.fill('#id_lines-0-quantity', '100')
                expect(page.locator('#id_lines-0-purchase_price')).to_have_value('4000.00')
                expect(page.locator('.purchase-stock-quantity')).to_have_text('2400')
                expect(page.locator('.purchase-line-total')).to_contain_text('400')

                page.fill('#id_lines-0-purchase_price', '4100')
                page.fill('#id_lines-0-quantity', '100')
                page.fill('#id_tax_rate', '10')
                expect(page.locator('#id_lines-0-purchase_price')).to_have_value('4100')
                expect(page.locator('#purchase-total-ht')).to_contain_text('410')
                expect(page.locator('#purchase-total-ttc')).to_contain_text('451')
            finally:
                browser.close()
