from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.storage import default_storage
from django.conf import settings
import tempfile
import shutil

from ..models import Product, Category, Brand, Unit
from django.contrib.auth import get_user_model


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ProductModelTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Cat1')
        self.brand = Brand.objects.create(name='Brand1')
        self.unit = Unit.objects.create(name='Unit1')
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pass')

    def test_reference_and_qr_generated_on_save(self):
        p = Product.objects.create(
            barcode='123',
            name='Test Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=5,
            minimum_stock=1,
        )
        self.assertTrue(p.reference.startswith('PRD-'))
        self.assertIsNotNone(p.qr_code)
        self.assertTrue(default_storage.exists(p.qr_code.name))

    def test_create_product_view_creates_qr(self):
        self.client.login(username='tester', password='pass')
        url = reverse('product_create')
        data = {
            'barcode': '321',
            'name': 'View Product',
            'brand_text': 'Brand1',
            'purchase_price': '5.00',
            'sale_price': '8.00',
            'quantity': 2,
            'minimum_stock': 0,
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        p = Product.objects.filter(name='View Product').first()
        self.assertIsNotNone(p)
        self.assertTrue(p.reference.startswith('PRD-'))
        self.assertIsNotNone(p.qr_code)
        self.assertTrue(default_storage.exists(p.qr_code.name))
