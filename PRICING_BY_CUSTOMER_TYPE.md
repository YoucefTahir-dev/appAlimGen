# Tarification par type de client

## Périmètre

Cette évolution centralise les tarifs **Super Gros**, **Gros** et **Détail** pour
le Web et l'API Android. La branche d'intégration est
`feature/android-pricing`. Elle n'est pas fusionnée dans `main` et aucune
migration n'a été exécutée sur Neon production.

## Modèles et anciennes données

- `Product` conserve `sale_price` comme alias technique du prix Détail afin
  de ne pas casser les anciens imports et consommateurs.
- Les champs `super_wholesale_price`, `wholesale_price` et `retail_price` sont
  ajoutés. Chaque valeur existante de `sale_price` est copiée dans les trois
  nouveaux champs.
- `Client.customer_type` est obligatoire et vaut `RETAIL` pour tous les
  anciens clients.
- L'e-mail client reste dans la base et dans les contrôles d'unicité, mais il
  est masqué dans le formulaire métier et dans l'export principal.

La migration `apps/inventory/migrations/0013_customer_type_pricing.py` est
transactionnelle. Elle refuse de continuer si une ancienne ligne possède déjà
un prix de vente inférieur au prix d'achat. Elle ajoute ensuite quatre
contraintes SQL : trois prix supérieurs ou égaux au coût et l'ordre
`Super Gros <= Gros <= Détail`.

## Règle tarifaire centrale

`apps.inventory.pricing.get_sale_price(product, customer, packaging=None)` est
la seule fonction de sélection du tarif. Sans type exploitable, elle retombe
sur Détail. Sur la branche Android, un conditionnement multiplie le tarif de
l'unité de base par son facteur de conversion (option B) ; il ne duplique donc
pas les trois tarifs.

Une modification manuelle reste autorisée sur une vente tant que le prix final
reste supérieur ou égal au coût réel. Le service de vente verrouille les
produits, recalcule le tarif côté serveur et ne fait jamais confiance au calcul
du mobile.

## Web

- Création, modification, liste et fiche produit affichent les nouveaux prix.
- Seuls Administrateur et Gestionnaire voient les trois tarifs.
- Le formulaire de facture interroge un endpoint Django protégé lorsqu'un
  client, un produit ou un conditionnement change.
- Une facture existante n'est jamais recalculée automatiquement.
- La fiche client affiche son type et ses 50 ventes les plus récentes.
- La liste clients accepte un filtre par type et un export Excel sans e-mail.
- L'import/export Produits utilise exactement le même ordre de 13 colonnes.

## API Android

- `GET /api/v1/clients/{id}/` expose `customer_type`.
- Les trois tarifs produit ne sont exposés qu'avec
  `inventory.view_product_pricing`.
- `GET /api/v1/products/{id}/price/?client_id=...&packaging_id=...` retourne
  uniquement le type et le prix applicable.
- Lors d'un `POST /api/v1/sales/`, `unit_price` peut être omis : Django choisit
  alors le tarif. S'il est fourni, Django conserve le prix manuel seulement
  s'il couvre le coût.
- Les factures, tickets et historiques utilisent toujours `SaleLine.unit_price`,
  c'est-à-dire le prix réellement facturé.

## Permissions

La permission `inventory.view_product_pricing` est ajoutée à la matrice et
attribuée aux groupes Administrateur et Gestionnaire par migration. Un Vendeur
peut obtenir le prix applicable à un client pendant la vente, mais ne reçoit
jamais les trois tarifs dans la ressource Produit.

## Procédure de validation staging

1. Sauvegarder la base de staging.
2. Rechercher les anciennes incohérences :

   ```sql
   SELECT id, reference, purchase_price, sale_price
   FROM inventory_product
   WHERE sale_price < purchase_price;
   ```

3. Exécuter `python manage.py migrate --plan`, puis `python manage.py migrate`.
4. Exécuter `python manage.py check --deploy` et toute la suite de tests avec
   PostgreSQL.
5. Tester une vente de chaque type, avec et sans prix manuel, puis vérifier le
   stock, la facture PDF et le ticket.
6. Ne migrer Neon production qu'après sauvegarde et validation explicite.

## Validation

- Suite complète : **206 tests réussis**, 1 test de concurrence PostgreSQL
  ignoré comme prévu sous SQLite.
- Démarrage Django et route `/healthz/` : validés (`HTTP 200`).
- Schéma OpenAPI : généré et validé.
- Dérive de migration : aucune.
- Validation PostgreSQL 16 CI : réussie (migrations, paramètres production,
  statiques, compilation, OpenAPI, 206 tests et audit des dépendances).
- Exécution : https://github.com/YoucefTahir-dev/appAlimGen/actions/runs/33780898345
- Neon production : non modifiée.

🟡 **VALIDÉE AVEC RÉSERVES** — la recette fonctionnelle sur Neon staging
reste requise avant toute fusion dans `main`.
