from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model


class AuthTests(TestCase):
    def test_login_view_and_authentication(self):
        User = get_user_model()
        user = User.objects.create_user(username='authuser', password='secret')
        url = reverse('login')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # attempt login
        resp = self.client.post(url, {'username': 'authuser', 'password': 'secret'})
        # login view should redirect on success
        self.assertIn(resp.status_code, (302, 301))

    def test_logout(self):
        User = get_user_model()
        user = User.objects.create_user(username='authuser2', password='secret')
        self.client.login(username='authuser2', password='secret')
        resp = self.client.get(reverse('logout'))
        # logout redirects to login
        self.assertIn(resp.status_code, (302, 301))
