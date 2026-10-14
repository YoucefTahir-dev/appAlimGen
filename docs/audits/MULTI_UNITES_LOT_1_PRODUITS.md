# LOT 1 — Produits et tarifs carton / pièce

Date de validation locale : 14 octobre 2026
Branche de travail : `fix/p0-03-historical-invoices`
Périmètre : produit, tarifs, formulaire Web et migration additive uniquement.

## 1. Résultat

Le LOT 1 est implémenté localement. Un nouveau produit peut être configuré en :

- pièce uniquement ;
- carton uniquement ;
- carton et pièce.

Le stock reste unique et canonique. Pour tout nouveau produit classifié, il est enregistré en pièces. Une saisie initiale en cartons est convertie exactement une fois avant l'écriture du mouvement de stock. Les tarifs carton Super Gros, Gros et Détail sont indépendants des tarifs pièce.

Aucun stock historique, mouvement, achat, vente, chargement ou document ancien n'a été converti. Les produits existants reçoivent uniquement le statut sûr `legacy` (« Historique à confirmer »).

## 2. Modèles adaptés

### `Product`

- ajout de `unit_mode` avec les valeurs `legacy`, `piece_only`, `carton_only` et `carton_and_piece` ;
- valeur de migration `legacy`, afin de ne donner aucune nouvelle signification aux données existantes ;
- propriétés de lecture du carton et de décomposition du stock ;
- unité `Pièce` affectée uniquement lors d'une création/classification explicite par le formulaire complet.

### `ProductPackaging`

- ajout d'un type stable `carton` ou `other` ;
- un seul carton autorisé par produit ;
- ajout du prix d'achat carton de référence ;
- ajout des trois prix carton indépendants ;
- ajout des indicateurs d'usage achat/vente ;
- contrôles de non-négativité, coût minimal et ordre Super Gros ≤ Gros ≤ Détail ;
- maintien de `default_sale_price` et du comportement proportionnel historique lorsqu'aucun nouveau tarif n'est défini.

Les conditionnements génériques historiques (palette, lot, etc.) restent visibles et éditables séparément. Un ancien conditionnement nommé exactement « Carton » n'est reclassifié que lorsqu'un responsable active explicitement un mode carton.

## 3. Migration préparée

Fichier : `apps/inventory/migrations/0017_product_multi_unit_pricing.py`

La migration est additive :

- elle ajoute des colonnes avec valeurs nulles ou valeurs par défaut sûres ;
- elle ajoute les contraintes de prix et d'unicité du carton ;
- elle n'inclut aucun `RunPython` ;
- elle ne multiplie aucune quantité ;
- elle ne modifie aucun prix ou mouvement historique.

Elle a été appliquée avec succès sur une base PostgreSQL 15 temporaire et isolée. Elle n'a pas été exécutée sur Neon.

## 4. Interface Web

Les pages Ajouter/Modifier un produit sont organisées en sections :

- informations générales ;
- conditionnement et unités ;
- prix d'achat pièce/carton ;
- matrice des trois tarifs pièce/carton ;
- stock initial ou physique et unité de saisie ;
- autres conditionnements ;
- aperçu dynamique récapitulatif.

Le JavaScript :

- affiche les champs carton seulement pour les modes concernés ;
- affiche `1 carton = X pièces` ;
- affiche la conversion du stock avant enregistrement ;
- calcule un coût pièce indicatif sans modifier le prix pièce saisi ;
- actualise le panneau récapitulatif ;
- utilise les libellés traduits fournis par Django.

Les traductions FR/AR/EN du nouveau formulaire ont été intégrées et les fichiers `.mo` AR/EN recompilés.

## 5. Compatibilité

- les anciennes requêtes API sans `unit_mode` créent/conservent le mode sûr `legacy` ;
- l'absence de prix carton utilise toujours le tarif de base multiplié par le facteur ;
- les nouveaux prix ne sont pas exposés par l'API aux utilisateurs dépourvus de `inventory.view_product_pricing` ;
- le formulaire rapide depuis un achat reste inchangé : il ne demande ni stock ni carton et ne crée aucun mouvement de stock ;
- les anciens conditionnements génériques restent gérables ;
- achats, ventes, chargements, impressions et Flutter ne sont pas modifiés dans ce lot.

