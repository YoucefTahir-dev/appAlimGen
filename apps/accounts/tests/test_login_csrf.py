from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
)
class LoginCsrfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = 'CsrfRegression123!'
        cls.admin = get_user_model().objects.create_superuser(
            username='csrf-admin', password=cls.password,
        )

    def setUp(self):
        # The default Django client bypasses CSRF and cannot reproduce this bug.
        self.client = Client(enforce_csrf_checks=True)

    def login_page_token(self, client=None):
        response = (client or self.client).get(reverse('login'), secure=True)
        self.assertEqual(response.status_code, 200)
        return str(response.context['csrf_token'])

    def submit(self, token, *, username=None, client=None, **headers):
        return (client or self.client).post(
            reverse('login'),
            {
                'username': username or self.admin.username,
                'password': self.password,
                'csrfmiddlewaretoken': token,
            },
            secure=True,
            HTTP_ORIGIN=headers.pop('HTTP_ORIGIN', 'https://testserver'),
            **headers,
        )

    def test_login_rotates_token_and_rejects_duplicate_post_without_switching_account(self):
        token = self.login_page_token()
        old_cookie = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        first = self.submit(token)
        self.assertRedirects(first, reverse('dashboard'), fetch_redirect_response=False)
        self.assertNotEqual(old_cookie, self.client.cookies[settings.CSRF_COOKIE_NAME].value)

        other = get_user_model().objects.create_user(
            username='csrf-other', password=self.password,
        )
        duplicate = self.submit(token, username=other.username)
        self.assertEqual(duplicate.status_code, 403)
        self.assertTemplateUsed(duplicate, 'accounts/csrf_failure.html')
        self.assertContains(duplicate, 'Accéder à mon espace', status_code=403)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.admin.pk))
        self.assertEqual(AuditLog.objects.filter(action='Connexion réussie').count(), 1)
        self.assertIn('no-store', duplicate['Cache-Control'])

        # Recovery is a GET; it never replays the rejected password submission.
        recovery = self.client.get(reverse('login'), secure=True)
        self.assertRedirects(recovery, reverse('dashboard'), fetch_redirect_response=False)

    def test_two_independent_browser_profiles_can_login(self):
        second_client = Client(enforce_csrf_checks=True)
        first_token = self.login_page_token()
        second_token = self.login_page_token(second_client)
        self.assertRedirects(self.submit(first_token), reverse('dashboard'), fetch_redirect_response=False)
        self.assertRedirects(
            self.submit(second_token, client=second_client),
            reverse('dashboard'), fetch_redirect_response=False,
        )

    def test_authenticated_login_get_respects_permissions(self):
        role = Group.objects.create(name='CSRF lecteur produits')
        role.permissions.add(Permission.objects.get(
            content_type__app_label='inventory', codename='view_product',
        ))
        reader = get_user_model().objects.create_user(
            username='csrf-reader', password=self.password,
        )
        reader.groups.add(role)
        self.client.force_login(reader)
        response = self.client.get(reverse('login'), secure=True)
        self.assertRedirects(response, reverse('product_list'), fetch_redirect_response=False)

    def test_self_or_external_next_never_causes_a_redirect_loop(self):
        self.client.force_login(self.admin)
        for destination in ('/', '/?next=/', 'https://testserver/', 'https://evil.invalid/'):
            with self.subTest(destination=destination):
                response = self.client.get(reverse('login'), {'next': destination}, secure=True)
                self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_safe_next_is_preserved_in_login_form_and_after_login(self):
        response = self.client.get(
            reverse('login'), {'next': reverse('product_list')}, secure=True,
        )
        self.assertContains(response, 'name="next"')
        token = str(response.context['csrf_token'])
        response = self.client.post(
            reverse('login'),
            {
                'username': self.admin.username, 'password': self.password,
                'csrfmiddlewaretoken': token, 'next': reverse('product_list'),
            },
            secure=True, HTTP_ORIGIN='https://testserver',
        )
        self.assertRedirects(response, reverse('product_list'), fetch_redirect_response=False)

    def test_anonymous_stale_form_has_a_fresh_get_recovery_without_authentication(self):
        self.login_page_token()
        response = self.submit('A' * 64)
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'accounts/csrf_failure.html')
        self.assertContains(response, 'Recharger la connexion', status_code=403)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotContains(response, self.password, status_code=403)

        fresh_token = self.login_page_token()
        self.assertEqual(self.submit(fresh_token).status_code, 302)

    def test_invalid_origin_is_rejected_even_with_a_valid_token(self):
        response = self.submit(self.login_page_token(), HTTP_ORIGIN='https://evil.invalid')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_missing_csrf_cookie_and_token_are_rejected(self):
        response = self.submit('')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_recovery_page_is_translated_and_does_not_echo_submitted_data(self):
        for language, expected in (
            ('ar', 'إعادة تحميل صفحة الدخول'),
            ('en', 'Reload sign-in'),
        ):
            with self.subTest(language=language):
                self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
                response = self.submit('')
                self.assertContains(response, expected, status_code=403)
                self.assertNotContains(response, self.password, status_code=403)
                self.assertNotContains(response, self.admin.username, status_code=403)

    def test_authenticated_business_post_with_invalid_csrf_cannot_change_profile(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('profile'),
            {'update_profile': '1', 'first_name': 'ATTACK', 'csrfmiddlewaretoken': 'A' * 64},
            secure=True, HTTP_ORIGIN='https://testserver',
        )
        self.assertEqual(response.status_code, 403)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, '')

    @override_settings(DEBUG=True)
    def test_recovery_page_does_not_expose_csrf_diagnostics_even_in_debug(self):
        response = self.submit('')
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'Reason given for failure', status_code=403)
        self.assertNotContains(response, 'DEBUG', status_code=403)
