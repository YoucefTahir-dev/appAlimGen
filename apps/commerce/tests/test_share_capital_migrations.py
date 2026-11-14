from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ShareCapitalMigrationTests(TransactionTestCase):
    """Exercise an upgrade using synthetic rows in the isolated test database."""

    def test_upgrade_keeps_old_capital_unknown_and_existing_labels_unchanged(self):
        before = [
            ('core', '0005_authratelimitbucket'),
            ('commerce', '0017_openingbalancepayment'),
        ]
        after = [
            ('core', '0006_companysettings_share_capital'),
            ('commerce', '0018_sale_company_share_capital_snapshot'),
        ]
        executor = MigrationExecutor(connection)
        original_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            old_apps = executor.loader.project_state(before).apps
            Company = old_apps.get_model('core', 'CompanySettings')
            Client = old_apps.get_model('inventory', 'Client')
            Sale = old_apps.get_model('commerce', 'Sale')
            company, _ = Company.objects.update_or_create(
                pk=1, defaults={'company_name': 'Entreprise avant capital'},
            )
            customer = Client.objects.create(name='Client avant capital')
            certified = Sale.objects.create(
                invoice_number='CAPITAL-MIGRATION-CERTIFIED', client=customer,
                total=Decimal('0.00'), snapshot_version=1,
                snapshot_integrity_status='complete',
                company_name_snapshot='Entreprise historique avant capital',
            )
            legacy = Sale.objects.create(
                invoice_number='CAPITAL-MIGRATION-LEGACY', client=customer,
                total=Decimal('0.00'), snapshot_version=0,
                snapshot_integrity_status='legacy_unknown',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new_apps = executor.loader.project_state(after).apps
            Company = new_apps.get_model('core', 'CompanySettings')
            Sale = new_apps.get_model('commerce', 'Sale')
            self.assertIsNone(Company.objects.get(pk=company.pk).share_capital)
            for pk, version, status in (
                (certified.pk, 1, 'complete'), (legacy.pk, 0, 'legacy_unknown'),
            ):
                with self.subTest(status=status):
                    upgraded = Sale.objects.get(pk=pk)
                    self.assertIsNone(upgraded.company_share_capital_snapshot)
                    self.assertEqual(upgraded.snapshot_version, version)
                    self.assertEqual(upgraded.snapshot_integrity_status, status)
            Company.objects.filter(pk=company.pk).update(share_capital=Decimal('1000000.00'))
            self.assertTrue(all(
                value is None for value in Sale.objects.filter(pk__in=[certified.pk, legacy.pk])
                .values_list('company_share_capital_snapshot', flat=True)
            ))
        finally:
            MigrationExecutor(connection).migrate(original_targets)
