"""Synthetic reset URLs only. No mail delivery or production log access."""
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import patch

from django.conf import settings
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils.log import AdminEmailHandler
from django.utils.translation import override

from apps.accounts.views import UserPasswordResetConfirmView
from apps.core.log_redaction import (
    RESET_PATH_MARKER, RESET_VALUE_MARKER, ResetLinkLogFilter,
    ResetSafeAtomsMixin, redact_reset_urls,
)
from apps.core.models import AuditLog
from apps.core.security import log_security_event


TOKEN = 'synthetic-reset-token-20261115-never-a-real-secret'
UID = 'c3ludGhldGljLXVzZXI'
PATH = f'/reset/{UID}/{TOKEN}/'


class ResetPathRedactorTests(SimpleTestCase):
    def test_root_prefixed_translated_and_malformed_urls_are_masked(self):
        for path in (PATH, '/fr'+PATH, '/ar'+PATH, '/en'+PATH,
                     '/missing-prefix'+PATH+'extra/', PATH+'?token='+TOKEN+'#'+TOKEN,
                     PATH.rstrip('/'), '/RESET/'+UID+'/'+TOKEN+'/',
                     'https://example.invalid'+PATH, 'GET '+PATH+' HTTP/1.1',
                     quote(PATH, safe=''), quote(quote(PATH, safe=''), safe=''),
                     '/r%65set/'+UID+'/'+TOKEN+'/',
                     '/reset/'+UID+'/%27'+TOKEN+'/',
                     '/reset/'+UID+'/%2F'+TOKEN+'/',
                     'first '+PATH+' second '+PATH):
            with self.subTest(path=path):
                result = redact_reset_urls(path)
                self.assertNotIn(TOKEN, result)
                self.assertNotIn(UID, result)
                self.assertIn('redacted', result)

    def test_ordinary_routes_and_encoded_non_reset_urls_are_unchanged(self):
        for value in ('/inventory/products/42/', '/password-reset/', '/api/v1/auth/reset/',
                      '/commerce/sales/42/?page=2', '/normal%20path/',
                      'HTTP 403 on /core/company/', ''):
            self.assertEqual(redact_reset_urls(value), value)

    def test_decoding_never_introduces_log_lines_or_reveals_token_tail(self):
        for suffix in ('%0A', '%0D', '%09', '%00', '%20', '%C2%A0'):
            for value in ('/reset/'+UID+'/'+suffix+TOKEN+'/',
                          'normal'+suffix+'forged '+PATH):
                with self.subTest(value=value):
                    result = redact_reset_urls(value)
                    self.assertNotIn(TOKEN, result)
                    self.assertFalse(any(char in result for char in '\r\n\t\x00'))
                    self.assertEqual(result, RESET_VALUE_MARKER)

    def test_excessive_recursive_encoding_is_conservatively_omitted(self):
        path = PATH
        for _ in range(20):
            path = quote(path, safe='')
        self.assertNotIn(TOKEN, redact_reset_urls(path))
        self.assertIn('redacted', redact_reset_urls(path))

    def test_redaction_is_idempotent(self):
        first = redact_reset_urls(PATH)
        self.assertEqual(first, RESET_PATH_MARKER)
        self.assertEqual(redact_reset_urls(first), first)


