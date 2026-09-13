"""Server-side reverse geocoding with safe, actionable diagnostics."""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.utils.translation import get_language

from .location import validate_location

logger = logging.getLogger(__name__)
PROVIDER_STATUSES = {'OK', 'ZERO_RESULTS', 'REQUEST_DENIED', 'INVALID_REQUEST',
                     'OVER_QUERY_LIMIT', 'OVER_DAILY_LIMIT', 'UNKNOWN_ERROR'}


class GeocodingUnavailable(Exception):
    def __init__(self, code='unavailable'):
        self.code = code
        super().__init__(code)


def unavailable(code, http_status=None, provider_status=None):
    # Never log URLs, keys, coordinates, raw provider bodies or exception strings.
    logger.warning('geocoding.failed provider=google code=%s http_status=%s provider_status=%s',
                   code, http_status, provider_status)
    raise GeocodingUnavailable(code) from None


def denial_reason(payload):
    # Classify documented provider explanations, without retaining their contents.
    message = str(payload.get('error_message', '')).lower()
    if 'referer' in message or 'referrer' in message:
        return 'referrer_restriction'
    if 'ip' in message and 'not authorized' in message:
        return 'ip_restriction'
    if 'not authorized to use this api' in message or 'not enabled' in message or 'disabled' in message:
        return 'api_not_enabled'
    if 'api key' in message and ('invalid' in message or 'expired' in message):
        return 'invalid_key'
    if 'billing' in message:
        return 'billing'
    return 'request_denied'


class GoogleGeocoder:
    def reverse_geocode(self, latitude, longitude):
        key = settings.GOOGLE_MAPS_API_KEY.strip()
        if not key:
            unavailable('missing_key')
        query = urlencode({'latlng': f'{latitude},{longitude}', 'key': key, 'language': get_language() or 'fr'})
        http_status = None
        try:
            try:
                response = urlopen('https://maps.googleapis.com/maps/api/geocode/json?' + query, timeout=5)
            except HTTPError as exc:
                # Google can return a useful JSON status even for HTTP 4xx/5xx.
                response = exc
            with response as received:
                candidate = getattr(response, 'status', None)
                http_status = candidate if isinstance(candidate, int) else None
                raw = received.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                unavailable('response_too_large', http_status)
            payload = json.loads(raw)
        except (TimeoutError, URLError, OSError) as exc:
            timeout = isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError)
            unavailable('timeout' if timeout else 'network_error', http_status)
        except (ValueError, TypeError):
            unavailable('invalid_json', http_status)
        if not isinstance(payload, dict):
            unavailable('invalid_response', http_status)
        status = payload.get('status')
        if not isinstance(status, str) or status not in PROVIDER_STATUSES:
            unavailable('unexpected_status', http_status)
        if status != 'OK':
            code = denial_reason(payload) if status == 'REQUEST_DENIED' else status.lower()
            unavailable(code, http_status, status)
        if http_status is not None and not 200 <= http_status < 300:
            unavailable('http_error', http_status, status)
        results = payload.get('results')
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            unavailable('invalid_results', http_status, status)
        result = results[0]
        address, place_id = result.get('formatted_address'), result.get('place_id') or ''
        if not isinstance(address, str) or not address.strip():
            unavailable('empty_address', http_status, status)
        if not isinstance(place_id, str) or len(address) > 1000 or len(place_id) > 255:
            unavailable('invalid_response', http_status, status)
        logger.info('geocoding.success provider=google http_status=%s provider_status=OK', http_status)
        return {'formatted_address': address.strip(), 'place_id': place_id}


class GeocodingService:
    def __init__(self, provider=None):
        self.provider = provider if provider is not None else GoogleGeocoder()

    def reverse_geocode(self, latitude, longitude):
        validate_location(latitude, longitude)
        return self.provider.reverse_geocode(latitude, longitude)
