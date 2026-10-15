# LOT 2 — Achats carton / pièce

Date de validation locale : 15 octobre 2026
Branche de travail : `feat/multi-unit-purchases`

## Résultat

Le flux d’achat Web et API accepte désormais l’unité de base et les conditionnements d’achat actifs. Plusieurs lignes du même produit peuvent être saisies avec des unités différentes.

Exemple validé :

- 100 cartons de 24 pièces à 4 000 DZD ;
- 30 pièces à 220 DZD ;
- total HT : 406 600 DZD ;
- entrée de stock canonique : 2 430 pièces.

Le prix d’achat reste le prix négocié pour l’unité saisie. Le prix de référence suggéré par le produit ou le carton n’écrase pas une valeur modifiée manuellement.

## Données et migration

La migration `apps/commerce/migrations/0014_purchaseline_packaging_purchaseline_packaging_factor_and_more.py` ajoute à `PurchaseLine` :

- le conditionnement sélectionné, nullable ;
- le nom historique de l’unité ;
- le facteur historique ;
- la quantité réellement saisie dans cette unité.

`PurchaseLine.quantity` reste la quantité canonique de stock. Les anciennes lignes restent valides avec un facteur 1 et une quantité saisie nullable. Aucun stock, ancien mouvement ou document historique n’est converti.

Deux contraintes garantissent un facteur supérieur ou égal à 1 et une quantité saisie strictement positive lorsqu’elle est renseignée.

## Règles métier

- le facteur est toujours chargé depuis le serveur ;
- un conditionnement doit appartenir au produit, être actif et autorisé à l’achat ;
- un produit historique ou « pièce uniquement » refuse un conditionnement secondaire ;
- un produit « carton uniquement » exige un conditionnement carton ;
- un changement futur du facteur carton ne modifie pas une ancienne ligne ;
- création, modification, suppression et annulation appliquent uniquement le delta canonique exact ;
- une erreur sur une ligne annule tout l’achat et tous ses mouvements ;
- les verrous produits et conditionnements sont conservés dans une transaction atomique.

Le contrôle PostgreSQL a initialement reproduit l’erreur `FOR UPDATE cannot be applied to the nullable side of an outer join` sur la relation nullable `Product.unit`. La requête a été corrigée pour verrouiller uniquement `Product`, puis charger l’unité séparément.

## Interface Web

Chaque ligne d’achat affiche :

- le produit ;
- l’unité d’achat ;
- la quantité saisie ;
- le prix négocié ;
- la quantité ajoutée au stock canonique ;
- le total de ligne.

Le récapitulatif HT, TVA et TTC est recalculé immédiatement. La recherche produit retourne uniquement les unités autorisées par le serveur avec leur facteur et leur prix indicatif.

Les nouveaux libellés sont traduits en français, arabe et anglais. Les catalogues `.mo` ont été recompilés.

## API

Le payload historique reste accepté :

```json
{"product": 123, "quantity": 5, "purchase_price": "220.00"}
```

Le nouveau payload ajoute facultativement `packaging_id` :

```json
{"product": 123, "packaging_id": 45, "quantity": 2, "purchase_price": "4000.00"}
```

La lecture expose de manière additive `packaging_id`, `packaging_name`, `packaging_factor`, `packaging_quantity`, `entered_quantity` et `base_quantity`. `quantity` conserve son sens historique de quantité canonique.

`openapi.yaml` a été régénéré et validé.

## Tests exécutés

- tests ciblés du LOT 2 sur SQLite : **8/8 PASS** ;
- tests achats, recherche produit et API existants : **84/84 PASS** ;
- test navigateur Microsoft Edge/Playwright : **1/1 PASS** ;
- tests ciblés PostgreSQL 15, incluant concurrence : **11/11 PASS** ;
- suite Django complète : **366 tests PASS, 9 ignorés** ;
- `manage.py check` : **PASS** ;
- `makemigrations --check --dry-run` : **PASS** ;
- validation OpenAPI : **PASS** ;
- traductions AR/EN non traduites : **0**.

## Hors périmètre confirmé

- aucune migration exécutée sur Neon ;
- aucun stock historique converti ;
- ventes multi-unités non implémentées dans ce lot ;
- bons de chargement et stock opérateur non modifiés ;
- application Flutter non modifiée ;
- impressions et factures non modifiées ;
- aucun déploiement Render effectué pendant la validation locale.

La prochaine étape du plan est le **LOT 3 — Ventes**, après intégration et validation de ce LOT 2.
