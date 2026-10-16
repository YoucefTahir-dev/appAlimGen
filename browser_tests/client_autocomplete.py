"""Run explicitly: manage.py test browser_tests.client_autocomplete."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from apps.commerce.models import Sale
from apps.inventory.models import Client


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
class ClientAutocompleteBrowserTests(StaticLiveServerTestCase):
    def test_search_duplicate_selection_and_edit_restore(self):
        user = get_user_model().objects.create_user(
            username='client-browser', password='StrongPass123!', role='manager',
        )
        first = Client.objects.create(name='Client identique', phone='0600 00 00 01')
        selected = Client.objects.create(name='Client identique', phone='0600 00 00 02')
        hidden = Client.objects.create(name='Catalogue confidentiel')
        sale = Sale.objects.create(client=selected, total='0.00', created_by=user)
        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value

        errors = []
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
                page.on('pageerror', lambda error: errors.append(str(error)))
                searches = []
                page.on('request', lambda request: searches.append(request.url)
                        if '/clients/search/' in request.url else None)

                page.goto(f'{self.live_server_url}/commerce/sales/new/')
                expect(page.locator('#id_client')).to_have_value('')
                expect(page.locator('#id_client_search')).to_have_value('')
                self.assertNotIn(first.name, page.content())
                self.assertNotIn(hidden.name, page.content())
                page.fill('#id_client_search', 'C')
                page.wait_for_timeout(400)
                self.assertEqual(searches, [])
                page.fill('#id_client_search', 'identique')
                results = page.locator('#id_client_results .client-result')
                expect(results).to_have_count(2)
                self.assertEqual(len(searches), 1)
                expect(results.nth(0)).to_contain_text(first.phone)
                expect(results.nth(1)).to_contain_text(selected.phone)
                results.nth(1).click()
                expect(page.locator('#id_client')).to_have_value(str(selected.pk))
                expect(page.locator('#id_client_search')).to_have_value(
                    f'{selected.name} — {selected.phone}',
                )

                page.goto(f'{self.live_server_url}/commerce/sales/{sale.pk}/edit/')
                expect(page.locator('#id_client')).to_have_value(str(selected.pk))
                expect(page.locator('#id_client_search')).to_have_value(
                    f'{selected.name} — {selected.phone}',
                )
                self.assertNotIn(hidden.name, page.content())
            finally:
                browser.close()

        self.assertEqual(errors, [])
