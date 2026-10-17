import csv
import io
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.commerce.models import Sale, SaleLine
from apps.inventory.historical_stock import (
    apply_historical_stock_conversion,
    build_conversion_plans,
    correct_conditioning,
    has_post_conversion_movements,
)
from apps.inventory.forms import CartonConfigurationForm, HistoricalStockConversionForm
from apps.inventory.models import (
    ConditioningCorrection,
    HistoricalStockConversion,
    LoadingOrder,
    LoadingOrderLine,
    OperatorStock,
    Product,
    ProductPackaging,
    StockMovement,
)
from apps.inventory.services import record_stock_movement
from apps.inventory.models import Client


STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(STORAGES=STORAGES)
class HistoricalStockConversionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(
            username='conversion-manager', password='StrongPass123!', role=User.MANAGER,
        )
        cls.seller = User.objects.create_user(
            username='conversion-seller', password='StrongPass123!', role=User.SELLER,
        )

    def legacy_product(self, *, name='Historique', quantity=100, minimum_stock=2):
        return Product.objects.create(
            name=name,
            purchase_price='10.00', sale_price='20.00',
            super_wholesale_price='20.00', wholesale_price='20.00', retail_price='20.00',
            quantity=quantity, minimum_stock=minimum_stock,
        )

    def test_simulation_uses_default_30_and_is_read_only(self):
        product = self.legacy_product()
        OperatorStock.objects.create(operator=self.seller, product=product, quantity=2)

        plan = build_conversion_plans()[0]

        self.assertEqual(plan.factor, 30)
        self.assertEqual(plan.factor_source, 'default')
        self.assertEqual(plan.depot_pieces_after, 3000)
        self.assertEqual(plan.minimum_pieces_after, 60)
        self.assertEqual(plan.operator_locations[0].pieces_after, 60)
        product.refresh_from_db()
        self.assertEqual(product.quantity, 100)
        self.assertFalse(HistoricalStockConversion.objects.exists())

    def test_conversion_form_does_not_prefill_unverified_factor(self):
        form = HistoricalStockConversionForm(product=self.legacy_product())
        self.assertIsNone(form.fields['factor'].initial)

    def test_conversion_preview_does_not_display_provisional_default_factor(self):
        product = self.legacy_product()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('product_conditioning_convert', args=[product.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['factor'], None)
        self.assertEqual(response.context['depot_after'], '—')
        self.assertEqual(response.context['minimum_after'], '—')

    def test_existing_carton_factor_is_confirmed(self):
        product = self.legacy_product()
        ProductPackaging.objects.create(
            product=product, name='Carton', kind=ProductPackaging.CARTON,
            conversion_factor=24, default_sale_price='480.00',
        )

        conversion, created = apply_historical_stock_conversion(
            product=product, user=self.manager,
        )

        self.assertTrue(created)
        self.assertEqual(conversion.factor, 24)
        product.refresh_from_db()
        self.assertEqual(product.quantity, 2400)
        self.assertEqual(product.conditioning_status, Product.ConditioningStatus.CONFIRMED)

    def test_default_conversion_updates_depot_and_operator_once(self):
        product = self.legacy_product()
        operator_stock = OperatorStock.objects.create(
            operator=self.seller, product=product, quantity=4,
        )

        conversion, created = apply_historical_stock_conversion(
            product=product, user=self.manager,
        )
        same_conversion, created_again = apply_historical_stock_conversion(
            product=product, user=self.manager,
        )

        product.refresh_from_db()
        operator_stock.refresh_from_db()
        carton = product.packagings.get(kind=ProductPackaging.CARTON)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(same_conversion.pk, conversion.pk)
        self.assertEqual(product.quantity, 3000)
        self.assertEqual(product.minimum_stock, 60)
        self.assertEqual(operator_stock.quantity, 120)
        self.assertEqual(product.conditioning_status, Product.ConditioningStatus.PROVISIONAL)
        self.assertFalse(carton.is_active)
        self.assertFalse(carton.sale_enabled)
        self.assertEqual(HistoricalStockConversion.objects.filter(product=product).count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(
                product=product, source_type=StockMovement.SOURCE_CONVERSION,
            ).count(),
            1,
        )

    def test_open_loading_order_blocks_conversion(self):
        product = self.legacy_product()
        order = LoadingOrder.objects.create(
            number='CHG-BLOCK', operator=self.seller, created_by=self.manager,
        )
        LoadingOrderLine.objects.create(loading_order=order, product=product, quantity=1)

        plan = build_conversion_plans()[0]
        self.assertIn('open_loading_order', plan.blockers)
        with self.assertRaises(ValidationError):
            apply_historical_stock_conversion(product=product, user=self.manager)
        product.refresh_from_db()
        self.assertEqual(product.quantity, 100)

    def test_correction_30_to_24_without_movement_recalculates_original_stock(self):
        product = self.legacy_product()
        operator_stock = OperatorStock.objects.create(
            operator=self.seller, product=product, quantity=2,
        )
        apply_historical_stock_conversion(product=product, user=self.manager)

        correction = correct_conditioning(
            product=product, new_factor=24, reason='Carton contrôlé', user=self.manager,
        )

        product.refresh_from_db()
        operator_stock.refresh_from_db()
        self.assertEqual(product.quantity, 2400)
        self.assertEqual(product.minimum_stock, 48)
        self.assertEqual(operator_stock.quantity, 48)
        self.assertEqual(correction.depot_difference, -600)
        self.assertEqual(correction.minimum_stock_before, 60)
        self.assertEqual(correction.minimum_stock_after, 48)
        self.assertTrue(correction.automatic_recalculation)
        self.assertEqual(product.carton_packaging.conversion_factor, 24)
        self.assertEqual(product.conditioning_status, Product.ConditioningStatus.CORRECTED)

    def test_movement_after_conversion_requires_complete_physical_inventory(self):
        product = self.legacy_product()
        operator_stock = OperatorStock.objects.create(
            operator=self.seller, product=product, quantity=2,
        )
        conversion, _created = apply_historical_stock_conversion(
            product=product, user=self.manager,
        )
        record_stock_movement(
            product=product, movement_type=StockMovement.EXIT, quantity=10,
            reason='Vente en pièces', source_type=StockMovement.SOURCE_SALE,
        )
        self.assertTrue(has_post_conversion_movements(conversion))

        with self.assertRaises(ValidationError):
            correct_conditioning(
                product=product, new_factor=24, reason='Comptage', user=self.manager,
            )
        correction = correct_conditioning(
            product=product, new_factor=24, reason='Inventaire physique', user=self.manager,
            physical_depot_quantity=2388,
            physical_operator_quantities={self.seller.pk: 47},
        )
        product.refresh_from_db()
        operator_stock.refresh_from_db()
        self.assertEqual(product.quantity, 2388)
        self.assertEqual(operator_stock.quantity, 47)
        self.assertFalse(correction.automatic_recalculation)
        conversion.refresh_from_db()
        self.assertTrue(conversion.physical_inventory_required)
        with self.assertRaises(ValidationError):
            correct_conditioning(
                product=product, new_factor=20, reason='Nouvelle correction', user=self.manager,
            )

    def test_purchase_carton_sale_and_return_movements_all_disable_naive_recalculation(self):
        scenarios = (
            (StockMovement.ENTRY, StockMovement.SOURCE_PURCHASE, 'Achat'),
            (StockMovement.EXIT, StockMovement.SOURCE_SALE, 'Vente carton'),
            (StockMovement.ENTRY, StockMovement.SOURCE_LOADING, 'Retour'),
        )
        for index, (movement_type, source_type, label) in enumerate(scenarios, start=1):
            product = self.legacy_product(name=f'Historique {index}', quantity=100)
            conversion, _created = apply_historical_stock_conversion(
                product=product, user=self.manager,
            )
            record_stock_movement(
                product=product, movement_type=movement_type, quantity=1,
                reason=label, source_type=source_type,
            )
            self.assertTrue(has_post_conversion_movements(conversion), label)

    def test_invoice_packaging_snapshot_is_preserved_by_correction(self):
        product = self.legacy_product(quantity=100)
        apply_historical_stock_conversion(product=product, user=self.manager)
        carton = product.carton_packaging
        ProductPackaging.objects.filter(pk=carton.pk).update(
            is_active=True, sale_enabled=True, purchase_enabled=True,
            default_sale_price='600.00', retail_price='600.00',
        )
        carton.refresh_from_db()
        customer = Client.objects.create(name='Client historique')
        sale = Sale.objects.create(invoice_number='FAC-HIST-COND', client=customer, total='600.00')
        line = SaleLine(
            sale=sale, product=product, packaging=carton,
            quantity=30, packaging_quantity=1, unit_price='600.00',
        )
        line._stock_user = self.manager
        line.save()

        correct_conditioning(
            product=product, new_factor=24, reason='Facteur réel', user=self.manager,
            physical_depot_quantity=product.quantity,
            physical_operator_quantities={},
        )

        line.refresh_from_db()
        self.assertEqual(line.packaging_factor, 30)
        self.assertEqual(line.packaging_quantity, 1)
        self.assertEqual(line.quantity, 30)

    def test_correction_history_is_immutable_and_permission_is_enforced(self):
        product = self.legacy_product()
        apply_historical_stock_conversion(product=product, user=self.manager)
        correction = correct_conditioning(
            product=product, new_factor=24, reason='Vérification', user=self.manager,
        )
        correction.reason = 'Altération'
        with self.assertRaises(ValidationError):
            correction.save()

        self.client.force_login(self.seller)
        denied = self.client.get(reverse('product_conditioning_correct', args=[product.pk]))
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.manager)
        allowed = self.client.get(reverse('product_conditioning_correct', args=[product.pk]))
        self.assertEqual(allowed.status_code, 200)

    def test_web_confirmation_applies_audited_correction(self):
        product = self.legacy_product()
        apply_historical_stock_conversion(product=product, user=self.manager)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('product_conditioning_correct', args=[product.pk]),
            {'new_factor': '24', 'reason': 'Comptage fournisseur', 'confirm': 'on'},
        )
        self.assertRedirects(response, reverse('product_detail', args=[product.pk]))
        product.refresh_from_db()
        self.assertEqual(product.quantity, 2400)
        self.assertEqual(product.conditioning_corrections.count(), 1)

    def test_web_conversion_uses_manager_confirmed_factor_and_backup(self):
        product = self.legacy_product(quantity=10, minimum_stock=2)
        OperatorStock.objects.create(operator=self.seller, product=product, quantity=3)
        self.client.force_login(self.manager)

        preview = self.client.get(reverse('product_conditioning_convert', args=[product.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'Confirmer et convertir le stock historique')

        response = self.client.post(
            reverse('product_conditioning_convert', args=[product.pk]),
            {
                'factor': '24',
                'backup_reference': 'neon-snapshot-2026-10-17',
                'confirm': 'on',
            },
        )
        self.assertRedirects(response, reverse('product_update', args=[product.pk]))
        product.refresh_from_db()
        operator_stock = OperatorStock.objects.get(operator=self.seller, product=product)
        conversion = product.historical_stock_conversion
        self.assertEqual(product.quantity, 240)
        self.assertEqual(product.minimum_stock, 48)
        self.assertEqual(operator_stock.quantity, 72)
        self.assertEqual(product.unit_mode, Product.UnitMode.CARTON_AND_PIECE)
        self.assertEqual(product.conditioning_status, Product.ConditioningStatus.CONFIRMED)
        self.assertEqual(conversion.factor, 24)
        self.assertFalse(conversion.used_default_factor)
        self.assertEqual(conversion.backup_reference, 'neon-snapshot-2026-10-17')
        self.assertEqual(len(conversion.simulation_checksum), 64)

    def test_web_conversion_is_blocked_by_open_loading_order(self):
        product = self.legacy_product(quantity=10)
        order = LoadingOrder.objects.create(
            number='CHG-WEB-BLOCK', operator=self.seller, created_by=self.manager,
        )
        LoadingOrderLine.objects.create(loading_order=order, product=product, quantity=1)
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse('product_conditioning_convert', args=[product.pk]),
            {
                'factor': '24',
                'backup_reference': 'neon-snapshot-2026-10-17',
                'confirm': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fermez ou annulez les bons de chargement ouverts')
        product.refresh_from_db()
        self.assertEqual(product.quantity, 10)
        self.assertFalse(HistoricalStockConversion.objects.filter(product=product).exists())

    def test_regular_product_editor_can_change_factor_without_stock_regularization(self):
        product = self.legacy_product()
        apply_historical_stock_conversion(product=product, user=self.manager)
        carton = ProductPackaging.objects.get(product=product, kind=ProductPackaging.CARTON)
        form = CartonConfigurationForm(
            data={
                'conversion_factor': '24',
                'reference_purchase_price': '240.00',
                'super_wholesale_price': '260.00',
                'wholesale_price': '280.00',
                'retail_price': '300.00',
                'barcode': '',
            },
            instance=carton,
            required=True,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        product.refresh_from_db()
        carton.refresh_from_db()
        self.assertEqual(carton.conversion_factor, 24)
        self.assertEqual(product.quantity, 3000)

    def test_command_is_read_only_by_default_and_application_is_guarded(self):
        product = self.legacy_product()
        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            'prepare_historical_stock_conversion', stdout=stdout, stderr=stderr,
        )
        rows = list(csv.DictReader(io.StringIO(stdout.getvalue())))
        self.assertEqual(rows[0]['depot_pieces_after'], '3000')
        product.refresh_from_db()
        self.assertEqual(product.quantity, 100)
        self.assertFalse(HistoricalStockConversion.objects.exists())
        with self.assertRaisesMessage(CommandError, 'ALLOW_HISTORICAL_STOCK_CONVERSION'):
            call_command(
                'prepare_historical_stock_conversion', apply=True,
                confirm='APPLY-HISTORICAL-STOCK-CONVERSION', backup_confirmed=True,
                backup_reference='neon-snapshot-test',
                stdout=io.StringIO(), stderr=io.StringIO(),
            )
        with self.settings(ALLOW_HISTORICAL_STOCK_CONVERSION=True):
            call_command(
                'prepare_historical_stock_conversion', apply=True,
                confirm='APPLY-HISTORICAL-STOCK-CONVERSION', backup_confirmed=True,
                backup_reference='neon-snapshot-test',
                stdout=io.StringIO(), stderr=io.StringIO(),
            )
        product.refresh_from_db()
        conversion = product.historical_stock_conversion
        self.assertEqual(product.quantity, 3000)
        self.assertEqual(conversion.backup_reference, 'neon-snapshot-test')
        self.assertEqual(len(conversion.simulation_checksum), 64)


@override_settings(STORAGES=STORAGES)
class PostgreSQLHistoricalConversionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Concurrence de conversion validée uniquement avec PostgreSQL.')
        self.product = Product.objects.create(
            name='Conversion concurrente', purchase_price='10.00', sale_price='20.00',
            super_wholesale_price='20.00', wholesale_price='20.00', retail_price='20.00',
            quantity=100, minimum_stock=0,
        )

    def _convert(self):
        close_old_connections()
        try:
            _conversion, created = apply_historical_stock_conversion(product=self.product.pk)
            return created
        finally:
            close_old_connections()

    def test_conversion_is_idempotent_under_concurrency(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._convert(), range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 3000)
        self.assertEqual(HistoricalStockConversion.objects.count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(source_type=StockMovement.SOURCE_CONVERSION).count(), 1,
        )
