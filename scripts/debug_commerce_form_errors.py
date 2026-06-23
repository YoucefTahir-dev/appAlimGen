import os
import sys
import django

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestio_stock.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.inventory.models import Category, Brand, Unit, Product, Client as ClientModel, Supplier
from apps.commerce.forms import PurchaseForm, PurchaseLineFormSet, SaleForm, SaleLineFormSet

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

purchase_data = {
    'reference': 'PO123',
    'supplier': str(supplier.pk),
    'tax_rate': '10.00',
    'lines-TOTAL_FORMS': '1',
    'lines-INITIAL_FORMS': '0',
    'lines-MIN_NUM_FORMS': '0',
    'lines-MAX_NUM_FORMS': '1000',
    'lines-0-product': str(prod.pk),
    'lines-0-quantity': '5',
    'lines-0-purchase_price': '20.00',
}
sale_data = {
    'invoice_number': 'INV123',
    'client': str(client_obj.pk),
    'discount': '0.00',
    'tax_rate': '10.00',
    'lines-TOTAL_FORMS': '1',
    'lines-INITIAL_FORMS': '0',
    'lines-MIN_NUM_FORMS': '0',
    'lines-MAX_NUM_FORMS': '1000',
    'lines-0-product': str(prod.pk),
    'lines-0-quantity': '3',
    'lines-0-unit_price': '15.00',
}

print('Purchase form validation:')
purchase_form = PurchaseForm(purchase_data)
purchase_formset = PurchaseLineFormSet(purchase_data, prefix='lines')
print('has lines-TOTAL_FORMS', 'lines-TOTAL_FORMS' in purchase_data)
print('has lines-INITIAL_FORMS', 'lines-INITIAL_FORMS' in purchase_data)
print('formset prefix', purchase_formset.prefix)
print('formset management form fields', list(purchase_formset.management_form.fields.keys()))
print('form valid', purchase_form.is_valid())
print('form errors', purchase_form.errors)
print('formset valid', purchase_formset.is_valid())
print('formset errors', purchase_formset.errors)
print('non_form_errors', purchase_formset.non_form_errors())

print('\nSale form validation:')
sale_form = SaleForm(sale_data)
sale_formset = SaleLineFormSet(sale_data, prefix='lines')
print('form valid', sale_form.is_valid())
print('form errors', sale_form.errors)
print('formset valid', sale_formset.is_valid())
print('formset errors', sale_formset.errors)
print('non_form_errors', sale_formset.non_form_errors())
