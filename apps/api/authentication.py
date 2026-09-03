from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.security import log_security_event


class MobileTokenSerializer(TokenObtainPairSerializer):
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


class MobileTokenRefreshView(TokenRefreshView):
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
            RefreshToken(refresh_value).blacklist()
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
