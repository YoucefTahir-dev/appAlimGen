import csv
import hashlib
import io
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inventory.historical_stock import (
    apply_historical_stock_conversion,
    build_conversion_plans,
)


FIELDS = (
    'product_id', 'reference', 'name', 'factor', 'factor_source',
    'conditioning_status', 'depot_cartons_before', 'depot_pieces_after',
    'minimum_cartons_before', 'minimum_pieces_after',
    'operator_stocks', 'total_cartons_before', 'total_pieces_after',
    'eligible', 'already_converted', 'blockers',
)
CONFIRMATION = 'APPLY-HISTORICAL-STOCK-CONVERSION'


def _rows(plans):
    rows = []
    for plan in plans:
        operator_stocks = [
            {
                'operator_id': location.operator_id,
                'operator': location.operator_name,
                'cartons_before': location.cartons_before,
                'pieces_after': location.pieces_after,
            }
            for location in plan.operator_locations
        ]
        rows.append({
            'product_id': plan.product_id,
            'reference': plan.reference,
            'name': plan.name,
            'factor': plan.factor,
            'factor_source': plan.factor_source,
            'conditioning_status': plan.conditioning_status,
            'depot_cartons_before': plan.depot_cartons_before,
            'depot_pieces_after': plan.depot_pieces_after,
            'minimum_cartons_before': plan.minimum_cartons_before,
            'minimum_pieces_after': plan.minimum_pieces_after,
            'operator_stocks': operator_stocks,
            'total_cartons_before': plan.depot_cartons_before + sum(
                item.cartons_before for item in plan.operator_locations
            ),
            'total_pieces_after': plan.depot_pieces_after + sum(
                item.pieces_after for item in plan.operator_locations
            ),
            'eligible': plan.eligible,
            'already_converted': plan.already_converted,
            'blockers': list(plan.blockers),
        })
    return rows


def _serialize(rows, output_format):
    if output_format == 'json':
        return json.dumps(rows, ensure_ascii=False, indent=2) + '\n'
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for row in rows:
        rendered = row.copy()
        rendered['operator_stocks'] = json.dumps(row['operator_stocks'], ensure_ascii=False)
        rendered['blockers'] = ','.join(row['blockers'])
        writer.writerow(rendered)
    return stream.getvalue()


class Command(BaseCommand):
    help = (
        'Diagnostique et simule la conversion des stocks historiques cartons vers pièces. '
        'La commande est strictement en lecture seule sans --apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--default-factor', type=int, default=30)
        parser.add_argument('--format', choices=('csv', 'json'), default='csv')
        parser.add_argument('--output', help='Fichier de rapport de simulation.')
        parser.add_argument('--apply', action='store_true', help='Appliquer les lignes éligibles.')
        parser.add_argument('--confirm', default='', help='Phrase de confirmation forte.')
        parser.add_argument('--backup-confirmed', action='store_true')
        parser.add_argument('--backup-reference', default='')

    def handle(self, *args, **options):
        plans = build_conversion_plans(default_factor=options['default_factor'])
        rows = _rows(plans)
        content = _serialize(rows, options['format'])
        checksum = hashlib.sha256(content.encode('utf-8')).hexdigest()
        output = options.get('output')
        if output:
            path = Path(output).expanduser().resolve()
            if not path.parent.exists():
                raise CommandError(f'Le dossier de destination n’existe pas : {path.parent}')
            path.write_text(content, encoding='utf-8', newline='')
            self.stdout.write(f'Rapport de simulation : {path}')
        else:
            self.stdout.write(content, ending='')

        provisional = sum(row['factor_source'] == 'default' for row in rows)
        confirmed = sum(row['factor_source'] == 'confirmed' for row in rows)
        eligible = sum(row['eligible'] for row in rows)
        blocked = sum(bool(row['blockers']) for row in rows)
        self.stderr.write(
            f'Simulation uniquement : produits={len(rows)}, provisoires={provisional}, '
            f'confirmés={confirmed}, éligibles={eligible}, bloqués={blocked}, SHA-256={checksum}'
        )
        if not options['apply']:
            return

        if not getattr(settings, 'ALLOW_HISTORICAL_STOCK_CONVERSION', False):
            raise CommandError(
                'Application interdite : ALLOW_HISTORICAL_STOCK_CONVERSION n’est pas activé.'
            )
        if options['confirm'] != CONFIRMATION:
            raise CommandError(f'Confirmation invalide. Valeur attendue : {CONFIRMATION}')
        if not options['backup_confirmed']:
            raise CommandError('Une sauvegarde vérifiée doit être confirmée avec --backup-confirmed.')
        if not options['backup_reference'].strip():
            raise CommandError('La référence de la sauvegarde vérifiée est obligatoire.')
        if blocked:
            raise CommandError('Application interrompue : au moins un produit possède un chargement ouvert.')

        applied = 0
        skipped = 0
        with transaction.atomic():
            for plan in plans:
                _conversion, created = apply_historical_stock_conversion(
                    product=plan.product_id,
                    default_factor=options['default_factor'],
                    backup_reference=options['backup_reference'],
                    simulation_checksum=checksum,
                )
                applied += int(created)
                skipped += int(not created)
        self.stdout.write(self.style.SUCCESS(
            f'Conversion terminée : {applied} appliquée(s), {skipped} déjà appliquée(s).'
        ))
