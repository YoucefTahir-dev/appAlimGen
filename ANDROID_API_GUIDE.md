# Guide API Android — El Amine ERP

Android communique uniquement avec Django via HTTPS. Il ne reçoit jamais `DATABASE_URL`, les secrets GCP/Render ni un accès direct à PostgreSQL.

## Authentification

Base : `/api/v1/`. Envoyer `Authorization: Bearer <access>` et `Accept-Language: fr|ar|en`.

| Action | Route |
|---|---|
| Connexion | `POST auth/login/` |
| Rotation | `POST auth/refresh/` |
| Déconnexion | `POST auth/logout/` |
| Profil et permissions | `GET auth/me/` |
| Mot de passe | `POST auth/password/change/` |

Les tokens sont stockés dans le Keystore/EncryptedSharedPreferences. Après un refresh, remplacer atomiquement access et refresh. Après changement/reset du mot de passe, tous les anciens jetons renvoient `TOKEN_REVOKED` : les supprimer et reconnecter l’utilisateur.

## Réponses et erreurs

Succès : `{"success": true, "data": ...}`. Erreur : `{"success": false, "error": {"code": "...", "message": "...", "details": ...}}`. Les codes sont stables et non traduits. Les dates utilisent ISO 8601 ; les Decimal sont des chaînes.

Les listes paginées placent `count`, `next`, `previous`, `results` dans `data`. `page_size` est plafonné à 100.

## Idempotence obligatoire côté client

Android doit envoyer `Idempotency-Key: <UUID>` pour ventes, achats, paiements, création/validation/clôture de chargement. Pour un retry, conserver exactement la même clé et le même corps. Le serveur rejoue la première réponse sans dupliquer l’écriture. Une même clé avec un corps différent renvoie `IDEMPOTENCY_KEY_REUSED`.

## Endpoints principaux

| Domaine | Routes |
|---|---|
| Produits | `products`, `products/{id}`, `products/barcode/{barcode}`, `products/qr/{reference}`, `packagings` |
| Clients | `clients`, `clients/{id}/history` |
| Fournisseurs | `suppliers`, `suppliers/{id}/history` |
| Ventes | `sales`, `sales/{id}` |
| Achats | `purchases`, `purchases/{id}` |
| Paiements | `payments`, `payments/{id}` |
| Chargements | `loading-orders`, `loading-orders/current`, `loading-orders/{id}/validate`, `loading-orders/{id}/close`, `loading-orders/{id}/cancel` |
| Stock opérateur | `operator-stock`, `operator-stock/movements` |
| Factures | `invoices`, `invoices/{id}/pdf`, `invoices/{id}/ticket`, `invoices/{id}/print-data` |
| Impression | `printers`, `printers/default`, `printers/{id}/test-payload`, `print-profiles`, `printing` |
| Stock global | `stock`, `stock/movements`, `stock/alerts` |
| Charges | `expenses`, `expense-categories` |
| Pilotage | `dashboard`, `alerts` |

OpenAPI : `/api/schema/` et Swagger : `/api/docs/` pour les comptes staff.

## Vente et stock opérateur

Une vente accepte plusieurs `items`. `quantity` est le nombre de conditionnements ; le serveur applique `conversion_factor`. `unit_price` peut être omis pour utiliser le tarif du client, mais ne peut jamais descendre sous le coût.

Si le créateur possède un chargement actif, Django rattache automatiquement la vente à ce chargement. Le catalogue/recherche et le solde retournés sont ceux de l’opérateur. La vente ne redécrémente jamais le dépôt.

## Cycle du chargement

1. Créer le brouillon avec `operator`, `notes` et `lines[{product, quantity}]`.
2. `validate` transfère atomiquement du dépôt vers l’opérateur ; un seul chargement peut être actif.
3. Lire `current` et `operator-stock` ; ne jamais filtrer un stock global dans Android.
4. Créer les ventes avec une clé d’idempotence.
5. `close` retourne atomiquement le reliquat au dépôt et historise vendu/retourné.

### Contrat HTTP des actions

Toutes les routes ci-dessous sont relatives à `/api/v1/`, exigent un jeton Bearer et se terminent par `/`.

| Action | Méthode et route | Corps | Permission | Réponse attendue |
|---|---|---|---|---|
| Lister | `GET loading-orders/` | aucun | droit de consultation | `200`, page de bons |
| Créer | `POST loading-orders/` | `operator`, `notes`, `lines` | `inventory.add_loadingorder` | `201`, bon brouillon |
| Modifier | `PATCH loading-orders/{id}/` | champs modifiés | `inventory.change_loadingorder` | `200`, uniquement si brouillon |
| Supprimer | `DELETE loading-orders/{id}/` | aucun | `inventory.delete_loadingorder` | `204`, uniquement si brouillon |
| Valider | `POST loading-orders/{id}/validate/` | aucun (`{}` accepté) | `inventory.validate_loadingorder` | `200`, statut `in_progress`, stock transféré |
| Annuler | `POST loading-orders/{id}/cancel/` | aucun (`{}` accepté) | `inventory.delete_loadingorder` | `200`, statut `cancelled`, uniquement si brouillon |
| Clôturer | `POST loading-orders/{id}/close/` | aucun (`{}` accepté) | `inventory.close_loadingorder` | `200`, statut `closed`, reliquat retourné |

`create`, `validate` et `close` doivent envoyer `Idempotency-Key`. Un `400` indique une transition ou des données invalides, `401` une session absente/expirée, `403` une permission absente, `405` un contrat serveur/client incompatible, `409` un conflit et `5xx` une erreur serveur. Après chaque succès, remplacer immédiatement le bon local par l’objet renvoyé puis rafraîchir les listes de stock concernées.

## Impression RPP02N

Le serveur ne contacte jamais le Bluetooth. Android récupère `print-data`, l’imprimante par défaut et le `test-payload`, puis envoie localement les octets au RPP02N. Pour l’arabe, utiliser le rendu bitmap lorsque `raster_arabic_recommended=true`. Une recette physique reste indispensable.

## Permissions

Les boutons Android suivent les permissions de `auth/me`, mais le serveur reste l’autorité. Un refus retourne 403. `inventory.view_product_pricing` contrôle l’exposition des coûts. Un opérateur ne doit jamais pouvoir voir les chargements, ventes, factures ou stocks d’un autre opérateur.
