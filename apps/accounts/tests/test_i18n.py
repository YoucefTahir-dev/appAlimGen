from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class InternationalizationTests(TestCase):
    def test_login_displays_language_switcher(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="language"')
        self.assertContains(response, 'value="fr"')
        self.assertContains(response, 'value="ar"')

    def test_arabic_language_switch_is_persisted_and_uses_rtl(self):
        response = self.client.post(
            reverse('set_language'),
            {'language': 'ar', 'next': reverse('login')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.client.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'ar')
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, 'تسجيل الدخول')
