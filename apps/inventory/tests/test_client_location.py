from unittest.mock import patch, MagicMock
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, Client as HttpClient, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import AuditLog
from apps.inventory.forms import ClientForm
from apps.inventory.models import Client
from apps.inventory.geocoding import GeocodingService, GeocodingUnavailable

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ClientLocationMigrationTests(TransactionTestCase):
    def test_existing_client_survives_additive_migration(self):
        previous = [('inventory', '0013_customer_type_pricing')]
        current = [('inventory', '0014_client_location')]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        try:
            old_client = executor.loader.project_state(previous).apps.get_model('inventory', 'Client')
            customer = old_client.objects.create(
                name='Client avant GPS', address='Adresse historique', phone='0123456789',
                balance='125.50', customer_type='WHOLESALE',
            )
        finally:
            MigrationExecutor(connection).migrate(current)
        migrated = Client.objects.get(pk=customer.pk)
        self.assertEqual(migrated.address, 'Adresse historique')
        self.assertEqual(migrated.phone, '0123456789')
        self.assertEqual(str(migrated.balance), '125.50')
        self.assertEqual(migrated.customer_type, 'WHOLESALE')
        for field in ('latitude', 'longitude', 'location_accuracy', 'formatted_address', 'place_id'):
            self.assertIsNone(getattr(migrated, field))


