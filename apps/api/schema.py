"""Describe the JSON envelope actually emitted by EnvelopeJSONRenderer."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class VersionedJWTScheme(OpenApiAuthenticationExtension):
    target_class = 'apps.api.jwt_auth.VersionedJWTAuthentication'
    name = 'jwtAuth'

    def get_security_definition(self, auto_schema):
        return {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'}


def envelope_responses(result, generator, request, public):
    error_schema = {
        'type': 'object',
        'required': ['success', 'error'],
        'properties': {
            'success': {'type': 'boolean', 'enum': [False]},
            'error': {
                'type': 'object', 'required': ['code', 'message'],
                'properties': {
                    'code': {'type': 'string'}, 'message': {'type': 'string'},
                    'details': {},
                },
            },
        },
    }
    result.setdefault('components', {}).setdefault('schemas', {})['APIError'] = error_schema
    for path, operations in result['paths'].items():
        if not path.startswith('/api/v1/'):
            continue
        for method, operation in operations.items():
            if method not in ('get', 'post', 'put', 'patch', 'delete'):
                continue
            responses = operation.get('responses', {})
            for code, response in responses.items():
                content = response.get('content', {}).get('application/json')
                if content is not None and str(code).startswith('2'):
                    content['schema'] = {
                        'type': 'object', 'required': ['success', 'data'],
                        'properties': {
                            'success': {'type': 'boolean', 'enum': [True]},
                            'data': content.get('schema', {}),
                        },
                    }
            for code, description in [('400', 'Invalid input'), ('401', 'Authentication required'),
                                      ('403', 'Permission denied'), ('404', 'Resource not found'),
                                      ('429', 'Rate limit exceeded')]:
                responses.setdefault(code, {'description': description, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/APIError'}}}})
    return result
