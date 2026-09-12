"""Optional server-side provider; never expose credentials or provider errors."""
import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.utils.translation import get_language

from .location import validate_location


class GeocodingUnavailable(Exception):
    pass


class GoogleGeocoder:
    def reverse_geocode(self, latitude, longitude):
        key = settings.GOOGLE_MAPS_API_KEY
        if not key:
            raise GeocodingUnavailable()
        query = urlencode({'latlng': f'{latitude},{longitude}', 'key': key, 'language': get_language() or 'fr'})
        try:
            with urlopen('https://maps.googleapis.com/maps/api/geocode/json?' + query, timeout=5) as response:
                payload = json.loads(response.read(1024 * 1024))
            if payload.get('status') != 'OK' or not payload.get('results'):
                raise GeocodingUnavailable()
            result = payload['results'][0]
            address, place_id = result.get('formatted_address', ''), result.get('place_id', '')
            if not isinstance(address, str) or not isinstance(place_id, str) or len(address) > 1000 or len(place_id) > 255:
                raise GeocodingUnavailable()
            return {'formatted_address': address, 'place_id': place_id}
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
            raise GeocodingUnavailable() from None


class GeocodingService:
    def __init__(self, provider=None):
        self.provider = provider if provider is not None else GoogleGeocoder()

    def reverse_geocode(self, latitude, longitude):
        validate_location(latitude, longitude)
        return self.provider.reverse_geocode(latitude, longitude)