class ClientLocationTests(TestCase):
    def test_web_location_fields_are_hidden_without_confirmation_section(self):
        for field in ('latitude', 'longitude', 'location_accuracy', 'formatted_address', 'place_id'):
            self.assertTrue(ClientForm().fields[field].widget.is_hidden)
        existing = Client.objects.create(name='Existing location', latitude=36, longitude=3)
        for url in (reverse('client_create'), reverse('client_update', args=[existing.pk])):
            response = self.client.get(url)
            self.assertContains(response, 'id="detect-location"')
            self.assertContains(response, 'class="input-group client-address-group"')
            self.assertNotContains(response, 'id="location-proposal"')
            self.assertNotContains(response, 'id="confirm-location"')
            self.assertNotContains(response, 'for="id_latitude"')
            self.assertContains(response, 'type="hidden" name="latitude"')

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='geo', role='manager')
        self.client.force_login(self.user)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.data = {'name': 'Client terrain', 'address': 'Adresse manuelle', 'balance': '0', 'customer_type': 'RETAIL'}
        self.gps = {'latitude': 36.3745, 'longitude': 3.9012, 'location_accuracy': 8.5}

    def test_manual_client_and_old_form_remain_valid(self):
        form = ClientForm(self.data)
        self.assertTrue(form.is_valid(), form.errors)
        customer = form.save()
        self.assertIsNone(customer.latitude)
        self.assertEqual(customer.maps_url, '')
        self.assertEqual(self.api.post('/api/v1/clients/', {'name': 'Old Android'}, format='json').status_code, 201)

    def test_web_creation_and_update_audit(self):
        response = self.client.post(reverse('client_create'), {**self.data, **self.gps, 'formatted_address': 'Adresse Google', 'place_id': 'abc'})
        self.assertEqual(response.status_code, 302)
        customer = Client.objects.get(name=self.data['name'])
        self.assertEqual(customer.address, 'Adresse manuelle')
        self.assertEqual(customer.location_accuracy, 8.5)
        self.assertIn('36.3745,3.9012', customer.maps_url)
        response = self.client.post(reverse('client_update', args=[customer.pk]), {**self.data, **self.gps, 'latitude': 37})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AuditLog.objects.filter(action__startswith='client.location.updated').count(), 2)
        self.assertContains(self.client.get(reverse('client_detail', args=[customer.pk])), 'query=37.0,3.9012')

    def test_invalid_locations_rejected_by_form_model_and_api(self):
        cases = ({'latitude': 91}, {'longitude': -181}, {'location_accuracy': -1}, {'latitude': float('inf')},
                 {'longitude': float('nan')}, {'latitude': None}, {'latitude': None, 'longitude': None})
        for invalid in cases:
            with self.subTest(invalid=invalid):
                data = {**self.gps, **invalid}
                self.assertFalse(ClientForm({**self.data, **{k: '' if v is None else v for k, v in data.items()}}).is_valid())
                with self.assertRaises(ValidationError):
                    Client(name='Invalid', **data).full_clean()
                # Multipart represents non-finite values without invalid JSON encoding.
                response = self.api.post('/api/v1/clients/', {'name': 'Invalid', **{k: '' if v is None else v for k, v in data.items()}})
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Client.objects.count(), 0)

    def test_api_optional_fields_patch_and_cleared_metadata(self):
        response = self.api.post('/api/v1/clients/', {'name': 'Android', **self.gps, 'formatted_address': 'Google', 'place_id': 'id'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        customer = Client.objects.get(name='Android')
        url = f'/api/v1/clients/{customer.pk}/'
        self.assertEqual(self.api.patch(url, {'address': 'Correction manuelle'}, format='json').status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(customer.latitude, self.gps['latitude'])
        self.assertEqual(customer.formatted_address, 'Google')
        self.assertEqual(self.api.patch(url, {'latitude': 0, 'longitude': 0}, format='json').status_code, 200)
        customer.refresh_from_db()
        self.assertIsNone(customer.place_id)
        self.assertIsNone(customer.location_accuracy)
        self.assertIn('query=0.0,0.0', customer.maps_url)
        self.assertEqual(self.api.patch(url, {'latitude': None, 'longitude': None}, format='json').status_code, 200)
        self.assertEqual(self.api.patch(url, {'place_id': 'x' * 256}, format='json').status_code, 400)

    def test_reverse_geocoding_no_write_and_csrf(self):
        url = reverse('client_reverse_geocode')
        with patch('apps.inventory.views.GeocodingService.reverse_geocode', return_value={'formatted_address': 'Bouira', 'place_id': 'abc'}) as provider:
            response = self.client.post(url, self.gps)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['formatted_address'], 'Bouira')
            self.assertEqual(Client.objects.count(), 0)
            self.assertEqual(self.client.post(url, {'latitude': 500, 'longitude': 1}).status_code, 400)
            self.assertEqual(provider.call_count, 1)
        csrf = HttpClient(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(url, self.gps).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch('apps.inventory.views.GeocodingService.reverse_geocode', side_effect=GeocodingUnavailable):
            self.assertEqual(self.client.post(url, self.gps).status_code, 503)

    def test_rbac_cannot_update_through_alternative_endpoint(self):
        group = Group.objects.create(name='Client creator')
        self.user.groups.add(group)
        url = reverse('client_reverse_geocode')
        self.assertEqual(self.client.post(url, self.gps).status_code, 403)
        group.permissions.add(Permission.objects.get(codename='add_client', content_type__app_label='inventory'))
        with patch('apps.inventory.views.GeocodingService.reverse_geocode', return_value={}):
            self.assertEqual(self.client.post(url, self.gps).status_code, 200)
        customer = Client.objects.create(name='Existing')
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.api.force_authenticate(self.user)
        self.assertEqual(self.api.patch(f'/api/v1/clients/{customer.pk}/', self.gps, format='json').status_code, 403)
        self.assertEqual(self.client.post(reverse('client_update', args=[customer.pk]), {**self.data, **self.gps}).status_code, 403)
        customer.refresh_from_db()
        self.assertIsNone(customer.latitude)

    def test_geocoding_rate_limit(self):
        with patch('apps.inventory.views.GeocodingService.reverse_geocode', return_value={}) as provider, patch('apps.inventory.views.time.time', return_value=120):
            for _ in range(30):
                self.assertEqual(self.client.post(reverse('client_reverse_geocode'), self.gps).status_code, 200)
            self.assertEqual(self.client.post(reverse('client_reverse_geocode'), self.gps).status_code, 429)
            self.assertEqual(provider.call_count, 30)

    @override_settings(GOOGLE_MAPS_API_KEY='')
    def test_missing_key_does_not_call_google(self):
        with patch('apps.inventory.geocoding.urlopen') as request:
            with self.assertRaises(GeocodingUnavailable):
                GeocodingService().reverse_geocode(36, 3)
            request.assert_not_called()

    @override_settings(GOOGLE_MAPS_API_KEY='test-key-only')
    def test_google_success_and_failures_are_mocked(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"OK","results":[{"formatted_address":"Bouira","place_id":"abc"}]}'
        with patch('apps.inventory.geocoding.urlopen', return_value=response) as request:
            self.assertEqual(GeocodingService().reverse_geocode(36, 3)['place_id'], 'abc')
            self.assertEqual(request.call_args.kwargs['timeout'], 5)
        for failure in (URLError('provider failure'), TimeoutError()):
            with patch('apps.inventory.geocoding.urlopen', side_effect=failure):
                with self.assertRaises(GeocodingUnavailable):
                    GeocodingService().reverse_geocode(36, 3)
        response.__enter__.return_value.read.return_value = b'{"status":"REQUEST_DENIED"}'
        with patch('apps.inventory.geocoding.urlopen', return_value=response):
            with self.assertRaises(GeocodingUnavailable):
                GeocodingService().reverse_geocode(36, 3)
