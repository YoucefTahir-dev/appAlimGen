import io
import json
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase, RequestFactory
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.admin import AuditLogAdmin


class AuditJournalAccessTests(TestCase):
    def test_only_active_superuser_can_read_and_no_admin_crud_is_exposed(self):
        staff = get_user_model().objects.create_user(username='audit-staff', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_auditlog', content_type__app_label='core'))
        owner = get_user_model().objects.create_superuser(username='audit-owner')
        admin = AuditLogAdmin(AuditLog, AdminSite())
        request = RequestFactory().get('/admin/core/auditlog/')
        for user, can_view in ((staff, False), (owner, True)):
            request.user = user
            self.assertEqual(admin.has_view_permission(request), can_view)
            self.assertEqual(admin.has_module_permission(request), can_view)
            self.assertFalse(admin.has_add_permission(request))
            self.assertFalse(admin.has_change_permission(request))
            self.assertFalse(admin.has_delete_permission(request))


class LocationRetentionTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='retention-synthetic')
        self.raw = 'client.location.updated id=42 old=(36.123456, 3.123456) new=(36.654321, 3.654321)'
        self.old = AuditLog.objects.create(user=self.actor, action=self.raw, path='/synthetic/', status_code=200)
        AuditLog.objects.filter(pk=self.old.pk).update(created_at=timezone.now() - timedelta(days=91))
        self.recent = AuditLog.objects.create(user=self.actor, action=self.raw)
        self.unrelated = AuditLog.objects.create(action='sale.updated id=42')

    def run_command(self, **kwargs):
        output = io.StringIO()
        call_command('anonymize_location_logs', stdout=output, **kwargs)
        self.assertNotIn('36.123456', output.getvalue())
        return json.loads(output.getvalue())

    def test_dry_run_does_not_write_and_never_prints_coordinates(self):
        result = self.run_command()
        self.assertEqual((result['eligible'], result['updated']), (1, 0))
        self.old.refresh_from_db()
        self.assertEqual(self.old.action, self.raw)

    def test_apply_requires_exact_confirmation(self):
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.old.refresh_from_db()
        self.assertEqual(self.old.action, self.raw)

    def test_apply_preserves_actor_date_path_record_and_recent_logs(self):
        before = AuditLog.objects.get(pk=self.old.pk)
        result = self.run_command(apply=True, confirm='ANONYMISER GPS 90 JOURS')
        self.assertEqual(result['updated'], 1)
        self.old.refresh_from_db()
        self.assertEqual(self.old.action, 'client.location.updated id=42 change=legacy_redacted')
        for key in ('pk', 'user_id', 'created_at', 'path', 'status_code', 'ip_address', 'level'):
            self.assertEqual(getattr(self.old, key), getattr(before, key))
        self.recent.refresh_from_db()
        self.assertEqual(self.recent.action, self.raw)
        self.assertEqual(AuditLog.objects.count(), 3)
        self.assertEqual(self.run_command()['eligible'], 0)

    def test_bounded_batch_and_truncated_legacy_payload(self):
        for i in range(2):
            row = AuditLog.objects.create(action=f'client.location.updated id={100+i} old=(None, None) new=(36.')
            AuditLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=92))
        result = self.run_command(apply=True, confirm='ANONYMISER GPS 90 JOURS', limit=1)
        self.assertTrue(result['limit_reached'])
        self.assertEqual(result['updated'], 1)
        self.assertEqual(self.run_command()['eligible'], 2)


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL metadata required.')
class DatabasePostureTests(TransactionTestCase):
    def test_readonly_tls_role_report_has_no_names_and_no_business_reads(self):
        queries = []
        def record(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)
        output = io.StringIO()
        with connection.execute_wrapper(record):
            call_command('check_database_posture', stdout=output)
        report = json.loads(output.getvalue())
        self.assertTrue(report['session_tls'])
        self.assertTrue(report['review_required'])  # disposable cluster owner
        self.assertFalse(report['business_data_read'])
        self.assertFalse(report['privileges_modified'])
        for sql in queries:
            self.assertNotIn('inventory_client', sql)
            self.assertNotIn('commerce_sale', sql)
        self.assertTrue(any('SET TRANSACTION READ ONLY' in sql for sql in queries))
        self.assertNotIn(connection.settings_dict['USER'], output.getvalue())
        with connection.cursor() as cursor:
            cursor.execute('SHOW transaction_read_only')
            self.assertEqual(cursor.fetchone()[0], 'off')
