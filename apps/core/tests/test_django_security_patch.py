"""Bounded CY-01 regressions for the supported Django 5.2 security patch.

These exercise framework behavior with synthetic data, not source-text matches
or a load test. No production configuration, database, file or transport is used.
The chunk assertions cover delivery to an upload handler, not whole-process RSS.
"""
import base64
from io import BytesIO

import django
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import FileUploadHandler
from django.http import HttpResponse
from django.http.multipartparser import (
    MAX_TOTAL_HEADER_SIZE, MultiPartParser, MultiPartParserError,
)
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils.cache import patch_vary_headers
from django.utils.http import parse_header_parameters
from django.utils.translation import trans_real
from django.views.decorators.cache import cache_page


class SupportedDjangoPatchTests(SimpleTestCase):
    def test_supported_52_patch_is_installed(self):
        # A compatibility guard, not a standalone assertion of application safety.
        self.assertEqual(django.VERSION[:2], (5, 2))
        self.assertGreaterEqual(django.VERSION[:3], (5, 2, 19))


@override_settings(
    LANGUAGES=(('fr', 'French'), ('ar', 'Arabic'), ('en', 'English')),
    LANGUAGE_CODE='fr',
    USE_I18N=True,
)
class LanguageInputBoundTests(SimpleTestCase):
    def _lookup_cache(self):
        # The fixed branch separates bounded public input from its cached lookup.
        # The fallback also makes the behavior probe meaningful on older 5.2.
        return getattr(
            trans_real, '_get_supported_language_variant',
            trans_real.get_supported_language_variant,
        )

    def setUp(self):
        super().setUp()
        self._lookup_cache().cache_clear()
        self.addCleanup(lambda: self._lookup_cache().cache_clear())

    def test_supported_languages_and_generic_variants_remain_available(self):
        for code in ('fr', 'ar', 'en'):
            with self.subTest(code=code):
                self.assertEqual(trans_real.get_supported_language_variant(code), code)
                self.assertEqual(
                    trans_real.get_supported_language_variant(code + '-xx'), code,
                )

    def test_distinct_overlong_variants_do_not_become_distinct_cache_keys(self):
        # Eight half-kilobyte values are sufficient; no unbounded corpus or timing
        # assertion is needed to check that attacker strings don't become keys.
        for suffix in range(8):
            code = 'fr-' + ('x' * 512) + str(suffix)
            self.assertEqual(trans_real.get_supported_language_variant(code), 'fr')
        self.assertEqual(self._lookup_cache().cache_info().currsize, 1)

    def test_overlong_values_without_a_bounded_variant_never_enter_cache(self):
        before = self._lookup_cache().cache_info()
        for suffix in range(4):
            with self.subTest(suffix=suffix), self.assertRaises(LookupError):
                trans_real.get_supported_language_variant(('x' * 501) + str(suffix))
        self.assertEqual(self._lookup_cache().cache_info(), before)

    def test_strict_overlong_variant_is_rejected_before_cached_lookup(self):
        before = self._lookup_cache().cache_info()
        with self.assertRaises(LookupError):
            trans_real.get_supported_language_variant('fr-' + ('x' * 512), strict=True)
        self.assertEqual(self._lookup_cache().cache_info(), before)


