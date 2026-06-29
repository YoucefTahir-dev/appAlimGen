import os

import django

django.setup()

from apps.accounts.models import User
from apps.core.models import CompanySettings
from apps.inventory.models import Brand, Category, Client, Product, Supplier, Unit


if __name__ == '__main__':
    CompanySettings.objects.update_or_create(
        company_name='El Amine lil Mawad El Ghidhaiya wa Ghayr El Ghidhaiya',
        defaults={
            'address': 'Rue des Agriculteurs, Alger, Algérie',
            'phone': '+213 21 123 456',
            'email': 'contact@elamine.dz',
            'rc_number': 'RC123456',
            'tax_number': 'NIF12345678',
            'tax_rate': 19.00,
        }
    )

    admin, _ = User.objects.get_or_create(
        username='admin',
        defaults={
            'email': 'admin@elamine.dz',
            'role': User.ADMIN,
            'is_superuser': True,
            'is_staff': True,
        },
    )
    if admin.pk and not admin.has_usable_password():
        demo_password = os.getenv('DEMO_ADMIN_PASSWORD')
        if demo_password:
            admin.set_password(demo_password)
            admin.save(update_fields=['password'])
        else:
            print('Admin démo créé sans mot de passe utilisable. Définissez DEMO_ADMIN_PASSWORD si nécessaire.')

    c1, _ = Category.objects.get_or_create(name='Épicerie')
    b1, _ = Brand.objects.get_or_create(name='El Amine')
    u1, _ = Unit.objects.get_or_create(name='Unité')
    Product.objects.get_or_create(reference='PRD001', defaults={
        'barcode': '1234567890123',
        'name': 'Farine 1kg',
        'category': c1,
        'brand': b1,
        'unit': u1,
        'purchase_price': 120.00,
        'sale_price': 150.00,
        'quantity': 40,
        'minimum_stock': 10,
    })
    Client.objects.get_or_create(name='Client Démo', defaults={'phone': '+213 660 000 000', 'wilaya': 'Alger', 'tax_number': 'NIF0001'})
    Supplier.objects.get_or_create(name='Fournisseur Démo', defaults={'phone': '+213 770 000 000', 'wilaya': 'Alger', 'rc_number': 'RC0001', 'tax_number': 'NIF0002'})
    print('Données de démonstration chargées.')
