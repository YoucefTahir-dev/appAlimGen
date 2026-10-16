"""Run explicitly: manage.py test browser_tests.product_unit_form."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright


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
class ProductUnitFormBrowserTests(StaticLiveServerTestCase):
    def test_price_conversions_and_mixed_stock_are_explicit_and_safe(self):
        user = get_user_model().objects.create_user(
            username='product-unit-browser', password='StrongPass123!', role='manager',
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
                page.goto(f'{self.live_server_url}/inventory/products/new/')

                expect(page.locator('#id_unit_mode option[value="legacy"]')).to_have_count(0)
                page.select_option('#id_unit_mode', 'carton_and_piece')
                page.fill('#id_carton-conversion_factor', '30')
                page.fill('#id_initial_cartons', '40')
                page.fill('#id_initial_pieces', '15')
                page.fill('#id_carton-reference_purchase_price', '4500')
                page.locator('#id_carton-reference_purchase_price').blur()

                expect(page.locator('#carton-equivalence')).to_have_text(
                    '1 carton = 30 pièce(s)',
                )
                expect(page.locator('#stock-conversion-preview')).to_contain_text(
                    '1215 pièce(s)',
                )
                expect(page.locator('#id_purchase_price')).to_have_value('150.00')

                page.fill('#id_purchase_price', '160')
                page.locator('[data-piece-id="id_purchase_price"] [data-price-convert="piece-to-carton"]').click()
                expect(page.locator('#id_carton-reference_purchase_price')).to_have_value('4800.00')

                page.fill('#id_carton-reference_purchase_price', '4700')
                page.fill('#id_carton-conversion_factor', '25')
                expect(page.locator('#id_purchase_price')).to_have_value('160')
                expect(page.locator('#id_carton-reference_purchase_price')).to_have_value('4700')

                for piece_id, carton_id in (
                    ('id_super_wholesale_price', 'id_carton-super_wholesale_price'),
                    ('id_wholesale_price', 'id_carton-wholesale_price'),
                    ('id_retail_price', 'id_carton-retail_price'),
                ):
                    page.fill(f'#{piece_id}', '200')
                    page.locator(
                        f'[data-piece-id="{piece_id}"] [data-price-convert="piece-to-carton"]'
                    ).click()
                    expect(page.locator(f'#{carton_id}')).to_have_value('5000.00')
            finally:
                browser.close()