class HeaderParameterCompatibilityTests(SimpleTestCase):
    def test_quoted_semicolons_and_later_parameters_keep_their_boundaries(self):
        value = 'audit' + (';' * 64) + 'fixture.txt'
        media_type, parameters = parse_header_parameters(
            'Application/Octet-Stream; name="' + value + '"; filename="invoice.txt"',
        )
        self.assertEqual(media_type, 'application/octet-stream')
        self.assertEqual(parameters, {'name': value, 'filename': 'invoice.txt'})

    def test_repeated_parameter_retains_the_existing_last_value_contract(self):
        media_type, parameters = parse_header_parameters(
            'text/plain; charset=utf-8; charset=ascii',
        )
        self.assertEqual(media_type, 'text/plain')
        self.assertEqual(parameters['charset'], 'ascii')

    def test_rfc2231_filename_with_missing_charset_is_decoded(self):
        media_type, parameters = parse_header_parameters(
            "attachment; filename*=''invoice%20audit.txt",
        )
        self.assertEqual(media_type, 'attachment')
        self.assertEqual(parameters['filename'], 'invoice audit.txt')

    def test_unknown_rfc2231_charset_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_header_parameters(
                "attachment; filename*=cy01-unknown-charset''audit.txt",
            )

    def test_empty_and_parameterless_headers_have_predictable_results(self):
        self.assertEqual(parse_header_parameters(''), ('', {}))
        self.assertEqual(parse_header_parameters(' Text/Plain '), ('text/plain', {}))


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'cy01-vary-header-local-regression',
        },
    },
    ALLOWED_HOSTS=('testserver',),
    USE_I18N=False,
)
class VaryHeaderCacheIsolationTests(SimpleTestCase):
    def test_absent_and_present_empty_vary_headers_cannot_share_a_response(self):
        cache = caches['default']
        cache.clear()
        self.addCleanup(cache.clear)
        rendered = []

        @cache_page(30, key_prefix='cy01-vary')
        def view(request):
            marker = 'present-empty' if 'HTTP_X_AUDIT_VARIANT' in request.META else 'absent'
            rendered.append(marker)
            response = HttpResponse(marker)
            patch_vary_headers(response, ('X-Audit-Variant',))
            return response

        factory = RequestFactory()
        absent = factory.get('/synthetic-cache-variant/')
        empty = factory.get('/synthetic-cache-variant/', HTTP_X_AUDIT_VARIANT='')
        self.assertEqual(view(absent).content, b'absent')
        self.assertEqual(view(empty).content, b'present-empty')
        # Both variants must remain separate on actual cache hits as well.
        self.assertEqual(view(factory.get('/synthetic-cache-variant/')).content, b'absent')
        self.assertEqual(
            view(factory.get('/synthetic-cache-variant/', HTTP_X_AUDIT_VARIANT='')).content,
            b'present-empty',
        )
        self.assertEqual(rendered, ['absent', 'present-empty'])


class RecordingUploadHandler(FileUploadHandler):
    # Deliberately not divisible by four, to exercise base64 carry between chunks.
    chunk_size = 1021

    def __init__(self):
        super().__init__()
        self.chunks = []
        self.starts = []
        self.completed_size = None

    def receive_data_chunk(self, raw_data, start):
        self.chunks.append(raw_data)
        self.starts.append(start)
        return None

    def file_complete(self, file_size):
        self.completed_size = file_size
        return SimpleUploadedFile(
            self.file_name, b''.join(self.chunks), content_type=self.content_type,
        )


@override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024)
class Base64MultipartChunkTests(SimpleTestCase):
    boundary = 'cy01-synthetic-boundary'

    def _parser(self, encoded, handler):
        prefix = (
            '--' + self.boundary + '\r\n'
            'Content-Disposition: form-data; name="upload"; filename="audit.bin"\r\n'
            'Content-Type: application/octet-stream\r\n'
            'Content-Transfer-Encoding: base64\r\n\r\n'
        ).encode('ascii')
        body = prefix + encoded + ('\r\n--' + self.boundary + '--\r\n').encode('ascii')
        return MultiPartParser(
            {
                'CONTENT_TYPE': 'multipart/form-data; boundary=' + self.boundary,
                'CONTENT_LENGTH': str(len(body)),
            },
            BytesIO(body), [handler], encoding='utf-8',
        )

    def test_small_base64_file_reaches_upload_handler_in_bounded_decoded_chunks(self):
        payload = bytes(range(256)) * 32  # Exactly 8 KiB of synthetic content.
        handler = RecordingUploadHandler()
        post, files = self._parser(base64.encodebytes(payload), handler).parse()
        self.addCleanup(files['upload'].close)
        self.assertFalse(post)
        self.assertEqual(files['upload'].read(), payload)
        self.assertEqual(handler.completed_size, len(payload))
        self.assertGreater(len(handler.chunks), 1)
        # parse_boundary_stream reads up to 1024 header bytes. LazyStream.unget
        # can coalesce its unused body prefix with one nominal input chunk;
        # decoded callbacks therefore need not be <= handler.chunk_size. Add
        # the base64 carry (at most 3 encoded bytes), then convert aligned
        # encoded quartets to decoded triples. Here the bound is 1536 bytes,
        # not the whole 8192-byte file that the old buffering path could emit.
        encoded_bound = MAX_TOTAL_HEADER_SIZE + handler.chunk_size + 3
        decoded_bound = (encoded_bound // 4) * 3
        self.assertLess(decoded_bound, len(payload) // 4)
        self.assertLessEqual(max(map(len, handler.chunks)), decoded_bound)
        offset = 0
        for start, chunk in zip(handler.starts, handler.chunks, strict=True):
            self.assertEqual(start, offset)
            offset += len(chunk)
        self.assertEqual(offset, len(payload))

    def test_truncated_base64_file_is_rejected_without_file_completion(self):
        handler = RecordingUploadHandler()
        encoded = base64.b64encode(b'synthetic-only')[:-1]
        with self.assertRaises(MultiPartParserError):
            self._parser(encoded, handler).parse()
        self.assertIsNone(handler.completed_size)
