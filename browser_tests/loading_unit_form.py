"""Run explicitly: manage.py test browser_tests.loading_unit_form."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from apps.inventory.models import Product, ProductPackaging, Unit


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
class LoadingUnitFormBrowserTests(StaticLiveServerTestCase):
    def test_loading_form_selects_carton_and_submits_mixed_lines(self):
        manager = get_user_model().objects.create_user(
            username='loading-unit-browser', password='StrongPass123!', role='manager',
        )
        operator = get_user_model().objects.create_user(
            username='loading-unit-operator', password='StrongPass123!',
        )
        unit = Unit.objects.create(name='Pièce navigateur chargement')
        product = Product.objects.create(
            name='Produit chargement navigateur', unit=unit,
            unit_mode=Product.UnitMode.CARTON_AND_PIECE,
            purchase_price=10, sale_price=20, quantity=245,
        )
        carton = ProductPackaging.objects.create(
            product=product, name='Carton 24', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price=480,
        )
        self.client.force_login(manager)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                page = browser.new_page()
                page.context.add_cookies([{
                    'name': settings.SESSION_COOKIE_NAME,
                    'value': session_cookie,
                    'url': self.live_server_url,
                }])
                page.goto(f'{self.live_server_url}/inventory/loading-orders/new/')
                page.select_option('#id_operator', str(operator.pk))
                payload = {
                    'id': product.pk,
                    'name': product.name,
                    'reference': product.reference,
                    'stock': 245,
                    'loading_units': [
                        {'id': None, 'name': unit.name, 'factor': 1},
                        {'id': carton.pk, 'name': carton.name, 'factor': 24},
                    ],
                }
                page.evaluate(
                    "product => window.ProductAutocomplete.select(document.querySelector('.product-picker'), product)",
                    payload,
                )
                expect(page.locator('#id_lines-0-packaging')).to_be_enabled()
                page.select_option('#id_lines-0-packaging', str(carton.pk))
                page.fill('#id_lines-0-quantity', '10')

                page.click('#add-loading-line')
                page.evaluate(
                    "product => window.ProductAutocomplete.select(document.querySelectorAll('.product-picker')[1], product)",
                    payload,
                )
                page.fill('#id_lines-1-quantity', '5')
                page.locator('#loading-order-form button.btn-success').click()
                page.wait_for_url('**/inventory/loading-orders/')
                expect(page.locator('body')).to_contain_text('10 Carton 24')
                expect(page.locator('body')).to_contain_text('5 Pièce navigateur chargement')
            finally:
                browser.close()
