"""Explicit local tests; GPS and geocoding are simulated, never real Google calls."""
import os
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from apps.inventory.models import Client
from apps.inventory.geocoding import GeocodingUnavailable


@override_settings(
    DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
    SECURE_SSL_REDIRECT=False, LANGUAGE_COOKIE_SECURE=False,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class ClientLocationBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='gps-test', role='manager')
        self.client.force_login(self.user)
        self.cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def context(self, browser, language='fr', width=390):
        context = browser.new_context(
            viewport={'width': width, 'height': 844},
            permissions=['geolocation'], geolocation={'latitude': 36.3745, 'longitude': 3.9012, 'accuracy': 12},
        )
        context.add_cookies([
            {'name': settings.SESSION_COOKIE_NAME, 'value': self.cookie, 'url': self.live_server_url},
            {'name': settings.LANGUAGE_COOKIE_NAME, 'value': language, 'url': self.live_server_url},
        ])
        return context

    def test_mobile_confirmation_save_and_maps_in_three_languages(self):
        with sync_playwright() as playwright, patch('apps.inventory.views.GeocodingService.reverse_geocode', return_value={'formatted_address': 'Bouira, Algérie', 'place_id': 'mock-place'}):
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for language in ('fr', 'ar', 'en'):
                    context = self.context(browser, language)
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(self.live_server_url + '/inventory/clients/new/')
                    expect(page.locator('html')).to_have_attribute('dir', 'rtl' if language == 'ar' else 'ltr')
                    page.locator('#id_name').fill('GPS ' + language)
                    page.locator('#id_address').fill('Adresse manuelle')
                    page.locator('#locate-client').click()
                    expect(page.locator('#confirm-location')).to_be_enabled()
                    expect(page.locator('#id_latitude')).to_have_value('')
                    expect(page.locator('#id_address')).to_have_value('Adresse manuelle')
                    page.locator('#cancel-location').click()
                    expect(page.locator('#id_latitude')).to_have_value('')
                    page.locator('#locate-client').click()
                    expect(page.locator('#confirm-location')).to_be_enabled()
                    path = Path(settings.BASE_DIR) / 'tmp' / 'client-location'
                    path.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(path / f'proposal-{language}.png'), full_page=True)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.locator('#confirm-location').click()
                    expect(page.locator('#id_address')).to_have_value('Bouira, Algérie')
                    expect(page.locator('#id_latitude')).to_have_value('36.3745')
                    expect(page.locator('#client-maps-link')).to_have_attribute('href', 'https://www.google.com/maps/search/?api=1&query=36.3745%2C3.9012')
                    page.locator('#client-form .form-actions button').click()
                    expect(page).to_have_url(self.live_server_url + '/inventory/clients/')
                    page.get_by_role('link', name='GPS ' + language, exact=True).click()
                    page.locator('a[href$="/edit/"]').click()
                    expect(page.locator('#id_latitude')).to_have_value('36.3745')
                    expect(page.locator('#client-maps-link')).to_be_visible()
                    self.assertFalse(errors)
                    context.close()
            finally:
                browser.close()
        for language in ('fr', 'ar', 'en'):
            customer = Client.objects.get(name='GPS ' + language)
            self.assertEqual(customer.latitude, 36.3745)
            self.assertEqual(customer.location_accuracy, 12)
            self.assertEqual(customer.place_id, 'mock-place')

    def test_gps_errors_and_fallback_preserve_manual_address(self):
        with sync_playwright() as playwright, patch('apps.inventory.views.GeocodingService.reverse_geocode', side_effect=GeocodingUnavailable):
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for code, key in ((1, 'denied'), (2, 'unavailable'), (3, 'timeout'), (0, 'unsupported')):
                    context = self.context(browser)
                    script = "Object.defineProperty(navigator, 'geolocation', {value: undefined});" if not code else f"Object.defineProperty(navigator, 'geolocation', {{value: {{getCurrentPosition: (ok, fail) => fail({{code:{code}}})}}}});"
                    context.add_init_script(script)
                    page = context.new_page()
                    page.goto(self.live_server_url + '/inventory/clients/new/')
                    page.locator('#id_address').fill('Manuel')
                    page.locator('#locate-client').click()
                    message = page.locator('#client-location').get_attribute('data-' + key)
                    expect(page.locator('#location-status')).to_have_text(message)
                    expect(page.locator('#id_address')).to_have_value('Manuel')
                    expect(page.locator('#id_latitude')).to_have_value('')
                    context.close()
                context = self.context(browser, width=1366)
                context.set_geolocation({'latitude': 36.3745, 'longitude': 3.9012, 'accuracy': 180})
                page = context.new_page()
                page.goto(self.live_server_url + '/inventory/clients/new/')
                page.locator('#id_name').fill('GPS fallback')
                page.locator('#id_address').fill('Manuel')
                page.locator('#locate-client').click()
                expect(page.locator('#detected-address')).to_have_text(page.locator('#client-location').get_attribute('data-fallback'))
                expect(page.locator('#location-warning')).not_to_be_empty()
                expect(page.locator('#confirm-location')).to_be_disabled()
                page.locator('#keep-manual-address').click()
                expect(page.locator('#id_latitude')).to_have_value('36.3745')
                expect(page.locator('#id_address')).to_have_value('Manuel')
                page.locator('#client-form .form-actions button').click()
                expect(page).to_have_url(self.live_server_url + '/inventory/clients/')
                context.close()
            finally:
                browser.close()
        customer = Client.objects.get(name='GPS fallback')
        self.assertEqual(customer.address, 'Manuel')
        self.assertEqual(customer.location_accuracy, 180)