class ResetLogFilterTests(SimpleTestCase):
    def request(self):
        request = RequestFactory().post(PATH+'?token='+TOKEN, {'token': TOKEN})
        request.user = SimpleNamespace(username='synthetic-user')
        request.resolver_match = SimpleNamespace(kwargs={'uidb64': UID, 'token': TOKEN})
        request.META['HTTP_AUTHORIZATION'] = 'Bearer '+TOKEN
        return request

    def record(self, request=None, message='Forbidden: %s', args=(PATH,), exc_info=None):
        record = logging.LogRecord('django.request', logging.ERROR, __file__, 1,
                                   message, args, exc_info)
        record.status_code = 500
        if request is not None:
            record.request = request
        return record

    def test_bare_token_extra_path_fields_and_live_request_are_safe(self):
        request = self.request()
        record = self.record(request, message='Failure token='+TOKEN, args=())
        record.raw_uri = PATH+'?token='+TOKEN
        record.path = PATH
        self.assertTrue(ResetLinkLogFilter().filter(record))
        self.assertNotIn(TOKEN, record.getMessage())
        self.assertNotIn(TOKEN, record.raw_uri)
        self.assertNotIn(TOKEN, record.path)
        self.assertIsNot(record.request, request)
        self.assertNotIn(TOKEN, repr(record.request))
        self.assertFalse(record.request.POST)
        self.assertFalse(record.request.GET)
        self.assertNotIn('HTTP_AUTHORIZATION', record.request.META)
        self.assertIsNone(record.request.resolver_match)
        self.assertEqual(request.path, PATH)
        self.assertEqual(request.POST['token'], TOKEN)
        self.assertEqual(request.resolver_match.kwargs['token'], TOKEN)
        self.assertIn(TOKEN, request.META['HTTP_AUTHORIZATION'])

    def test_sensitive_traceback_payload_is_omitted_but_error_type_retained(self):
        try:
            raise ValueError(TOKEN)
        except ValueError:
            record = self.record(self.request(), exc_info=sys.exc_info())
        record.exc_text = TOKEN
        record.stack_info = TOKEN
        ResetLinkLogFilter().filter(record)
        output = logging.Formatter('%(levelname)s %(message)s').format(record)
        self.assertNotIn(TOKEN, output)
        self.assertIn('ValueError', output)
        self.assertIn('ERROR', output)
        self.assertEqual(record.status_code, 500)
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)

    def test_cached_traceback_and_stack_urls_are_masked_without_request(self):
        for field in ('exc_text', 'stack_info'):
            record = self.record(message='Server error', args=())
            setattr(record, field, 'cached detail '+PATH)
            ResetLinkLogFilter().filter(record)
            self.assertNotIn(TOKEN, logging.Formatter().format(record))
            self.assertIsNone(getattr(record, field))

    def test_normal_exception_and_status_are_not_hidden(self):
        try:
            raise ValueError('ordinary-failure')
        except ValueError:
            record = self.record(message='ordinary route', args=(), exc_info=sys.exc_info())
        original = record.exc_info
        ResetLinkLogFilter().filter(record)
        self.assertIs(record.exc_info, original)
        self.assertIn('ordinary-failure', logging.Formatter().format(record))

    @override_settings(DEBUG=False, ADMINS=[('Synthetic', 'admin@example.invalid')])
    def test_default_admin_email_report_does_not_reintroduce_request_secrets(self):
        request = self.request()
        try:
            raise ValueError(TOKEN)
        except ValueError:
            record = self.record(request, exc_info=sys.exc_info())
        handler = AdminEmailHandler(include_html=True)
        handler.addFilter(ResetLinkLogFilter())
        with patch.object(handler, 'send_mail') as send:
            handler.handle(record)
        send.assert_called_once()
        self.assertNotIn(TOKEN, str(send.call_args))
        self.assertNotIn(UID, str(send.call_args))
        self.assertEqual(request.path, PATH)

    def test_filter_is_idempotent_for_shared_handlers(self):
        record = self.record(self.request())
        instance = ResetLinkLogFilter()
        instance.filter(record)
        message = record.getMessage()
        instance.filter(record)
        self.assertEqual(record.getMessage(), message)


