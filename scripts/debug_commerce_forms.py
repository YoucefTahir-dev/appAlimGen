import os
import sys
import django

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestio_stock.settings')
django.setup()

from django.conf import settings
from django.test import Client
from django.contrib.auth import get_user_model
from apps.inventory.models import Category, Brand, Unit, Product, Client as ClientModel, Supplier

if hasattr(settings, 'ALLOWED_HOSTS'):
    if 'testserver' not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS.append('testserver')

User = get_user_model()
User.objects.filter(username='debugger').delete()
user = User.objects.create_user(username='debugger', password='pass')
cat = Category.objects.create(name='Cat1')
brand = Brand.objects.create(name='Brand1')
unit = Unit.objects.create(name='Unit1')
prod = Product.objects.create(
    barcode='123',
    name='Test Product',
    category=cat,
    brand=brand,
    unit=unit,
    purchase_price='10.00',
    sale_price='15.00',
    quantity=10,
    minimum_stock=1,
)
client_obj = ClientModel.objects.create(name='Client1')
supplier = Supplier.objects.create(name='Supplier1')

c = Client()
logged = c.login(username='debugger', password='pass')
print('login', logged)

requests = [
    ('/commerce/purchases/new/', {
        'reference': 'PO123',
        'supplier': str(supplier.pk),
        'tax_rate': '10.00',
        'purchaseline_set-TOTAL_FORMS': '1',
        'purchaseline_set-INITIAL_FORMS': '0',
        'purchaseline_set-MIN_NUM_FORMS': '0',
        'purchaseline_set-MAX_NUM_FORMS': '1000',
        'purchaseline_set-0-product': str(prod.pk),
        'purchaseline_set-0-quantity': '5',
        'purchaseline_set-0-purchase_price': '20.00',
    }),
    ('/commerce/sales/new/', {
        'invoice_number': 'INV123',
        'client': str(client_obj.pk),
        'discount': '0.00',
        'tax_rate': '10.00',
        'saleline_set-TOTAL_FORMS': '1',
        'saleline_set-INITIAL_FORMS': '0',
        'saleline_set-MIN_NUM_FORMS': '0',
        'saleline_set-MAX_NUM_FORMS': '1000',
        'saleline_set-0-product': str(prod.pk),
        'saleline_set-0-quantity': '3',
        'saleline_set-0-unit_price': '15.00',
    }),
]

for url, data in requests:
    resp = c.post(url, data)
    print('URL', url, 'status', resp.status_code)
    print('content start:\n', resp.content.decode('utf-8', 'ignore')[:2000])
    print('--- end ---\n')
