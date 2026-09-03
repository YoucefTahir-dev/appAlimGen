from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler


STATUS_CODES = {
    400: 'VALIDATION_ERROR',
    401: 'AUTHENTICATION_REQUIRED',
    403: 'PERMISSION_DENIED',
    404: 'NOT_FOUND',
    405: 'METHOD_NOT_ALLOWED',
    429: 'RATE_LIMITED',
}


class BusinessAPIException(APIException):
    status_code = 400

    def __init__(self, code, message, details=None):
        self.business_code = code
        self.business_message = str(message)
        self.business_details = details
        super().__init__(self.business_message, code=code)


def django_validation_detail(exception):
    if hasattr(exception, 'message_dict'):
        return exception.message_dict
    return exception.messages


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(django_validation_detail(exc))
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, BusinessAPIException):
        response.data = {
            'success': False,
            'error': {
                'code': exc.business_code,
                'message': exc.business_message,
                **({'details': exc.business_details} if exc.business_details is not None else {}),
            },
        }
        return response

    details = response.data
    if isinstance(details, dict) and set(details) == {'detail'}:
        message = str(details['detail'])
        details = None
    else:
        message = _('Les données envoyées sont invalides.')

    response.data = {
        'success': False,
        'error': {
            'code': STATUS_CODES.get(response.status_code, 'API_ERROR'),
            'message': message,
            **({'details': details} if details is not None else {}),
        },
    }
    return response