class ResetAuditIntegrationTests(TestCase):
    def assert_clean(self, captured):
        self.assertNotIn(TOKEN, '\n'.join(captured.output))
        for row in AuditLog.objects.all():
            self.assertNotIn(TOKEN, row.path)
            self.assertNotIn(TOKEN, row.action)

    def test_csrf_rejection_on_real_reset_route_is_redacted_in_all_languages(self):
        for language in ('fr', 'ar', 'en'):
            with self.subTest(language=language), override(language), \
                    self.assertLogs('security', level='WARNING') as app_logs, \
                    self.assertLogs('django.security.csrf', level='WARNING') as django_logs:
                response = Client(enforce_csrf_checks=True).post(PATH)
            self.assertEqual(response.status_code, 403)
            self.assert_clean(app_logs)
            self.assertNotIn(TOKEN, '\n'.join(django_logs.output))
        self.assertTrue(AuditLog.objects.filter(path=RESET_PATH_MARKER, status_code=403).exists())

    def test_404_url_keeps_status_and_masks_malformed_reset_suffix(self):
        path = '/missing-prefix'+PATH+'extra/'
        with self.assertLogs('security', level='INFO') as app_logs, \
                self.assertLogs('django.request', level='WARNING') as django_logs:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 404)
        self.assert_clean(app_logs)
        self.assertNotIn(TOKEN, '\n'.join(django_logs.output))
        self.assertTrue(AuditLog.objects.filter(status_code=404).exists())

    def test_500_on_real_route_omits_exception_token_without_changing_event(self):
        client = Client(raise_request_exception=False)
        with patch.object(UserPasswordResetConfirmView, 'dispatch', side_effect=RuntimeError(TOKEN)), \
                self.assertLogs('security', level='ERROR') as app_logs, \
                self.assertLogs('django.request', level='ERROR') as django_logs:
            response = client.get(PATH)
        self.assertEqual(response.status_code, 500)
        self.assert_clean(app_logs)
        self.assertNotIn(TOKEN, '\n'.join(django_logs.output))
        self.assertIn('RuntimeError', '\n'.join(django_logs.output))
        self.assertTrue(AuditLog.objects.filter(path=RESET_PATH_MARKER, status_code=500).exists())

    def test_redaction_precedes_database_length_limit_and_preserves_actor(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user('synthetic-log-actor')
        request = RequestFactory().get('/prefix/'+'x'*240+PATH)
        request.user = user
        with self.assertLogs('security', level='WARNING') as logs:
            log_security_event(request, 'reset denied '+PATH, status_code=403, level='warning')
        self.assert_clean(logs)
        row = AuditLog.objects.latest('pk')
        self.assertLessEqual(len(row.path), 255)
        self.assertEqual(row.user_id, user.pk)
        self.assertEqual(row.status_code, 403)
        self.assertEqual(row.level, 'warning')

    def test_bare_path_token_in_action_is_not_persisted_or_logged(self):
        request = RequestFactory().get(PATH)
        request.user = None
        with self.assertLogs('security', level='WARNING') as logs:
            log_security_event(request, 'Bad token '+TOKEN, status_code=403, level='warning')
        self.assert_clean(logs)
        row = AuditLog.objects.latest('pk')
        self.assertEqual(row.path, RESET_PATH_MARKER)
        self.assertEqual(row.action, 'Bad token [redacted]')

    def test_non_sensitive_audit_paths_and_actions_are_preserved(self):
        request = RequestFactory().get('/inventory/products/42/')
        request.user = None
        log_security_event(request, 'product.view', status_code=200)
        row = AuditLog.objects.latest('pk')
        self.assertEqual(row.path, request.path)
        self.assertEqual(row.action, 'product.view')


class ResetAccessAtomsTests(SimpleTestCase):
    def logger(self):
        class Base:
            def atoms(self, response, request, environ, duration):
                return {'U': environ['PATH_INFO'], 'r': 'GET '+environ['RAW_URI']+' HTTP/1.1',
                        'q': environ['QUERY_STRING'], '{raw_uri}e': environ['RAW_URI'],
                        '{path_info}e': environ['PATH_INFO'], '{query_string}e': environ['QUERY_STRING'],
                        '{referer}i': environ['HTTP_REFERER'], 's': '403'}
        class Logger(ResetSafeAtomsMixin, Base):
            pass
        return Logger()

    def test_all_reset_atoms_are_redacted_even_with_verbose_local_format(self):
        for path in (PATH, quote(PATH, safe=''), '/r%65set/'+UID+'/'+TOKEN+'/',
                     '/reset/'+UID+'/%0A'+TOKEN+'/', '/reset/'+UID+'/%27'+TOKEN+'/'):
            environ = {'PATH_INFO': path, 'RAW_URI': path+'?token='+TOKEN,
                       'QUERY_STRING': 'token='+TOKEN, 'HTTP_REFERER': 'https://example.invalid'+PATH}
            original = dict(environ)
            atoms = self.logger().atoms(None, None, environ, None)
            self.assertNotIn(TOKEN, str(atoms))
            self.assertEqual(atoms['q'], '')
            self.assertEqual(atoms['{query_string}e'], '')
            self.assertEqual(atoms['s'], '403')
            self.assertEqual(environ, original)

    def test_non_reset_access_atoms_are_unchanged(self):
        environ = {'PATH_INFO': '/inventory/products/', 'RAW_URI': '/inventory/products/?page=2',
                   'QUERY_STRING': 'page=2', 'HTTP_REFERER': 'https://example.invalid/'}
        atoms = self.logger().atoms(None, None, environ, None)
        self.assertEqual(atoms['U'], environ['PATH_INFO'])
        self.assertEqual(atoms['q'], 'page=2')
        self.assertEqual(atoms['{raw_uri}e'], environ['RAW_URI'])

    def test_query_and_uid_cannot_redact_structural_status(self):
        for path in (PATH+'?next=403', '/reset/403/'+TOKEN+'/'):
            environ = {'PATH_INFO': path, 'RAW_URI': path,
                       'QUERY_STRING': 'next=403', 'HTTP_REFERER': ''}
            atoms = self.logger().atoms(None, None, environ, None)
            self.assertEqual(atoms['s'], '403')
            self.assertNotIn(TOKEN, str(atoms))

    def test_malformed_control_context_masks_bare_header_without_clean_url_copy(self):
        path = '/reset/'+UID+'/%0A'+TOKEN+'/'
        environ = {'PATH_INFO': path, 'RAW_URI': path,
                   'QUERY_STRING': 'leak='+TOKEN, 'HTTP_REFERER': TOKEN}
        original = dict(environ)
        atoms = self.logger().atoms(None, None, environ, None)
        self.assertNotIn(TOKEN, str(atoms))
        self.assertEqual(atoms['s'], '403')
        self.assertEqual(environ, original)

    def test_both_startup_modes_select_logger_without_enabling_access_logs(self):
        from scripts.run_colocated_reset import web_command
        start = (Path(settings.BASE_DIR)/'start.sh').read_text(encoding='utf-8')
        self.assertIn('--logger-class apps.core.access_logging.ResetSafeLogger', start)
        self.assertNotIn('--access-logfile', start)
        command = web_command({})
        self.assertEqual(command[command.index('--logger-class')+1], 'apps.core.access_logging.ResetSafeLogger')
        self.assertNotIn('--access-logfile', command)
        self.assertEqual(command[command.index('--access-logformat')+1], '%(m)s %(U)s %(s)s')

    def test_docker_direct_gunicorn_entrypoint_also_selects_safe_logger(self):
        import json
        dockerfile = (Path(settings.BASE_DIR)/'Dockerfile').read_text(encoding='utf-8')
        command = json.loads(next(line[4:] for line in dockerfile.splitlines() if line.startswith('CMD ')))
        self.assertEqual(command[:4], ['gunicorn', 'gestio_stock.wsgi:application', '--bind', '0.0.0.0:8000'])
        self.assertEqual(command[command.index('--logger-class')+1], 'apps.core.access_logging.ResetSafeLogger')
        self.assertNotIn('--access-logfile', command)
