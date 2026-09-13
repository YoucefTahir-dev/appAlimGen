import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.inventory.geocoding import GeocodingService, GeocodingUnavailable


@override_settings(GOOGLE_MAPS_API_KEY='private-test-key')
class GeocodingDiagnosticsTests(SimpleTestCase):
    def test_nearby_street_preferred_to_city_without_extra_request(self):
        results = [
            {'formatted_address': 'City', 'types': ['locality']},
            {'formatted_address': 'Far street', 'types': ['street_address'],
             'geometry': {'location': {'lat': 40, 'lng': 3}}},
            {'formatted_address': 'Nearby street', 'types': ['street_address'],
             'geometry': {'location': {'lat': 36.00001, 'lng': 3}, 'location_type': 'ROOFTOP'}},
        ]
        with patch('apps.inventory.geocoding.urlopen', return_value=self.response({'status': 'OK', 'results': results})) as request:
            self.assertEqual(GeocodingService().reverse_geocode(36, 3)['formatted_address'], 'Nearby street')
            request.assert_called_once()

    def test_unusable_geometry_keeps_provider_order(self):
        results = [{'formatted_address': 'Provider first'},
                   {'formatted_address': 'Bad geometry', 'types': ['street_address'], 'geometry': {'location': {'lat': 'secret', 'lng': 3}}}]
        with patch('apps.inventory.geocoding.urlopen', return_value=self.response({'status': 'OK', 'results': results})):
            self.assertEqual(GeocodingService().reverse_geocode(36, 3)['formatted_address'], 'Provider first')

    def response(self, payload, status=200):
        response = MagicMock()
        response.status = status
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode()
        return response

    @override_settings(GOOGLE_MAPS_API_KEY='   ')
    def test_missing_key_is_identified_without_network(self):
        with patch('apps.inventory.geocoding.urlopen') as request, self.assertLogs('apps.inventory.geocoding') as logs:
            with self.assertRaises(GeocodingUnavailable) as error:
                GeocodingService().reverse_geocode(36, 3)
        self.assertEqual(error.exception.code, 'missing_key')
        request.assert_not_called()
        self.assertIn('code=missing_key', logs.output[0])

    def test_success_sends_only_coordinates_key_and_language(self):
        response = self.response({'status': 'OK', 'results': [{'formatted_address': 'Rue actuelle, Bouira'}]})
        with patch('apps.inventory.geocoding.urlopen', return_value=response) as request:
            result = GeocodingService().reverse_geocode(36.3745, 3.9012)
        self.assertEqual(result, {'formatted_address': 'Rue actuelle, Bouira', 'place_id': ''})
        query = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertEqual(set(query), {'latlng', 'key', 'language'})
        self.assertEqual(query['latlng'], ['36.3745,3.9012'])
        self.assertEqual(request.call_args.kwargs, {'timeout': 5})

    def test_google_status_codes_preserved_without_sensitive_body(self):
        for status in ('ZERO_RESULTS', 'INVALID_REQUEST', 'OVER_QUERY_LIMIT', 'OVER_DAILY_LIMIT', 'UNKNOWN_ERROR', 'REQUEST_DENIED'):
            with self.subTest(status=status):
                response = self.response({'status': status, 'error_message': 'private-test-key precise-location secret'})
                with patch('apps.inventory.geocoding.urlopen', return_value=response), self.assertLogs('apps.inventory.geocoding') as logs:
                    with self.assertRaises(GeocodingUnavailable) as error:
                        GeocodingService().reverse_geocode(36, 3)
                self.assertEqual(error.exception.code, status.lower())
                self.assertIn('http_status=200', logs.output[0])
                self.assertIn('provider_status=' + status, logs.output[0])
                self.assertNotIn('private-test-key', str(logs.output))
                self.assertNotIn('precise-location', str(logs.output))

    def test_denial_classification_handles_http_error_json(self):
        explanations = {
            'API keys with referer restrictions cannot be used with this API.': 'referrer_restriction',
            'This IP is not authorized to use this API key.': 'ip_restriction',
            'This API project is not authorized to use this API.': 'api_not_enabled',
            'The provided API key is invalid.': 'invalid_key',
            'Billing must be enabled.': 'billing',
        }
        for explanation, code in explanations.items():
            body = json.dumps({'status': 'REQUEST_DENIED', 'error_message': explanation}).encode()
            failure = HTTPError('https://example.invalid/?key=private-test-key', 403, 'Forbidden', {}, io.BytesIO(body))
            with patch('apps.inventory.geocoding.urlopen', side_effect=failure), self.assertLogs('apps.inventory.geocoding') as logs:
                with self.assertRaises(GeocodingUnavailable) as error:
                    GeocodingService().reverse_geocode(36, 3)
            self.assertEqual(error.exception.code, code)
            self.assertIn('http_status=403', logs.output[0])
            self.assertNotIn('private-test-key', str(logs.output))

    def test_malformed_and_empty_results_are_distinguished(self):
        for payload, code in (([], 'invalid_response'), ({'status': 'unexpected secret'}, 'unexpected_status'),
                              ({'status': 'OK', 'results': []}, 'invalid_results'),
                              ({'status': 'OK', 'results': [{'formatted_address': '  '}]}, 'empty_address')):
            with patch('apps.inventory.geocoding.urlopen', return_value=self.response(payload)), self.assertLogs('apps.inventory.geocoding'):
                with self.assertRaises(GeocodingUnavailable) as error:
                    GeocodingService().reverse_geocode(36, 3)
            self.assertEqual(error.exception.code, code)
        response = self.response({})
        response.read.return_value = b'not-json private-test-key'
        with patch('apps.inventory.geocoding.urlopen', return_value=response), self.assertLogs('apps.inventory.geocoding') as logs:
            with self.assertRaises(GeocodingUnavailable) as error:
                GeocodingService().reverse_geocode(36, 3)
        self.assertEqual(error.exception.code, 'invalid_json')
        self.assertNotIn('private-test-key', str(logs.output))

    def test_network_failure_does_not_log_secret_url(self):
        for failure, code in ((URLError('https://example.invalid/?key=private-test-key'), 'network_error'), (TimeoutError(), 'timeout')):
            with patch('apps.inventory.geocoding.urlopen', side_effect=failure), self.assertLogs('apps.inventory.geocoding') as logs:
                with self.assertRaises(GeocodingUnavailable) as error:
                    GeocodingService().reverse_geocode(36, 3)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('private-test-key', str(logs.output))
