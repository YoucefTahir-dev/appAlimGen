# Améliorations de la fiche produit carton / pièce

Date de validation locale : 16 octobre 2026

## Périmètre

Ce lot améliore uniquement la fiche produit Web existante. Il ne modifie ni les anciens documents commerciaux, ni le contrat de lecture de l'API Android, ni les données de production.

## 1. Conversion des prix

Les quatre couples de prix disposent maintenant d'une conversion ponctuelle dans les deux sens :

- achat ;
- Super Gros ;
- Gros ;
- Détail.

Les calculs sont :

- carton vers pièce : `prix_carton / facteur`, arrondi au centime selon un arrondi arithmétique ;
- pièce vers carton : `prix_piece * facteur`.

L'interface utilise des centimes entiers afin d'éviter les erreurs usuelles de virgule flottante. Une première valeur peut être suggérée lorsque le champ cible est vide. Ensuite, seuls les boutons de conversion remplacent explicitement la cible. Modifier le facteur ne remplace jamais les prix déjà saisis.

Les prix carton et pièce restent indépendants en base. La validation de marge compare :

- un prix de vente pièce au prix d'achat pièce ;
- un prix de vente carton au prix d'achat carton, ou au coût pièce multiplié par le facteur lorsque le prix d'achat carton n'est pas renseigné.

Les valeurs négatives restent interdites par les formulaires, les modèles et les contraintes existantes.

## 2. Stock initial mixte

Lors de la création, le formulaire accepte :

- le nombre de cartons ;
- les pièces supplémentaires.

Le backend calcule l'unique quantité canonique :

`stock_en_pieces = cartons * pieces_par_carton + pieces_supplementaires`

Exemple validé : `40 * 30 + 15 = 1 215 pièces`.

Un seul mouvement `ENTRY` est écrit dans le journal de stock. Il n'existe pas de stock carton parallèle. En mode carton uniquement, le reliquat en pièces demeure comptabilisé dans la quantité interne, mais les règles métier existantes continuent d'interdire sa vente à la pièce.

En modification, les champs de stock initial ne sont plus proposés et toute valeur forgée est ignorée. Le stock réel et le journal restent inchangés. L'écran renvoie vers l'opération d'ajustement de stock distincte.

## 3. Code-barres interne EAN-13

Un nouveau produit Web reçoit une proposition numérique EAN-13 valide. Le préfixe `29` est utilisé comme plage interne ; l'interface précise explicitement qu'il ne s'agit pas d'une attribution GS1.

- le chiffre de contrôle EAN-13 est calculé côté serveur ;
- un bouton permet de demander une autre proposition ;
- la saisie manuelle reste possible, avec validation du format et du chiffre de contrôle ;
- la contrainte `UNIQUE` existante demeure la garantie finale en cas de concurrence ;
- une collision tardive est interceptée proprement et n'écrase aucun produit ;
- un code historique non EAN reste accepté sans modification lors de l'édition ;
- un code existant n'est jamais régénéré à l'ouverture de la fiche.

L'impression utilise désormais le symbole EAN-13 pour les nouveaux codes numériques. Les anciens codes continuent d'être rendus en Code 128. La recherche Web/API reste basée sur la valeur textuelle du code et demeure compatible avec les scanners existants.

## Fichiers modifiés

- `apps/inventory/forms.py`
- `apps/inventory/models.py`
- `apps/inventory/views.py`
- `apps/inventory/urls.py`
- `apps/inventory/templates/inventory/product_form.html`
- `static/js/product-unit-form.js`
- `static/css/styles.css`
- `apps/inventory/tests/test_product.py`
- `apps/inventory/tests/test_multi_units_product.py`
- `apps/inventory/tests/test_inventory.py`
- `browser_tests/product_unit_form.py`

## Couverture de validation

| Scénario demandé | Couverture | Résultat |
|---|---|---|
| Achat carton vers pièce | Navigateur réel | PASS |
| Achat pièce vers carton | Navigateur réel | PASS |
| Trois niveaux de vente | Navigateur réel | PASS |
| Prix personnalisés indépendants | Django + navigateur | PASS |
| Changement du facteur sans écrasement | Django + navigateur | PASS |
| Marges comparées dans la même unité | Django | PASS |
| 40 cartons + 15 pièces = 1 215 | Django + navigateur | PASS |
| Mode carton uniquement | Django | PASS |
| Mode pièce uniquement | Django | PASS |
| Modification sans réinitialisation du stock | Django | PASS |
| Absence de mouvement doublé | Django | PASS |
| EAN-13 et chiffre de contrôle | Django | PASS |
| Unicité du code-barres | Django + contrainte SQL | PASS |
| Code manuel préservé | Django | PASS |
| Compatibilité API Android | Tests API | PASS |
| Achats, ventes et stocks | Tests de non-régression | PASS |

Commandes exécutées :

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.inventory.tests.test_product apps.inventory.tests.test_multi_units_product apps.inventory.tests.test_quick_product apps.api.tests.test_api apps.commerce.tests.test_commerce apps.commerce.tests.test_product_search --keepdb
python manage.py test browser_tests.product_unit_form --keepdb
python manage.py test --keepdb
```

Résultats : suite exhaustive de 407 tests PASS (10 tests conditionnels ignorés) et 1 test navigateur PASS.

## Migration et déploiement

- Migration de schéma : aucune.
- Migration de données historiques : aucune.
- Déploiement : non effectué.
- Commit/push : non effectués, conformément à la consigne.
