from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.api.serializers import ProductPackagingSerializer, ProductSerializer
from apps.inventory.forms import ProductForm
from apps.inventory.models import Product, ProductPackaging, StockMovement, Unit
from apps.inventory.pricing import get_sale_price


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class MultiUnitProductLotOneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_user(
            username='multi-unit-manager', password='StrongPass123!',
            role=get_user_model().MANAGER,
        )
        cls.seller = get_user_model().objects.create_user(
            username='multi-unit-seller', password='StrongPass123!',
            role=get_user_model().SELLER,
        )

    def setUp(self):
        self.client.force_login(self.manager)

    @staticmethod
    def product_payload(**overrides):
        payload = {
            'name': 'Chocolat test',
            'brand_text': 'Mina',
            'unit_mode': Product.UnitMode.CARTON_AND_PIECE,
            'purchase_price': '160.00',
            'super_wholesale_price': '180.00',
            'wholesale_price': '200.00',
            'retail_price': '220.00',
            'quantity': '100',
            'stock_unit': ProductForm.STOCK_CARTON,
            'minimum_stock': '20',
            'description': '',
            'carton-conversion_factor': '24',
            'carton-reference_purchase_price': '4000.00',
            'carton-super_wholesale_price': '4300.00',
            'carton-wholesale_price': '4500.00',
            'carton-retail_price': '4800.00',
            'carton-barcode': '',
        }
        payload.update(overrides)
        return payload

    def test_existing_and_legacy_api_payloads_remain_unclassified(self):
        historical = Product.objects.create(
            name='Historique', purchase_price='10.00', sale_price='15.00', quantity=100,
        )
        self.assertEqual(historical.unit_mode, Product.UnitMode.LEGACY)
        self.assertEqual(historical.quantity, 100)

        serializer = ProductSerializer(data={
            'name': 'Ancien client API', 'purchase_price': '10.00',
            'sale_price': '15.00', 'quantity': 7, 'minimum_stock': 0,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        api_product = Product(**serializer.validated_data)
        self.assertEqual(api_product.unit_mode, Product.UnitMode.LEGACY)
        self.assertEqual(api_product.quantity, 7)

    def test_piece_only_creation_keeps_entered_stock_without_carton(self):
        response = self.client.post(reverse('product_create'), self.product_payload(
            name='Produit pièce', unit_mode=Product.UnitMode.PIECE_ONLY,
            quantity='30', stock_unit=ProductForm.STOCK_BASE,
        ))
        self.assertRedirects(response, reverse('product_list'))
        product = Product.objects.get(name='Produit pièce')
        self.assertEqual(product.quantity, 30)
        self.assertEqual(product.unit.name, 'Pièce')
        self.assertFalse(product.packagings.filter(kind=ProductPackaging.CARTON).exists())
        self.assertEqual(
            StockMovement.objects.get(product=product).applied_delta, 30,
        )

    def test_carton_and_piece_creation_converts_initial_stock_once(self):
        response = self.client.post(reverse('product_create'), self.product_payload())
        self.assertRedirects(response, reverse('product_list'))
        product = Product.objects.get(name='Chocolat test')
        carton = product.packagings.get(kind=ProductPackaging.CARTON)

        self.assertEqual(product.quantity, 2400)
        self.assertEqual(product.unit.name, 'Pièce')
        self.assertEqual(carton.conversion_factor, 24)
        self.assertEqual(carton.reference_purchase_price, Decimal('4000.00'))
        self.assertEqual(carton.super_wholesale_price, Decimal('4300.00'))
        self.assertEqual(carton.wholesale_price, Decimal('4500.00'))
        self.assertEqual(carton.retail_price, Decimal('4800.00'))
        movements = StockMovement.objects.filter(product=product)
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.get().applied_delta, 2400)

    def test_mixed_initial_stock_is_canonical_and_recorded_once(self):
        response = self.client.post(reverse('product_create'), self.product_payload(
            name='Stock mixte', initial_cartons='40', initial_pieces='15',
            **{'carton-conversion_factor': '30'},
        ))
        self.assertRedirects(response, reverse('product_list'))
        product = Product.objects.get(name='Stock mixte')
        self.assertEqual(product.quantity, 1215)
        movements = StockMovement.objects.filter(product=product)
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.get().applied_delta, 1215)

    def test_carton_only_keeps_extra_pieces_as_internal_stock(self):
        response = self.client.post(reverse('product_create'), self.product_payload(
            name='Carton mixte interne', unit_mode=Product.UnitMode.CARTON_ONLY,
            initial_cartons='2', initial_pieces='5',
        ))
        self.assertRedirects(response, reverse('product_list'))
        product = Product.objects.get(name='Carton mixte interne')
        self.assertEqual(product.quantity, 53)
        self.assertEqual(product.format_stock_quantity(), '2 carton(s) et 5 pièce(s)')

    def test_piece_only_uses_piece_initial_quantity(self):
        response = self.client.post(reverse('product_create'), self.product_payload(
            name='Pièces initiales', unit_mode=Product.UnitMode.PIECE_ONLY,
            initial_cartons='0', initial_pieces='37',
        ))
        self.assertRedirects(response, reverse('product_list'))
        product = Product.objects.get(name='Pièces initiales')
        self.assertEqual(product.quantity, 37)
        self.assertEqual(StockMovement.objects.get(product=product).applied_delta, 37)

    def test_carton_only_accepts_initial_stock_entered_in_pieces(self):
        response = self.client.post(reverse('product_create'), self.product_payload(
            name='Carton vendu entier', unit_mode=Product.UnitMode.CARTON_ONLY,
            quantity='53', stock_unit=ProductForm.STOCK_BASE,
        ))
        self.assertRedirects(response, reverse('product_list'))
        product = Product.objects.get(name='Carton vendu entier')
        self.assertEqual(product.quantity, 53)
        self.assertEqual(StockMovement.objects.get(product=product).applied_delta, 53)

    def test_carton_factor_one_is_rejected_without_creating_product(self):
        response = self.client.post(reverse('product_create'), self.product_payload(
            name='Carton invalide', **{'carton-conversion_factor': '1'},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'plus d’une pièce')
        self.assertFalse(Product.objects.filter(name='Carton invalide').exists())

    def test_carton_prices_are_independent_and_selected_by_customer_type(self):
        self.client.post(reverse('product_create'), self.product_payload())
        product = Product.objects.get(name='Chocolat test')
        carton = product.packagings.get(kind=ProductPackaging.CARTON)
        customer = type('Customer', (), {'customer_type': 'RETAIL'})()

        self.assertEqual(get_sale_price(product, customer, carton), Decimal('4800.00'))
        self.assertNotEqual(
            get_sale_price(product, customer, carton),
            product.retail_price * carton.conversion_factor,
        )

    def test_carton_margin_validation_compares_carton_prices_to_carton_cost(self):
        accepted = self.client.post(reverse('product_create'), self.product_payload(
            name='Marge carton valide',
            **{
                'carton-reference_purchase_price': '4500.00',
                'carton-super_wholesale_price': '4600.00',
                'carton-wholesale_price': '4700.00',
                'carton-retail_price': '4800.00',
            },
        ))
        self.assertRedirects(accepted, reverse('product_list'))

        rejected = self.client.post(reverse('product_create'), self.product_payload(
            name='Marge carton invalide',
            initial_cartons='40', initial_pieces='15',
            **{
                'carton-reference_purchase_price': '4500.00',
                'carton-super_wholesale_price': '4499.99',
                'carton-wholesale_price': '4700.00',
                'carton-retail_price': '4800.00',
            },
        ))
        self.assertEqual(rejected.status_code, 200)
        self.assertIn('super_wholesale_price', rejected.context['carton_form'].errors)
        self.assertEqual(rejected.context['form']['initial_cartons'].value(), '40')
        self.assertEqual(rejected.context['form']['initial_pieces'].value(), '15')
        self.assertFalse(Product.objects.filter(name='Marge carton invalide').exists())

    def test_carton_product_update_never_reapplies_initial_stock(self):
        self.client.post(reverse('product_create'), self.product_payload())
        product = Product.objects.get(name='Chocolat test')

        response = self.client.post(
            reverse('product_update', args=[product.pk]),
            self.product_payload(quantity='101'),
        )

        self.assertRedirects(response, reverse('product_list'))
        product.refresh_from_db()
        self.assertEqual(product.quantity, 2400)
        movements = list(StockMovement.objects.filter(product=product).order_by('pk'))
        self.assertEqual([movement.applied_delta for movement in movements], [2400])
        self.assertEqual(
            product.packagings.filter(kind=ProductPackaging.CARTON).count(), 1,
        )

    def test_factor_change_preserves_custom_prices_and_real_stock(self):
        self.client.post(reverse('product_create'), self.product_payload())
        product = Product.objects.get(name='Chocolat test')
        response = self.client.post(
            reverse('product_update', args=[product.pk]),
            self.product_payload(
                quantity='999', initial_cartons='99', initial_pieces='99',
                **{'carton-conversion_factor': '30'},
            ),
        )
        self.assertRedirects(response, reverse('product_list'))
        product.refresh_from_db()
        carton = product.packagings.get(kind=ProductPackaging.CARTON)
        self.assertEqual(product.quantity, 2400)
        self.assertEqual(carton.conversion_factor, 30)
        self.assertEqual(carton.reference_purchase_price, Decimal('4000.00'))
        self.assertEqual(carton.retail_price, Decimal('4800.00'))
        self.assertEqual(StockMovement.objects.filter(product=product).count(), 1)

    def test_legacy_generic_packaging_can_still_be_created(self):
        payload = self.product_payload(
            name='Produit avec palette', unit_mode=Product.UnitMode.PIECE_ONLY,
            quantity='0', stock_unit=ProductForm.STOCK_BASE,
            **{
                'other_packagings-TOTAL_FORMS': '1',
                'other_packagings-INITIAL_FORMS': '0',
                'other_packagings-MIN_NUM_FORMS': '0',
                'other_packagings-MAX_NUM_FORMS': '1000',
                'other_packagings-0-name': 'Palette',
                'other_packagings-0-conversion_factor': '100',
                'other_packagings-0-default_sale_price': '22000.00',
                'other_packagings-0-barcode': '',
                'other_packagings-0-is_active': 'on',
            },
        )
        response = self.client.post(reverse('product_create'), payload)
        self.assertRedirects(response, reverse('product_list'))
        packaging = Product.objects.get(name='Produit avec palette').packagings.get()
        self.assertEqual(packaging.name, 'Palette')
        self.assertEqual(packaging.kind, ProductPackaging.OTHER)

    def test_legacy_packaging_price_fallback_is_unchanged(self):
        product = Product.objects.create(
            name='Produit historique conditionné', purchase_price='10.00',
            sale_price='20.00', quantity=10,
        )
        packaging = ProductPackaging.objects.create(
            product=product, name='Lot de 6', conversion_factor=6,
            default_sale_price='120.00',
        )
        customer = type('Customer', (), {'customer_type': 'RETAIL'})()
        self.assertEqual(get_sale_price(product, customer, packaging), Decimal('120.00'))

    def test_ambiguous_historical_stock_cannot_be_silently_reclassified(self):
        historical = Product.objects.create(
            name='Stock ambigu', purchase_price='10.00', sale_price='15.00', quantity=100,
        )
        form = ProductForm(data=self.product_payload(
            name=historical.name, quantity='100', stock_unit=ProductForm.STOCK_BASE,
        ), instance=historical)
        self.assertFalse(form.is_valid())
        self.assertIn('unit_mode', form.errors)
        historical.refresh_from_db()
        self.assertEqual(historical.unit_mode, Product.UnitMode.LEGACY)
        self.assertEqual(historical.quantity, 100)

    def test_only_one_carton_configuration_is_allowed(self):
        product = Product.objects.create(
            name='Contrainte carton', purchase_price='10.00', sale_price='20.00', quantity=0,
        )
        ProductPackaging.objects.create(
            product=product, name='Carton', kind=ProductPackaging.CARTON,
            conversion_factor=6, default_sale_price='120.00',
        )
        with self.assertRaises(ValidationError):
            ProductPackaging.objects.create(
                product=product, name='Caisse', kind=ProductPackaging.CARTON,
                conversion_factor=12, default_sale_price='240.00',
            )

    def test_carton_prices_remain_hidden_without_pricing_permission(self):
        product = Product.objects.create(
            name='Tarifs protégés', purchase_price='10.00', sale_price='20.00', quantity=0,
        )
        packaging = ProductPackaging.objects.create(
            product=product, name='Carton', kind=ProductPackaging.CARTON,
            conversion_factor=6, default_sale_price='120.00',
            reference_purchase_price='60.00', super_wholesale_price='90.00',
            wholesale_price='100.00', retail_price='120.00',
        )
        request = type('Request', (), {'user': self.seller})()
        data = ProductPackagingSerializer(packaging, context={'request': request}).data
        for protected_field in (
            'default_sale_price', 'minimum_sale_price', 'reference_purchase_price',
            'super_wholesale_price', 'wholesale_price', 'retail_price',
        ):
            self.assertNotIn(protected_field, data)

    def test_product_editor_contains_dynamic_preview_and_preserves_other_packagings(self):
        piece = Unit.objects.create(name='Pièce')
        product = Product.objects.create(
            name='Produit éditable', unit=piece, unit_mode=Product.UnitMode.PIECE_ONLY,
            purchase_price='10.00', sale_price='20.00', quantity=0,
        )
        ProductPackaging.objects.create(
            product=product, name='Palette', conversion_factor=100,
            default_sale_price='2000.00',
        )
        response = self.client.get(reverse('product_update', args=[product.pk]))
        self.assertContains(response, 'id="product-unit-form"')
        self.assertContains(response, 'id="preview-stock"')
        self.assertContains(response, 'js/product-unit-form.js')
        self.assertContains(response, 'Palette')

        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ar'
        arabic_response = self.client.get(reverse('product_update', args=[product.pk]))
        self.assertContains(arabic_response, 'التعبئة والوحدات')
        self.assertContains(arabic_response, 'معاينة فورية')