## 6. Tests ajoutés

`apps/inventory/tests/test_multi_units_product.py` vérifie notamment :

- nouveau produit pièce uniquement ;
- produit carton uniquement ;
- produit carton et pièce ;
- facteur carton égal à 1 refusé ;
- stock initial en cartons converti une seule fois ;
- stock initial en pièces non converti ;
- modification carton appliquant seulement la différence de stock ;
- trois tarifs carton indépendants ;
- fallback tarifaire historique ;
- unicité du carton ;
- blocage de la reclassification silencieuse d'un stock historique ambigu ;
- compatibilité d'un ancien payload API ;
- maintien des conditionnements génériques ;
- masquage des prix sans permission ;
- rendu du formulaire, aperçu dynamique et traduction arabe.

`browser_tests/product_unit_form.py` ouvre la page dans Microsoft Edge avec Playwright et vérifie réellement le masquage/affichage des champs, le calcul `100 × 24 = 2 400`, le coût indicatif et l'absence d'écrasement du prix pièce.

## 7. Commandes et résultats

- `python manage.py check` : **PASS**.
- `python manage.py makemigrations --check --dry-run` : **PASS**, aucune migration manquante.
- tests ciblés SQLite (`test_multi_units_product`, `test_product`, `test_pricing`) : **PASS**.
- mêmes tests ciblés sur PostgreSQL 15 isolé : **30 tests, PASS**.
- tests de compatibilité formulaire rapide/recherche produit : **14 tests, PASS**.
- test navigateur Edge/Playwright de l'aperçu dynamique : **1 test, PASS**.
- suite Django complète : **358 tests, PASS ; 9 ignorés**.
- `git diff --check` : **PASS** (seul avertissement de normalisation CRLF/LF du catalogue arabe).

Les messages simulant des pannes de stockage média ou des refus HTTP dans certains tests sont attendus et ne représentent pas des échecs.

## 8. Procédure de vérification visuelle

1. Dans **Produits > Ajouter un produit**, vérifier que seuls les trois modes métier sont proposés.
2. Choisir **Carton et pièce** et saisir `24` pièces par carton.
3. Vérifier l'indication immédiate `1 carton = 24 pièces`.
4. Saisir un achat carton de `4 000 DZD` et constater le coût indicatif `166,67 DZD/pièce` sans modification automatique du prix pièce.
5. Saisir des tarifs pièce `180 / 200 / 220` et carton `4 300 / 4 500 / 4 800`.
6. Saisir `100` avec l'unité **Carton** et vérifier l'aperçu `100 × 24 = 2 400 pièces`.
7. Enregistrer et vérifier que le produit possède un solde `2 400` et un seul mouvement initial de `+2 400`.
8. Refaire l'affichage en français, arabe et anglais, puis contrôler le RTL en arabe.
9. Ouvrir un ancien produit non classifié : l'avertissement historique doit être visible et une reclassification ambiguë doit être refusée.

## 9. Risques et décisions nécessaires

Avant toute activation sur des produits existants, le propriétaire métier doit confirmer produit par produit :

- l'unité réelle du stock courant ;
- l'unité des prix actuels ;
- le facteur carton ;
- la stratégie de conversion éventuelle.

En particulier, l'information « Chocolat Mina : 100 signifie 100 cartons » est conservée comme décision métier à traiter dans un futur cutover contrôlé. Ce lot ne transforme pas cette quantité en 2 400 pièces.

Le choix retenu impose qu'un carton contienne au moins deux pièces. L'ordre tarifaire Super Gros ≤ Gros ≤ Détail est imposé séparément aux prix pièce et carton.

## 10. Hors périmètre confirmé

- LOT 2 achats non commencé ;
- ventes, chargements, stock opérateur et impressions non modifiés ;
- application Flutter non modifiée ;
- aucune migration Neon ;
- aucun déploiement Render effectué pendant la validation locale.
