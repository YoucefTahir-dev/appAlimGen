"""Run explicitly: manage.py test browser_tests.purchase_quick_product.

Requires local Playwright and Microsoft Edge (or PLAYWRIGHT_CHANNEL).
Only the isolated Django test database is used.
"""
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from apps.inventory.models import Product, Supplier, StockMovement
from apps.commerce.models import Purchase


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
class PurchaseQuickProductBrowserTests(StaticLiveServerTestCase):
    def test_modal_preserves_purchase_and_targets_dynamic_line(self):
        user = get_user_model().objects.create_user(username='browser-buyer', role='manager')
        self.client.force_login(user)
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        supplier = Supplier.objects.create(name='ABC Distribution')
        existing = Product.objects.create(name='Coca-Cola', purchase_price=80, sale_price=100)
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for language in ('fr', 'ar', 'en'):
                    with self.subTest(language=language):
                        viewport = {'width': 1440, 'height': 1000} if language == 'fr' else {'width': 390, 'height': 844}
                        context = browser.new_context(viewport=viewport)
                        context.add_cookies([
                            {'name': settings.SESSION_COOKIE_NAME, 'value': cookie, 'url': self.live_server_url},
                            {'name': settings.LANGUAGE_COOKIE_NAME, 'value': language, 'url': self.live_server_url},
                        ])
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(self.live_server_url + '/commerce/purchases/new/')
                        page.locator('#id_reference').fill('BROWSER-' + language)
                        page.locator('#id_supplier').select_option(str(supplier.pk))
                        page.locator('#id_tax_rate').fill('19')
                        page.locator('#id_lines-0-product').select_option(str(existing.pk))
                        page.locator('#id_lines-0-quantity').fill('20')
                        page.locator('#id_lines-0-purchase_price').fill('80')
                        page.locator('#add-purchase-line').click()
                        row = page.locator('.purchase-line-row').nth(1)
                        row.locator('.quick-product-open').click()
                        modal = page.locator('#quick-product-modal')
                        expect(modal).to_be_visible()
                        page.locator('#id_quick-name').fill('Cancelled product')
                        modal.locator('.modal-footer [data-bs-dismiss]').click()
                        expect(modal).not_to_be_visible()
                        expect(page.locator('#id_lines-0-quantity')).to_have_value('20')
                        row.locator('.quick-product-open').click()
                        expect(page.locator('#id_quick-name')).to_have_value('')
                        for field, value in {
                            'name': 'Sprite ' + language, 'brand_text': 'Sprite',
                            'purchase_price': '80', 'super_wholesale_price': '90',
                            'wholesale_price': '100', 'retail_price': '70',
                        }.items():
                            page.locator('#id_quick-' + field).fill(value)
                        modal.locator('[type=submit]').click()
                        expect(modal.locator('[data-errors=retail_price]')).not_to_be_empty()
                        expect(modal).to_be_visible()
                        page.locator('#id_quick-retail_price').fill('110')
                        output = Path(settings.BASE_DIR) / 'tmp' / 'purchase-browser'
                        output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(output / f'modal-{language}.png'), full_page=True)
                        modal.locator('[type=submit]').click()
                        expect(modal).not_to_be_visible()
                        expect(row.locator('select[name$="-product"] option:checked')).to_have_text('Sprite ' + language)
                        expect(row.locator('input[name$="-purchase_price"]')).to_have_value('80.00')
                        expect(page.locator('#id_lines-0-product')).to_have_value(str(existing.pk))
                        expect(page.locator('#id_lines-0-quantity')).to_have_value('20')
                        expect(page.locator('#id_supplier')).to_have_value(str(supplier.pk))
                        expect(page.locator('#id_tax_rate')).to_have_value('19')
                        expect(page.locator('#id_reference')).to_have_value('BROWSER-' + language)
                        row.locator('input[name$="-quantity"]').fill('100')
                        page.locator('#add-purchase-line').click()
                        expect(page.locator('#id_lines-2-product option').filter(has_text='Sprite ' + language)).to_have_count(1)
                        page.locator('#id_lines-2-DELETE').check()
                        page.locator('#purchase-form .table-responsive').evaluate('(element) => { element.scrollLeft = 0; }')
                        page.screenshot(path=str(output / f'purchase-{language}.png'), full_page=True)
                        page.locator('#purchase-form button.btn-success').click()
                        expect(page).to_have_url(self.live_server_url + '/commerce/purchases/')
                        context.close()
            finally:
                browser.close()
        self.assertEqual(errors, [])
        self.assertFalse(Product.objects.filter(name='Cancelled product').exists())
        for language in ('fr', 'ar', 'en'):
            product = Product.objects.get(name='Sprite ' + language)
            self.assertEqual(product.quantity, 100)
            self.assertEqual(StockMovement.objects.filter(product=product).count(), 1)
            self.assertEqual(Purchase.objects.get(reference='BROWSER-' + language).lines.count(), 2)
