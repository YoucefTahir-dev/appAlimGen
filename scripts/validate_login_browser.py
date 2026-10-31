r"""Isolated browser tests for login.js; no ERP server or user profile is used.

Run: .\.venv\Scripts\python.exe scripts\validate_login_browser.py
Requires the optional playwright package and a locally installed Microsoft Edge.
"""

import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright


SCRIPT = Path(__file__).resolve().parents[1] / 'static' / 'js' / 'login.js'
ORIGIN = 'https://login-regression.invalid'
PAGE = """<!doctype html><html><body>
<form id="login-form" action="/" method="post">
<input name="username" required><input name="password" type="password" required>
<button type="submit">Connexion</button></form>
<script src="/login.js" defer></script></body></html>"""


class LoginBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.posts = []
        self.gets = 0
        self.context.route(f'{ORIGIN}/**', self.serve)
        self.page.goto(f'{ORIGIN}/')

    def tearDown(self):
        self.context.close()

    def serve(self, route):
        request = route.request
        if request.url.endswith('/login.js'):
            route.fulfill(path=str(SCRIPT), content_type='application/javascript')
        elif request.method == 'POST':
            self.posts.append(request.method)
            route.fulfill(body='<h1>Connected</h1>', content_type='text/html')
        else:
            self.gets += 1
            route.fulfill(body=PAGE, content_type='text/html')

    def fill_form(self):
        self.page.locator('[name="username"]').fill('fictitious-user')
        self.page.locator('[name="password"]').fill('fictitious-password')

    def test_double_submission_sends_one_post_and_locks_button(self):
        self.fill_form()
        self.page.evaluate("""() => {
            const form = document.getElementById('login-form');
            form.requestSubmit();
            form.requestSubmit();
            if (!form.querySelector('button').disabled || form.getAttribute('aria-busy') !== 'true') {
                throw new Error('Submit guard not activated');
            }
        }""")
        self.page.wait_for_selector('h1')
        self.assertEqual(len(self.posts), 1)

    def test_invalid_form_does_not_lock_the_button_and_can_be_corrected(self):
        self.page.locator('button').click()
        self.assertTrue(self.page.locator('button').is_enabled())
        self.assertEqual(len(self.posts), 0)
        self.fill_form()
        self.page.locator('button').click()
        self.page.wait_for_selector('h1')
        self.assertEqual(len(self.posts), 1)

    def test_repeated_enter_sends_one_post(self):
        self.fill_form()
        self.page.locator('[name="password"]').press('Enter')
        self.page.wait_for_selector('h1')
        self.page.keyboard.press('Enter')
        self.assertEqual(len(self.posts), 1)

    def test_restored_page_requests_a_fresh_get_without_resubmission(self):
        initial_gets = self.gets
        with self.page.expect_navigation(wait_until='load'):
            self.page.evaluate("""() => window.dispatchEvent(
                new PageTransitionEvent('pageshow', {persisted: true})
            )""")
        self.page.wait_for_selector('#login-form')
        self.assertEqual(self.gets, initial_gets + 1)
        self.assertEqual(len(self.posts), 0)
        self.assertTrue(self.page.locator('button').is_enabled())


if __name__ == '__main__':
    unittest.main(verbosity=2)
