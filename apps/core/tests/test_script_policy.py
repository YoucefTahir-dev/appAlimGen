from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse


PINNED_CDN = {
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css':
        'sha384-T3c6CoIi6uLrA9TneNEoa7RxnatzjcDSCmG1MXxSR1GAsXEV/Dwwykc2MPK8M2HN',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js':
        'sha384-C6RzsynM9kWDrMNeT87bh95OGNyZPhcTNXj1NW7RuBCsyN/o0jlpcV8Qyq46cDfL',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css':
        'sha384-tViUnnbYAV00FLIhhi3v/dWt3Jxw4gZQcNoSCxCIFNJVCx7/D55/wXsrNIRANwdD',
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js':
        'sha384-9nhczxUqK87bcKHh20fSQcTGD4qq5GhayNYSYWqwBkINBhOfQLg/P5HG5lF1urn4',
}


class ScriptTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []
        self.inline_executable = 0
        self.handlers = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.handlers.extend(key for key in attrs if key.startswith('on'))
        if tag == 'script' and not attrs.get('src') and attrs.get('type') != 'application/json':
            self.inline_executable += 1
        if tag in ('script', 'link'):
            url = attrs.get('src') if tag == 'script' else attrs.get('href')
            if url and urlsplit(url).netloc:
                self.resources.append((url, attrs))


class ScriptSourceContractTests(SimpleTestCase):
    def test_templates_have_no_executable_inline_scripts_or_event_attributes(self):
        for folder in ('templates', 'apps'):
            for path in (settings.BASE_DIR / folder).rglob('*.html'):
                with self.subTest(template=str(path.relative_to(settings.BASE_DIR))):
                    text = path.read_text(encoding='utf-8')
                    # Template interpolation can contain quotes; inspect script
                    # tags and event attributes, not application data attributes.
                    parser = ScriptTags()
                    parser.feed(text)
                    self.assertEqual(parser.inline_executable, 0)
                    self.assertFalse(re.search(r'\bon[a-z]+\s*=', text, re.I))
                    self.assertFalse(re.search(r'(?:href|src)\s*=\s*[\"\']javascript:', text, re.I))

    def test_all_direct_cdn_resources_have_verified_integrity_and_cors(self):
        seen = []
        for folder in ('templates', 'apps'):
            for path in (settings.BASE_DIR / folder).rglob('*.html'):
                parser = ScriptTags()
                parser.feed(path.read_text(encoding='utf-8'))
                for url, attrs in parser.resources:
                    with self.subTest(template=str(path), url=url):
                        self.assertIn(url, PINNED_CDN)
                        self.assertEqual(attrs.get('integrity'), PINNED_CDN[url])
                        self.assertEqual(attrs.get('crossorigin'), 'anonymous')
                        seen.append(url)
        self.assertEqual(len(seen), 5)
        self.assertEqual(set(seen), set(PINNED_CDN))

    def test_extracted_scripts_exist_and_have_no_template_expressions(self):
        for name in ('sidebar', 'dashboard', 'conditioning-preview', 'auto-print', 'printer-test', 'print-controls'):
            with self.subTest(script=name):
                script = (settings.BASE_DIR / 'static' / 'js' / f'{name}.js').read_text(encoding='utf-8')
                self.assertNotIn('{%', script)
                self.assertNotIn('{{', script)


class ScriptPolicyResponseTests(TestCase):
    def test_default_policy_denies_inline_scripts_and_limits_cdn_script_paths(self):
        response = self.client.get(reverse('login'))
        directives = dict(
            (parts[0], parts[1:])
            for directive in response['Content-Security-Policy'].split(';')
            if (parts := directive.split())
        )
        self.assertNotIn("'unsafe-inline'", directives['script-src'])
        self.assertNotIn("'unsafe-eval'", directives['script-src'])
        self.assertNotIn('https://cdn.jsdelivr.net', directives['script-src'])
        self.assertEqual(directives['script-src-attr'], ["'none'"])
        parser = ScriptTags()
        parser.feed(response.content.decode())
        self.assertEqual(parser.inline_executable, 0)
        self.assertFalse(parser.handlers)

    @override_settings(SECURITY_RESPONSE_HEADERS={'Content-Security-Policy': "default-src 'self'; script-src 'none'"})
    def test_explicit_operator_policy_is_not_silently_overwritten(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response['Content-Security-Policy'], settings.SECURITY_RESPONSE_HEADERS['Content-Security-Policy'])
