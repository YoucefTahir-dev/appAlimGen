from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = (renderer_context or {}).get('response')
        if response is not None and response.exception:
            payload = data if isinstance(data, dict) and 'success' in data else {
                'success': False,
                'error': {'code': 'API_ERROR', 'message': str(data)},
            }
        elif isinstance(data, dict) and 'success' in data:
            payload = data
        else:
            payload = {'success': True, 'data': data}
        return super().render(payload, accepted_media_type, renderer_context)
