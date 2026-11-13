from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.core.models import AuditLog
from apps.inventory.location import audit_location, location_snapshot
from apps.inventory.models import Client


class LocationLogPrivacyTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().post('/synthetic-location-update/')
        self.request.user = get_user_model().objects.create_user(username='synthetic-gps-actor')
        self.customer = Client.objects.create(name='Synthetic GPS client')

    def test_add_change_metadata_and_clear_keep_audit_without_location_values(self):
        steps = [
            (36.37451234, 3.90125678, 8.54321, 'Private precise address', 'private-place-id', 'set'),
            (37.12345678, 4.87654321, 7.12345, 'Other private address', 'other-private-id', 'changed'),
            (37.12345678, 4.87654321, 6.98765, 'Metadata-only address', 'metadata-private-id', 'changed'),
            (None, None, None, None, None, 'cleared'),
        ]
        before = None
        with self.assertLogs('security', level='INFO') as captured:
            for lat, lon, accuracy, address, place, change in steps:
                self.customer.latitude, self.customer.longitude = lat, lon
                self.customer.location_accuracy = accuracy
                self.customer.formatted_address, self.customer.place_id = address, place
                audit_location(self.request, self.customer, before)
                action = AuditLog.objects.latest('pk')
                self.assertEqual(action.action, f'client.location.updated id={self.customer.pk} change={change}')
                self.assertEqual(action.user_id, self.request.user.pk)
                before = location_snapshot(self.customer)
        persisted = '\n'.join(AuditLog.objects.values_list('action', flat=True))
        output = '\n'.join(captured.output)
        for step in steps:
            for value in step[:5]:
                if value is not None:
                    self.assertNotIn(str(value), persisted)
                    self.assertNotIn(str(value), output)
        self.assertEqual(AuditLog.objects.count(), 4)

    def test_unchanged_or_new_empty_location_does_not_emit_event(self):
        with patch('apps.core.security.log_security_event') as emit:
            audit_location(self.request, self.customer, None)
            audit_location(self.request, self.customer, location_snapshot(self.customer))
        emit.assert_not_called()
