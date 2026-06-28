from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.storage import default_storage
from django.conf import settings
import tempfile
import shutil

from ..models import Product, ProductPackaging, Category, Brand, Unit
from django.contrib.auth import get_user_model


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ProductModelTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Cat1')
        self.brand = Brand.objects.create(name='Brand1')
        self.unit = Unit.objects.create(name='Unit1')
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pass', role=User.MANAGER)

    def test_reference_qr_and_barcode_generated_on_save(self):
        p = Product.objects.create(
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
        self.assertTrue(p.barcode.startswith('BC-PRD-'))
        self.assertIsNotNone(p.barcode_image)
        self.assertTrue(default_storage.exists(p.barcode_image.name))
        self.assertIsNotNone(p.qr_code)
        self.assertTrue(default_storage.exists(p.qr_code.name))

    def test_create_product_view_creates_qr(self):
        self.client.login(username='tester', password='pass')
        url = reverse('product_create')
        data = {
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
        self.assertTrue(p.barcode.startswith('BC-PRD-'))
        self.assertIsNotNone(p.barcode_image)
        self.assertTrue(default_storage.exists(p.barcode_image.name))
        self.assertIsNotNone(p.qr_code)
        self.assertTrue(default_storage.exists(p.qr_code.name))

    def test_product_detail_displays_barcode(self):
        self.client.login(username='tester', password='pass')
        product = Product.objects.create(
            name='Barcode Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=5,
            minimum_stock=1,
        )
        response = self.client.get(reverse('product_detail', args=[product.pk]))
        self.assertContains(response, product.reference)
        self.assertContains(response, product.barcode)
        self.assertContains(response, 'Code-barres')

    def test_barcode_download(self):
        self.client.login(username='tester', password='pass')
        product = Product.objects.create(
            name='Download Barcode',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=5,
            minimum_stock=1,
        )
        response = self.client.get(reverse('product_barcode_download', args=[product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])

    def test_product_packaging_purchase_cost(self):
        product = Product.objects.create(
            name='Packaged Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=48,
            minimum_stock=1,
        )
        packaging = ProductPackaging.objects.create(
            product=product,
            name='Carton',
            unit_quantity=24,
            default_sale_price='300.00',
        )
        self.assertEqual(packaging.purchase_cost, product.purchase_price * 24)

    def test_product_detail_displays_packagings(self):
        self.client.login(username='tester', password='pass')
        product = Product.objects.create(
            name='Packaging Detail',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=48,
            minimum_stock=1,
        )
        ProductPackaging.objects.create(
            product=product,
            name='Carton',
            unit_quantity=24,
            default_sale_price='300.00',
        )
        response = self.client.get(reverse('product_detail', args=[product.pk]))
        self.assertContains(response, 'Conditionnements')
        self.assertContains(response, 'Carton')
        self.assertContains(response, '24')
