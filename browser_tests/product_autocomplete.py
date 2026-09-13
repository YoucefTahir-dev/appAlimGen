"""Real DOM and HTTP form workflows, isolated test database; no production access."""
import os
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from apps.commerce.models import Sale
from apps.inventory.models import Client, Product, ProductPackaging


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
    SECURE_SSL_REDIRECT=False, LANGUAGE_COOKIE_SECURE=False, STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
class AutocompleteBrowserTests(StaticLiveServerTestCase):
    def test_sales_multiline_pricing_search_and_rtl(self):
        user = get_user_model().objects.create_user(username='search-browser', role='manager')
        self.client.force_login(user)
        first = Product.objects.create(name='Chocolat Noir', purchase_price=10, sale_price=20,
            wholesale_price=18, super_wholesale_price=15, quantity=100)
        second = Product.objects.create(name='Biscuit', purchase_price=5, sale_price=10, quantity=100)
        pack = ProductPackaging.objects.create(product=first, name='Carton', conversion_factor=2, default_sale_price=40)
        cases = [(language, Client.objects.create(name='Customer ' + language, customer_type=kind), price)
                 for language, kind, price in [('fr', 'RETAIL', '20.00'), ('ar', 'WHOLESALE', '18.00'), ('en', 'SUPER_WHOLESALE', '15.00')]]
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for language, customer, price in cases:
                    context = browser.new_context(viewport={'width': 1366 if language == 'fr' else 390, 'height': 900})
                    context.add_cookies([
                        {'name': settings.SESSION_COOKIE_NAME, 'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url},
                        {'name': settings.LANGUAGE_COOKIE_NAME, 'value': language, 'url': self.live_server_url},
                    ])
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    searches = []
                    page.on('request', lambda r: searches.append(r.url) if '/products/search/' in r.url else None)
                    page.goto(self.live_server_url + '/commerce/sales/new/')
                    expect(page.locator('html')).to_have_attribute('dir', 'rtl' if language == 'ar' else 'ltr')
                    field = page.locator('#id_lines-0-product_search')
                    expect(field).to_have_value('')
                    self.assertEqual(searches, [])
                    page.locator('#id_client').select_option(str(customer.pk))
                    field.fill('C')
                    page.wait_for_timeout(400)
                    self.assertEqual(searches, [])
                    field.fill('Ch'); field.fill('Cho'); field.fill('Choc')
                    expect(page.locator('#id_lines-0-product_results .product-result')).to_have_count(1)
                    self.assertEqual(len(searches), 1)
                    field.press('ArrowDown'); field.press('Enter')
                    expect(page.locator('#id_lines-0-product')).to_have_value(str(first.pk))
                    expect(page.locator('#id_lines-0-unit_price')).to_have_value(price)
                    page.locator('#id_lines-0-packaging').select_option(str(pack.pk))
                    expect(page.locator('#id_lines-0-unit_price')).to_have_value(f'{Decimal(price) * 2:.2f}')
                    page.locator('#id_lines-0-quantity').fill('1')
                    page.locator('#add-sale-line').click()
                    field2 = page.locator('#id_lines-1-product_search')
                    field2.fill('Bisc')
                    page.locator('#id_lines-1-product_results .product-result').click()
                    expect(page.locator('#id_lines-1-product')).to_have_value(str(second.pk))
                    expect(page.locator('#id_lines-1-unit_price')).to_have_value('10.00')
                    # Changing text must invalidate the old ID and prevent accidental submit.
                    field2.fill('Unselected')
                    expect(page.locator('#id_lines-1-product')).to_have_value('')
                    self.assertFalse(field2.evaluate('(el) => el.checkValidity()'))
                    field2.fill('Bisc')
                    page.locator('#id_lines-1-product_results .product-result').click()
                    expect(page.locator('#id_lines-1-unit_price')).to_have_value('10.00')
                    page.locator('#id_lines-1-quantity').fill('3')
                    expect(page.locator('#id_lines-0-product')).to_have_value(str(first.pk))
                    output = Path(settings.BASE_DIR) / 'tmp' / 'product-autocomplete'
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(output / f'sale-{language}.png'), full_page=True)
                    page.locator('#sale-form .form-actions button.btn-success').click()
                    expect(page).to_have_url(self.live_server_url + '/commerce/sales/')
                    context.close()
            finally:
                browser.close()
        first.refresh_from_db(); second.refresh_from_db()
        for _language, customer, price in cases:
            sale = Sale.objects.get(client=customer)
            self.assertEqual(sale.total, Decimal(price) * 2 + 30)
            self.assertEqual(sale.lines.count(), 2)
            self.assertEqual(sale.amount_paid, sale.total)
        self.assertEqual(first.quantity, 94)
        self.assertEqual(second.quantity, 91)
        self.assertEqual(errors, [])
