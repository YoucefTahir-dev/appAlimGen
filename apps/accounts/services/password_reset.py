import json
import logging
import socket
from dataclasses import dataclass
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _


logger = logging.getLogger('security')


class PasswordResetDeliveryError(RuntimeError):
    """The reset request is valid, but its notification could not be delivered."""


class EmailDeliveryProviderError(RuntimeError):
    """Sanitized provider failure safe to report in production logs."""

    def __init__(
        self,
        *,
        provider: str,
        transport: str,
        stage: str,
        exception_type: str,
        timeout: bool = False,
        status: int | None = None,
        provider_code: str | None = None,
    ):
        super().__init__('The email provider rejected or could not complete the request.')
        self.provider = provider
        self.transport = transport
        self.stage = stage
        self.exception_type = exception_type
        self.timeout = timeout
        self.status = status
        self.provider_code = provider_code


@dataclass(frozen=True)
class PasswordResetMessage:
    recipient: str
    subject: str
    text_body: str
    html_body: str


class DjangoEmailDeliveryService:
    """Provider-neutral adapter around Django's configured email backend."""

    def send(self, message: PasswordResetMessage) -> None:
        from_email = settings.DEFAULT_FROM_EMAIL
        if '<' not in from_email and settings.DEFAULT_FROM_NAME:
            from_email = f'{settings.DEFAULT_FROM_NAME} <{from_email}>'
        email = EmailMultiAlternatives(
            subject=message.subject,
            body=message.text_body,
            from_email=from_email,
            to=[message.recipient],
        )
        email.attach_alternative(message.html_body, 'text/html')
        try:
            email.send(fail_silently=False)
        except Exception as exc:
            raise EmailDeliveryProviderError(
                provider='django',
                transport='smtp' if 'smtp' in settings.EMAIL_BACKEND.lower() else 'django',
                stage='send',
                exception_type=type(exc).__name__,
                timeout=isinstance(exc, (TimeoutError, socket.timeout)),
                status=getattr(exc, 'smtp_code', None),
            ) from exc


def _safe_provider_code(response_body: bytes) -> str | None:
    """Return only Brevo's short machine code, never its free-form message."""
    try:
        code = json.loads(response_body.decode('utf-8')).get('code')
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(code, str) or not code:
        return None
    sanitized = ''.join(char for char in code if char.isalnum() or char in '._-')
    return sanitized[:64] or None


