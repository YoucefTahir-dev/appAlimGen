# Lot 6 — Migration contrôlée et déploiement

## État de livraison

Les migrations additives, le backend Web/API rétrocompatible et l'application Android compatible sont livrés. Les produits historiques restent en mode `legacy` tant que leur unité réelle n'est pas confirmée. Aucune quantité, aucun prix, aucun mouvement et aucune facture historique n'a été réinterprété.

## Export de validation métier

La commande suivante produit un inventaire en lecture seule :

```bash
python manage.py export_product_unit_audit --output product-unit-audit.csv
```

Elle inclut l'unité et le mode actuels, les stocks dépôt/opérateurs/chargements ouverts, les tarifs, les conditionnements et les nombres de lignes et mouvements historiques. Les colonnes `classification`, `confirmed_carton_factor` et `validation_notes` sont volontairement vides. Le fichier affiche un SHA-256 afin que la version validée puisse être identifiée sans ambiguïté.

Valeurs métier à renseigner dans `classification` :

- `BASE_ALREADY_PIECE` : le stock actuel est déjà exprimé en pièces ; aucune multiplication du stock ;
- `BASE_OTHER_PRESERVED` : l'unité actuelle reste l'unité de base ; seuls des multiples compatibles pourront être ajoutés ;
- `BASE_CARTON_REQUIRES_CUTOVER` : conversion métier séparée obligatoire ; aucune activation directe ;
- `UNKNOWN` : produit bloqué, aucune activation multi-unités.

## Procédure de production

1. Sauvegarder Neon et vérifier une restauration sur un environnement isolé.
2. Exécuter l'export depuis un environnement autorisé, jamais pendant le build Render.
3. Faire valider chaque ligne par le responsable métier, y compris l'unité des quatre tarifs et le facteur carton.
4. Conserver le CSV approuvé et son SHA-256.
5. N'activer d'abord que des produits `BASE_ALREADY_PIECE`, par petit lot.
6. Vérifier stock global, stock opérateur, chargements ouverts, journal, achats, ventes et factures après chaque lot.
7. Laisser `UNKNOWN` et `BASE_CARTON_REQUIRES_CUTOVER` en mode `legacy`.

## Garde-fous

- La commande d'audit ne possède aucune option d'écriture.
- Aucun import automatique de classification ou de conversion n'est fourni avant validation métier du fichier réel.
- Aucun traitement long ou reprise de données n'est ajouté au build Render.
- Les migrations de schéma restent additives et les requêtes historiques sans `packaging_id` gardent le facteur 1.
- Un produit historiquement stocké en cartons exige une procédure de cutover dédiée et auditable ; il ne sera jamais multiplié silencieusement.

## Rollback

Avant toute activation produit, le binaire précédent peut être remis en service tout en conservant les colonnes additives. Après création de documents multi-unités, désactiver les nouvelles écritures, conserver la lecture compatible et corriger uniquement par opérations compensatoires traçables. Ne jamais supprimer ni réécrire les documents historiques.
