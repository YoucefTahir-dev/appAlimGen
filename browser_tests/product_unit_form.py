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
    def test_carton_preview_updates_without_overwriting_piece_price(self):
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
                page.fill('#id_carton-conversion_factor', '24')
                page.fill('#id_quantity', '100')
                page.select_option('#id_stock_unit', 'carton')
                page.fill('#id_purchase_price', '160')
                page.fill('#id_carton-reference_purchase_price', '4000')

                expect(page.locator('#carton-equivalence')).to_have_text(
                    '1 carton = 24 pièce(s)',
                )
                expect(page.locator('#stock-conversion-preview')).to_contain_text(
                    '2400 pièce(s)',
                )
                expect(page.locator('#piece-cost-hint')).to_contain_text('166.67 DZD/pièce')
                expect(page.locator('#id_purchase_price')).to_have_value('160')
            finally:
                browser.close()

