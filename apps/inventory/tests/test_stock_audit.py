from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.inventory.models import Product, StockMovement
from apps.inventory.services import record_stock_movement
from apps.inventory.stock_audit import audit_stock_ledger


class StockLedgerAuditTests(TestCase):
    def create_product(self, *, reference, quantity=0):
        return Product.objects.create(
            reference=reference,
            name=reference,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=quantity,
            minimum_stock=0,
        )

    def test_consistent_ledger_passes(self):
        product = self.create_product(reference='AUDIT-CLEAN')
        record_stock_movement(
            product=product,
            movement_type=StockMovement.ENTRY,
            quantity=5,
        )

        audit = audit_stock_ledger()

        self.assertTrue(audit.is_consistent)
        self.assertEqual(audit.checked_products, 1)
        self.assertEqual(audit.checked_movements, 1)
        call_command('audit_stock_ledger', verbosity=0)

    def test_untracked_nonzero_stock_fails_closed(self):
        self.create_product(reference='AUDIT-UNTRACKED', quantity=5)

        audit = audit_stock_ledger()

        self.assertFalse(audit.is_consistent)
        self.assertTrue(any('sans mouvement rapproché' in issue for issue in audit.issues))
        with self.assertRaises(CommandError):
            call_command('audit_stock_ledger', verbosity=0)

    def test_corrupted_snapshot_is_detected(self):
        product = self.create_product(reference='AUDIT-CORRUPT')
        movement = record_stock_movement(
            product=product,
            movement_type=StockMovement.ENTRY,
            quantity=3,
        )
        table = StockMovement._meta.db_table
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE {table} SET balance_after = %s WHERE id = %s',
                [9, movement.pk],
            )

        audit = audit_stock_ledger()

        self.assertFalse(audit.is_consistent)
        self.assertTrue(any('variation incompatible' in issue for issue in audit.issues))

    def test_baseline_resolves_legacy_history_without_rewriting_it(self):
        product = self.create_product(reference='AUDIT-LEGACY', quantity=5)
        table = StockMovement._meta.db_table
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'''INSERT INTO {table}
                    (product_id, movement_type, quantity, reason, applied_delta,
                     balance_before, balance_after, source_type, source_reference,
                     created_by_id, reversal_of_id, created_at)
                    VALUES (%s, %s, %s, %s, NULL, NULL, NULL, %s, %s, NULL, NULL, CURRENT_TIMESTAMP)''',
                [product.pk, StockMovement.ENTRY, 5, 'Ancien mouvement', 'legacy', ''],
            )
        record_stock_movement(
            product=product,
            movement_type=StockMovement.ADJUSTMENT,
            quantity=5,
            source_type=StockMovement.SOURCE_LEGACY,
            source_reference='baseline:test',
        )

        audit = audit_stock_ledger()

        self.assertTrue(audit.is_consistent)
        self.assertEqual(audit.legacy_movements, 1)
        self.assertEqual(audit.unresolved_legacy_products, 0)

    @property
    def connection(self):
        from django.db import connection

        return connection
