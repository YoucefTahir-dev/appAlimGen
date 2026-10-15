# Lot 4 — Stocks et bons de chargement multi-unités

Date de validation : 2026-10-15

## Résultat

Le stock reste stocké dans une seule unité canonique. Un bon de chargement peut
désormais contenir plusieurs lignes du même produit, par exemple 10 cartons de
24 et 5 pièces. La validation agrège ces lignes avant tout mouvement : un seul
débit dépôt et un seul crédit opérateur sont créés pour 245 unités de base.

Chaque ligne conserve un snapshot du nom du conditionnement, du facteur de
conversion et de la quantité saisie. Une modification ultérieure du catalogue
ne réinterprète donc pas un chargement historique.

## Garanties transactionnelles

- validation et clôture exécutées dans `transaction.atomic` ;
- verrouillage explicite du bon, de ses lignes, des produits et des stocks
  opérateur, sans verrouillage d'une jointure externe nullable ;
- contrôle du stock sur la somme canonique par produit ;
- validation et clôture non rejouables ;
- retour du reliquat exact, réparti de manière déterministe sur les lignes ;
- aucun double mouvement lorsqu'un produit existe sur plusieurs lignes.

## Compatibilité

L'API accepte toujours une ligne historique `{product, quantity}` : la quantité
est alors une quantité de base. `packaging_id` est facultatif. Les réponses ont
été enrichies avec `packaging_name`, `packaging_factor`, `packaging_quantity`,
`entered_quantity` et `base_quantity`. Le contrat OpenAPI est synchronisé.

Les produits `legacy` et `piece_only` restent en unité de base. Un produit
`carton_only` exige un carton actif. Aucune donnée historique ni quantité de
production n'est convertie automatiquement.

## Validation exécutée

- tests Django complets : 384 PASS, 10 SKIPPED ;
- tests ciblés multi-unités/chargements : 24 PASS ;
- tests PostgreSQL ciblés et concurrence : 8 PASS ;
- test navigateur Edge du formulaire mixte carton/pièce : 1 PASS ;
- `manage.py check` : PASS ;
- migrations : aucune migration manquante ;
- OpenAPI : synchronisé et validé ;
- traductions anglaises et arabes : catalogues `.po` et `.mo` mis à jour.

Les tests ignorés sont les tests explicitement réservés à PostgreSQL lorsqu'ils
sont exécutés dans la campagne SQLite. Ils ont été exécutés séparément contre
PostgreSQL local.
