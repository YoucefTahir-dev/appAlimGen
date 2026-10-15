# LOT 3 — Ventes carton / pièce

Date de validation locale : 15 octobre 2026
Branche de travail : `feat/multi-unit-sales`

## Résultat

Les ventes Web et API acceptent désormais explicitement l’unité de base et les conditionnements autorisés à la vente. Plusieurs lignes du même produit peuvent utiliser des unités différentes dans une facture.

Exemple validé :

- 2 cartons de 24 pièces à 4 800 DZD ;
- 5 pièces à 220 DZD ;
- total HT : 10 700 DZD ;
- sortie de stock canonique : 53 pièces.

Le backend reste l’autorité pour le facteur, le tarif suggéré, le total, les remises, la TVA, le paiement et le mouvement de stock.

## Règles métier

- le conditionnement doit appartenir au produit, être actif et autorisé à la vente ;
- un produit historique ou « pièce uniquement » refuse un nouveau conditionnement secondaire ;
- un produit « carton uniquement » exige un conditionnement de type carton ;
- le tarif carton par niveau client est indépendant du tarif pièce, avec conservation du fallback proportionnel historique ;
- le besoin en stock est agrégé en unité de base avant toute écriture ;
- une erreur sur une ligne annule la facture, ses lignes, son paiement et ses mouvements ;
- le stock global ou opérateur est verrouillé et débité avec la quantité canonique ;
- modification, suppression et annulation contrepassent exactement les quantités de base ;
- les snapshots déjà enregistrés ne changent pas lorsque le produit, le prix ou le facteur du conditionnement est modifié ultérieurement.

## Interface Web

Chaque ligne de vente affiche maintenant :

- le produit ;
- l’unité de vente ;
- la quantité saisie ;
- le prix de l’unité choisie ;
- la quantité canonique déduite du stock ;
- le total de ligne.

Le récapitulatif HT, TVA, remise et TTC est recalculé immédiatement. Pour un produit « carton uniquement », l’unité de base n’est pas proposée et le premier carton valide est sélectionné. Les unités inactives ou désactivées à la vente ne sont jamais proposées.

## API

Le payload historique reste accepté pour les produits autorisant l’unité de base :

```json
{"product_id": 123, "quantity": 5}
```

Le payload multi-unités ajoute facultativement `packaging_id` :

```json
{"product_id": 123, "packaging_id": 45, "quantity": 2}
```

La lecture expose de manière additive :

- `packaging_id` ;
- `packaging_name` ;
- `packaging_factor` ;
- `packaging_quantity` et `entered_quantity` ;
- `quantity` et `base_quantity` pour la quantité canonique.

`openapi.yaml` a été régénéré et validé.

## Fichiers principaux modifiés

- `apps/inventory/pricing.py` : disponibilité des unités et matrice tarifaire ;
- `apps/commerce/services.py` : validation, conversion et verrouillage des ventes ;
- `apps/commerce/models.py` : protection des snapshots et des écritures directes ;
- `apps/commerce/forms.py` et `apps/commerce/views.py` : validations Web ;
- `apps/commerce/templates/commerce/sale_form.html` et `static/js/sale-formset.js` : UX et calcul instantané ;
- `apps/api/serializers.py` et `apps/api/views.py` : contrat API additif ;
- `locale/ar` et `locale/en` : traductions compilées ;
- `apps/commerce/tests/test_multi_unit_sales.py` et `browser_tests/sale_unit_form.py` : couverture métier et navigateur.

## Migrations et données historiques

Aucune migration Django n’est nécessaire pour ce lot : les colonnes de vente existaient déjà.

- aucun stock historique n’a été converti ;
- aucune facture historique n’a été recalculée ;
- aucune migration n’a été exécutée sur Neon ;
- aucune donnée de production n’a été modifiée.

## Tests exécutés

- tests ciblés du LOT 3 sur SQLite : **10 PASS, 1 ignoré PostgreSQL** ;
- test navigateur Microsoft Edge/Playwright : **1 PASS** ;
- tests PostgreSQL 15 ciblés et concurrence : **13 PASS** ;
- suite Django complète : **375 PASS, 10 ignorés** ;
- `manage.py check` : **PASS** ;
- `makemigrations --check --dry-run` : **PASS** ;
- validation OpenAPI : **PASS** ;
- traductions AR/EN non traduites ou fuzzy : **0** ;
- `git diff --check` : **PASS**.

## Compatibilité et périmètre

- les anciens clients restent compatibles quand `packaging_id` est absent ;
- les champs API existants ne changent pas de sens ;
- Flutter n’est pas modifié dans ce lot ;
- les bons de chargement et l’affichage général des stocks restent dans le LOT 4 ;
- les PDF, tickets et impressions Android restent dans le LOT 5 ;
- aucun déploiement Render n’est réalisé dans ce lot.

La prochaine étape du plan est le **LOT 4 — stocks et bons de chargement** après intégration de ce lot.
