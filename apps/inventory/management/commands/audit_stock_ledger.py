from django.core.management.base import BaseCommand, CommandError

from apps.inventory.stock_audit import audit_stock_ledger


class Command(BaseCommand):
    help = (
        "Vérifie la continuité du journal de stock et sa concordance avec "
        "les quantités courantes, sans modifier les données."
    )

    def handle(self, *args, **options):
        audit = audit_stock_ledger()
        self.stdout.write(
            "Audit stock : "
            f"{audit.checked_products} produit(s), "
            f"{audit.checked_movements} mouvement(s), "
            f"{audit.legacy_movements} mouvement(s) historique(s), "
            f"{audit.unresolved_legacy_products} produit(s) non rapproché(s)."
        )
        if audit.issues:
            for issue in audit.issues[:100]:
                self.stderr.write(f"- {issue}")
            if len(audit.issues) > 100:
                self.stderr.write(f"- ... {len(audit.issues) - 100} autre(s) anomalie(s).")
            raise CommandError(
                f"Journal de stock non validé : {len(audit.issues)} anomalie(s)."
            )
        self.stdout.write(self.style.SUCCESS("Journal de stock cohérent."))