class BrevoApiEmailDeliveryService:
    """Brevo transactional HTTPS API transport, suitable for Render Free."""

    provider = 'brevo'
    transport = 'https_api'

    def send(self, message: PasswordResetMessage) -> None:
        api_key = settings.BREVO_API_KEY.strip()
        if not api_key:
            raise EmailDeliveryProviderError(
                provider=self.provider,
                transport=self.transport,
                stage='configuration',
                exception_type='MissingBrevoApiKey',
                provider_code='missing_api_key',
            )

        parsed_name, sender_email = parseaddr(settings.DEFAULT_FROM_EMAIL)
        sender_name = settings.DEFAULT_FROM_NAME.strip() or parsed_name
        if not sender_email:
            raise EmailDeliveryProviderError(
                provider=self.provider,
                transport=self.transport,
                stage='configuration',
                exception_type='MissingSenderEmail',
                provider_code='missing_sender',
            )

        payload = {
            'sender': {'email': sender_email, 'name': sender_name},
            'to': [{'email': message.recipient}],
            'subject': message.subject,
            'textContent': message.text_body,
            'htmlContent': message.html_body,
            'tags': ['password-reset'],
        }
        request = Request(
            settings.BREVO_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'accept': 'application/json',
                'api-key': api_key,
                'content-type': 'application/json',
                'user-agent': 'El-Amine-ERP/1.0',
            },
            method='POST',
        )
        try:
            with urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                status = response.getcode()
                if not 200 <= status < 300:
                    raise EmailDeliveryProviderError(
                        provider=self.provider,
                        transport=self.transport,
                        stage='response',
                        exception_type='UnexpectedHttpStatus',
                        status=status,
                    )
        except EmailDeliveryProviderError:
            raise
        except HTTPError as exc:
            body = exc.read(4096)
            raise EmailDeliveryProviderError(
                provider=self.provider,
                transport=self.transport,
                stage='response',
                exception_type=type(exc).__name__,
                status=exc.code,
                provider_code=_safe_provider_code(body),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise EmailDeliveryProviderError(
                provider=self.provider,
                transport=self.transport,
                stage='connect',
                exception_type=type(exc).__name__,
                timeout=True,
            ) from exc
        except URLError as exc:
            reason = getattr(exc, 'reason', None)
            raise EmailDeliveryProviderError(
                provider=self.provider,
                transport=self.transport,
                stage='connect',
                exception_type=type(reason).__name__ if reason else type(exc).__name__,
                timeout=isinstance(reason, (TimeoutError, socket.timeout)),
            ) from exc


def configured_email_delivery_service():
    provider = settings.PASSWORD_RESET_EMAIL_PROVIDER
    if provider == 'brevo_api':
        return BrevoApiEmailDeliveryService()
    return DjangoEmailDeliveryService()


class EmailPasswordResetDelivery:
    """Email channel. A future SMS channel can implement the same boundary."""

    def __init__(self, email_service=None):
        self.email_service = email_service or configured_email_delivery_service()

    def deliver(self, *, user, reset_url: str) -> None:
        timeout_minutes = max(1, settings.PASSWORD_RESET_TIMEOUT // 60)
        context = {
            'user': user,
            'reset_url': reset_url,
            'timeout_minutes': timeout_minutes,
            'default_from_name': settings.DEFAULT_FROM_NAME,
        }
        subject = render_to_string(
            'accounts/password_reset_subject.txt', context,
        ).strip().replace('\n', ' ')
        message = PasswordResetMessage(
            recipient=user.email,
            subject=subject,
            text_body=render_to_string(
                'accounts/password_reset_email.txt', context,
            ),
            html_body=render_to_string(
                'accounts/password_reset_email.html', context,
            ),
        )
        self.email_service.send(message)


class PasswordResetService:
    """Single password-reset source of truth for Web, API and admin clients."""

    def __init__(self, delivery=None, token_generator=None):
        self.delivery = delivery or EmailPasswordResetDelivery()
        self.token_generator = token_generator or default_token_generator

    def request(self, email: str, *, request=None) -> int:
        normalized = get_user_model().objects.normalize_email(email).strip()
        users = get_user_model()._default_manager.filter(
            email__iexact=normalized,
            is_active=True,
        )
        dispatched = 0
        for user in users:
            if not user.has_usable_password() or not user.email:
                continue
            self.request_for_user(user, request=request)
            dispatched += 1
        return dispatched

    def request_for_user(self, user, *, request=None) -> None:
        if not user.is_active or not user.email or not user.has_usable_password():
            raise ValueError(_('Aucune adresse e-mail active n’est configurée.'))
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = self.token_generator.make_token(user)
        path = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
        try:
            self.delivery.deliver(user=user, reset_url=self._absolute_url(path, request))
        except EmailDeliveryProviderError as exc:
            logger.error(
                'Password reset delivery failed provider=%s transport=%s '
                'stage=%s exception=%s timeout=%s status=%s code=%s',
                exc.provider,
                exc.transport,
                exc.stage,
                exc.exception_type,
                str(exc.timeout).lower(),
                exc.status if exc.status is not None else 'none',
                exc.provider_code or 'none',
            )
            raise PasswordResetDeliveryError from exc
        except PasswordResetDeliveryError:
            raise
        except Exception as exc:
            # Log the class only: exception messages can contain credentials,
            # recipients or reset URLs depending on the backend.
            logger.error(
                'Password reset delivery failed provider=unknown transport=unknown '
                'stage=delivery exception=%s timeout=%s status=none code=none',
                type(exc).__name__,
                str(isinstance(exc, (TimeoutError, socket.timeout))).lower(),
            )
            raise PasswordResetDeliveryError from exc

    def validate_token(self, uid: str, token: str):
        try:
            user_id = urlsafe_base64_decode(uid).decode()
            user = get_user_model()._default_manager.get(pk=user_id, is_active=True)
        except (TypeError, ValueError, OverflowError, UnicodeDecodeError, get_user_model().DoesNotExist):
            return None
        return user if self.token_generator.check_token(user, token) else None

    @transaction.atomic
    def confirm(
        self,
        *,
        uid: str,
        token: str,
        new_password: str,
        new_password_confirm: str,
    ):
        if new_password != new_password_confirm:
            from django.core.exceptions import ValidationError

            raise ValidationError({
                'new_password_confirm': _('Les mots de passe ne correspondent pas.'),
            })
        user = self.validate_token(uid, token)
        if user is None:
            from django.core.exceptions import ValidationError

            raise ValidationError({'token': _('Le lien est invalide ou a expiré.')})
        password_validation.validate_password(new_password, user=user)
        locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
        if not self.token_generator.check_token(locked_user, token):
            from django.core.exceptions import ValidationError

            raise ValidationError({'token': _('Le lien est invalide ou a expiré.')})
        return self.complete_for_user(locked_user, new_password)

    @staticmethod
    @transaction.atomic
    def complete_for_user(user, new_password: str):
        user.set_password(new_password)
        user.force_password_change = False
        user.save(update_fields=['password', 'force_password_change'])
        user.revoke_api_tokens()
        return user

    @staticmethod
    def _absolute_url(path: str, request=None) -> str:
        base_url = settings.PASSWORD_RESET_PUBLIC_BASE_URL.strip()
        if base_url:
            return urljoin(f'{base_url.rstrip("/")}/', path.lstrip('/'))
        if request is not None:
            return request.build_absolute_uri(path)
        raise PasswordResetDeliveryError(
            'PASSWORD_RESET_PUBLIC_BASE_URL is required without an HTTP request.'
        )
