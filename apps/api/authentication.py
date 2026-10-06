from django.conf import settings
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework.exceptions import APIException, AuthenticationFailed, Throttled
from django.contrib.auth import get_user_model
from django.contrib.auth import password_validation
from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.services import PasswordResetDeliveryError, PasswordResetService
from apps.core.security import (
    is_password_reset_rate_limited,
    log_security_event,
    password_reset_rate_limit_action,
)
from .jwt_auth import TOKEN_VERSION_CLAIM, TokenRevoked


class MobileTokenSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token[TOKEN_VERSION_CLAIM] = user.auth_token_version
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        if self.user.force_password_change:
            raise serializers.ValidationError(
                {'detail': _('Vous devez modifier votre mot de passe depuis l’application Web.')},
                code='password_change_required',
            )
        data['user'] = {
            'id': self.user.pk,
            'username': self.user.get_username(),
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'role': self.user.primary_role.name if self.user.primary_role else self.user.get_role_display(),
        }
        return data


class MobileTokenView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = MobileTokenSerializer
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        log_security_event(request, 'api.auth.login.success', user=self.user_from_response(request), status_code=200)
        return response

    def handle_exception(self, exc):
        log_security_event(self.request, 'api.auth.login.failed', level='warning', status_code=401)
        return super().handle_exception(exc)

    @staticmethod
    def user_from_response(request):
        from django.contrib.auth import get_user_model

        username = request.data.get('username', '')
        return get_user_model().objects.filter(username=username).first()


class MobileTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        refresh = self.token_class(attrs['refresh'])
        user = get_user_model().objects.filter(
            **{api_settings.USER_ID_FIELD: refresh.get(api_settings.USER_ID_CLAIM)}
        ).first()
        if user is None or not user.is_active or user.force_password_change:
            raise AuthenticationFailed(_('Compte indisponible ou changement de mot de passe requis.'))
        if refresh.get(TOKEN_VERSION_CLAIM) != user.auth_token_version:
            raise TokenRevoked()
        return super().validate(attrs)


class MobileTokenRefreshView(TokenRefreshView):
    serializer_class = MobileTokenRefreshSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'auth'


class LogoutView(APIView):
    @extend_schema(
        request=inline_serializer('LogoutRequest', {'refresh': serializers.CharField()}),
        responses=inline_serializer('LogoutResponse', {'message': serializers.CharField()}),
    )
    def post(self, request):
        refresh_value = request.data.get('refresh')
        if not refresh_value:
            raise serializers.ValidationError({'refresh': _('Le refresh token est obligatoire.')})
        try:
            token = RefreshToken(refresh_value)
            if str(token.get(api_settings.USER_ID_CLAIM)) != str(getattr(request.user, api_settings.USER_ID_FIELD)):
                raise serializers.ValidationError({'refresh': _('Refresh token invalide.')})
            token.blacklist()
        except TokenError as exc:
            raise serializers.ValidationError({'refresh': _('Refresh token invalide.')}) from exc
        log_security_event(request, 'api.auth.logout', status_code=200)
        return Response({'message': _('Déconnexion effectuée.')}, status=status.HTTP_200_OK)


class CurrentUserView(APIView):
    @extend_schema(responses=dict)
    def get(self, request):
        from apps.accounts.permissions import ALL_MANAGED_PERMISSION_NAMES, has_permission

        user = request.user
        return Response({
            'id': user.pk,
            'username': user.get_username(),
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'phone': user.phone,
            'role': user.primary_role.name if user.primary_role else user.get_role_display(),
            'permissions': sorted(
                permission for permission in ALL_MANAGED_PERMISSION_NAMES
                if has_permission(user, permission)
            ),
        })


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = self.context['request'].user
        if not user.check_password(attrs['current_password']):
            raise serializers.ValidationError({'current_password': _('Mot de passe actuel incorrect.')})
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({'new_password_confirm': _('Les mots de passe ne correspondent pas.')})
        password_validation.validate_password(attrs['new_password'], user=user)
        return attrs


class PasswordChangeView(APIView):
    @extend_schema(
        request=PasswordChangeSerializer,
        responses=inline_serializer('PasswordChangeResponse', {'message': serializers.CharField()}),
    )
    @transaction.atomic
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        user.set_password(serializer.validated_data['new_password'])
        user.force_password_change = False
        user.save(update_fields=['password', 'force_password_change'])
        user.revoke_api_tokens()
        log_security_event(request, 'api.auth.password_change', status_code=200)
        return Response({'message': _('Mot de passe modifié. Reconnectez-vous.')})


class PasswordResetDeliveryUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    business_code = 'PASSWORD_RESET_DELIVERY_UNAVAILABLE'
    business_message = _('Le service d’envoi est temporairement indisponible.')
    default_detail = business_message


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class PasswordResetRequestView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'password_reset'

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses=inline_serializer(
            'PasswordResetRequestResponse', {'message': serializers.CharField()},
        ),
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        if is_password_reset_rate_limited(request, email):
            log_security_event(
                request, 'api.auth.password_reset.blocked',
                level='warning', status_code=429,
            )
            raise Throttled(
                wait=getattr(settings, 'PASSWORD_RESET_WINDOW_SECONDS', 3600),
                detail=_('Trop de demandes. Réessayez plus tard.'),
            )
        log_security_event(request, password_reset_rate_limit_action(email))
        try:
            dispatched = PasswordResetService().request(email, request=request)
        except PasswordResetDeliveryError as exc:
            log_security_event(
                request, 'api.auth.password_reset.delivery_failed',
                level='error', status_code=503,
            )
            raise PasswordResetDeliveryUnavailable() from exc
        if dispatched:
            log_security_event(request, 'api.auth.password_reset.email_dispatched')
        return Response({
            'message': _(
                'Si une adresse correspond à un compte, un lien de réinitialisation a été envoyé.'
            ),
        })


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=128)
    token = serializers.CharField(max_length=256, write_only=True)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)


class PasswordResetConfirmView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'password_reset'

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses=inline_serializer(
            'PasswordResetConfirmResponse', {'message': serializers.CharField()},
        ),
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = PasswordResetService().confirm(**serializer.validated_data)
        log_security_event(
            request, 'api.auth.password_reset.completed', user=user, status_code=200,
        )
        return Response({'message': _('Mot de passe réinitialisé. Vous pouvez vous connecter.')})
