from django.utils.translation import gettext as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


TOKEN_VERSION_CLAIM = 'token_version'


class TokenRevoked(AuthenticationFailed):
    business_code = 'TOKEN_REVOKED'
    business_message = _('Ce jeton a été révoqué. Reconnectez-vous.')

    def __init__(self):
        super().__init__(self.business_message, code='token_revoked')


class VersionedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if validated_token.get(TOKEN_VERSION_CLAIM) != user.auth_token_version:
            raise TokenRevoked()
        return user
