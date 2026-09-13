"""Explicit local tests; GPS and geocoding are simulated, never real Google calls."""
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

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
    def test_poor_accuracy_preserves_existing_location_in_three_languages(self):
        with sync_playwright() as playwright, patch('apps.inventory.views.GeocodingService.reverse_geocode') as provider:
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for language in ('fr', 'ar', 'en'):
                    context = self.context(browser, language, width=1366)
                    context.set_geolocation({'latitude': 36, 'longitude': 3, 'accuracy': 1332})
                    page = context.new_page()
                    page.goto(self.live_server_url + '/inventory/clients/new/')
                    page.locator('#id_address').fill('Adresse existante')
                    page.evaluate("document.getElementById('id_latitude').value = '35'")
                    page.locator('#detect-location').click()
                    expect(page.locator('#location-status')).to_have_text(
                        page.locator('#client-location').get_attribute('data-imprecise'), timeout=18000)
                    expect(page.locator('#id_address')).to_have_value('Adresse existante')
                    expect(page.locator('#id_latitude')).to_have_value('35')
                    expect(page.locator('#detect-location')).to_be_enabled()
                    self.assertFalse(page.locator('#location-status').inner_text().endswith('1332'))
                    provider.assert_not_called()
                    context.close()
            finally:
                browser.close()

    def test_manual_input_and_submit_ignore_late_gps(self):
        with sync_playwright() as playwright, patch('apps.inventory.views.GeocodingService.reverse_geocode') as provider:
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                context = self.context(browser)
                context.add_init_script("""
                    window.gpsCalls = 0;
                    Object.defineProperty(navigator, 'geolocation', {value: {
                        watchPosition: (ok, fail, options) => { window.gpsCalls++; window.gpsOptions = options; window.delayedGPS = ok; return 1; },
                        clearWatch: () => {}
                    }});
                """)
                page = context.new_page()
                page.goto(self.live_server_url + '/inventory/clients/new/')
                self.assertEqual(page.evaluate('gpsCalls'), 0)
                page.locator('#id_name').fill('Manual while GPS pending')
                page.locator('#detect-location').click()
                self.assertEqual(page.evaluate('gpsOptions.maximumAge'), 0)
                expect(page.locator('#location-spinner')).to_be_visible()
                expect(page.locator('#detect-location')).to_be_disabled()
                page.locator('#id_address').fill('Adresse tapée pendant GPS')
                expect(page.locator('#detect-location')).to_be_enabled()
                page.evaluate('delayedGPS({coords:{latitude:36,longitude:3,accuracy:12}})')
                expect(page.locator('#id_address')).to_have_value('Adresse tapée pendant GPS')
                expect(page.locator('#id_latitude')).to_have_value('')
                page.locator('#detect-location').click()
                page.locator('#client-form .form-actions button').click()
                expect(page).to_have_url(self.live_server_url + '/inventory/clients/')
                provider.assert_not_called()
                context.close()
            finally:
                browser.close()
        customer = Client.objects.get(name='Manual while GPS pending')
        self.assertEqual(customer.address, 'Adresse tapée pendant GPS')
        self.assertIsNone(customer.latitude)

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

    def test_mobile_autofill_edit_and_save_in_three_languages(self):
        with sync_playwright() as playwright, patch('apps.inventory.views.GeocodingService.reverse_geocode', return_value={'formatted_address': 'Bouira, Algérie', 'place_id': 'mock-place'}):
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for language in ('fr', 'ar', 'en'):
                    context = self.context(browser, language)
                    if language == 'en':
                        context.set_geolocation({'latitude': 36.3745, 'longitude': 3.9012, 'accuracy': 80})
                    page = context.new_page()
                    errors = []
                    writes = []
                    lookups = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' and '/reverse-geocode/' not in request.url else None)
                    page.on('request', lambda request: lookups.append(request.post_data) if '/reverse-geocode/' in request.url else None)
                    page.goto(self.live_server_url + '/inventory/clients/new/')
                    expect(page.locator('html')).to_have_attribute('dir', 'rtl' if language == 'ar' else 'ltr')
                    if language != 'fr':
                        page.locator('#id_name').fill('GPS ' + language)
                    page.locator('#id_address').fill('Adresse manuelle')
                    for field in ('latitude', 'longitude', 'location_accuracy', 'formatted_address', 'place_id'):
                        expect(page.locator('#id_' + field)).to_have_attribute('type', 'hidden')
                        expect(page.locator('#id_' + field)).not_to_be_visible()
                    expect(page.locator('#location-proposal')).to_have_count(0)
                    page.locator('#detect-location').click()
                    expect(page.locator('#id_address')).to_have_value('Bouira, Algérie', timeout=18000)
                    expect(page.locator('#detect-location')).to_be_enabled()
                    if language == 'en':
                        expect(page.locator('#location-status')).to_have_text(page.locator('#client-location').get_attribute('data-weak'))
                        expect(page.locator('#location-status')).not_to_contain_text('180')
                    self.assertEqual(writes, [])
                    self.assertEqual(parse_qs(lookups[-1]), {'latitude': ['36.3745'], 'longitude': ['3.9012']})
                    page.locator('#id_name').fill('GPS ' + language)
                    path = Path(settings.BASE_DIR) / 'tmp' / 'client-location'
                    path.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(path / f'simple-{language}.png'), full_page=True)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    expect(page.locator('#id_latitude')).to_have_value('36.3745')
                    page.locator('#id_address').fill('Magasin Ahmed, Bouira')
                    page.locator('#client-form .form-actions button').click()
                    expect(page).to_have_url(self.live_server_url + '/inventory/clients/')
                    page.get_by_role('link', name='GPS ' + language, exact=True).click()
                    page.locator('a[href$="/edit/"]').click()
                    expect(page.locator('#id_latitude')).to_have_value('36.3745')
                    expect(page.locator('#id_latitude')).not_to_be_visible()
                    expect(page.locator('#id_address')).to_have_value('Magasin Ahmed, Bouira')
                    page.locator('#detect-location').click()
                    expect(page.locator('#id_address')).to_have_value('Bouira, Algérie', timeout=18000)
                    self.assertEqual(len(writes), 1)
                    page.locator('#id_address').fill('Adresse corrigée ' + language)
                    page.locator('#client-form .form-actions button').click()
                    expect(page).to_have_url(self.live_server_url + '/inventory/clients/')
                    self.assertFalse(errors)
                    context.close()
            finally:
                browser.close()
        for language in ('fr', 'ar', 'en'):
            customer = Client.objects.get(name='GPS ' + language)
            self.assertEqual(customer.latitude, 36.3745)
            self.assertEqual(customer.location_accuracy, 80 if language == 'en' else 12)
            self.assertEqual(customer.place_id, 'mock-place')
            self.assertEqual(customer.address, 'Adresse corrigée ' + language)

    def test_gps_errors_and_fallback_preserve_manual_address(self):
        with sync_playwright() as playwright, patch('apps.inventory.views.GeocodingService.reverse_geocode', side_effect=GeocodingUnavailable):
            browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
            try:
                for code, key in ((1, 'denied'), (2, 'timeout'), (3, 'timeout'), (0, 'unsupported')):
                    context = self.context(browser)
                    script = "Object.defineProperty(navigator, 'geolocation', {value: undefined});" if not code else f"Object.defineProperty(navigator, 'geolocation', {{value: {{watchPosition: (ok, fail) => {{fail({{code:{code}}}); return 1;}}, clearWatch: () => {{}}}}}});"
                    context.add_init_script(script)
                    page = context.new_page()
                    page.goto(self.live_server_url + '/inventory/clients/new/')
                    page.locator('#id_address').fill('Manuel')
                    page.locator('#detect-location').click()
                    message = page.locator('#client-location').get_attribute('data-' + key)
                    expect(page.locator('#location-status')).to_have_text(message, timeout=18000)
                    expect(page.locator('#id_address')).to_have_value('Manuel')
                    expect(page.locator('#id_latitude')).to_have_value('')
                    context.close()
                context = self.context(browser, width=1366)
                context.set_geolocation({'latitude': 36.3745, 'longitude': 3.9012, 'accuracy': 12})
                page = context.new_page()
                page.goto(self.live_server_url + '/inventory/clients/new/')
                page.locator('#id_name').fill('GPS fallback')
                page.locator('#id_address').fill('Manuel')
                page.locator('#detect-location').click()
                expect(page.locator('#location-status')).to_have_text(page.locator('#client-location').get_attribute('data-fallback'))
                expect(page.locator('#detect-location')).to_be_enabled()
                expect(page.locator('#id_latitude')).to_have_value('36.3745')
                expect(page.locator('#id_address')).to_have_value('Manuel')
                page.locator('#client-form .form-actions button').click()
                expect(page).to_have_url(self.live_server_url + '/inventory/clients/')
                context.close()
            finally:
                browser.close()
        customer = Client.objects.get(name='GPS fallback')
        self.assertEqual(customer.address, 'Manuel')
        self.assertEqual(customer.location_accuracy, 12)
