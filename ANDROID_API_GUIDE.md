# Guide API Android — El Amine ERP

## Architecture et sécurité

L'application Android communique uniquement avec Django via HTTPS. Elle ne doit
jamais recevoir `DATABASE_URL` ni accéder directement à PostgreSQL Neon.

```text
Android -> HTTPS -> Django REST API -> services métier Django -> Neon PostgreSQL
Web     -> HTTPS -> Django HTML/session -> mêmes services -> Neon PostgreSQL
```

Les sessions Django du Web restent inchangées. L'API utilise des JWT Bearer :

- access token : 10 minutes par défaut ;
- refresh token : 7 jours par défaut ;
- rotation à chaque refresh ;
- ancien refresh token blacklisté après rotation ;
- révocation explicite au logout.

En production, `JWT_SIGNING_KEY` peut être défini avec un secret long et distinct
dans Render/GCP Secret Manager. S'il est absent, Django utilise `SECRET_KEY`.

Android doit conserver les tokens dans `EncryptedSharedPreferences` ou dans le
Keystore Android, jamais en clair dans les logs, une base locale non chiffrée ou Git.

## URLs selon l'environnement

```text
Développement émulateur : http://10.0.2.2:8000/api/v1/
Staging                 : https://staging.example.com/api/v1/
Production              : https://erp.example.com/api/v1/
```

La base URL doit être une propriété de build Android, pas une constante dispersée
dans le code. En production, HTTPS est obligatoire.

## Authentification

### Connexion

`POST /api/v1/auth/login/`

```json
{
  "username": "vendeur",
  "password": "mot-de-passe"
}
```

### Rafraîchissement

`POST /api/v1/auth/refresh/`

```json
{"refresh": "<refresh-token>"}
```

La réponse contient un nouvel access token et un nouveau refresh token. Android
doit remplacer atomiquement les deux anciennes valeurs.

### Déconnexion et révocation

`POST /api/v1/auth/logout/`, avec l'access token dans l'en-tête et le refresh
token dans le corps :

```http
Authorization: Bearer <access-token>
Accept-Language: fr
Content-Type: application/json
```

```json
{"refresh": "<refresh-token>"}
```

### Profil courant

`GET /api/v1/auth/me/` retourne le profil, le rôle et uniquement les permissions
effectives, après application des refus individuels.

## Format des réponses

Succès :

```json
{
  "success": true,
  "data": {}
}
```

Erreur :

```json
{
  "success": false,
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Stock insuffisant : 3 unité(s) disponible(s).",
    "details": {}
  }
}
```

Codes métier importants : `INSUFFICIENT_STOCK`, `SALE_PRICE_BELOW_COST`,
`VALIDATION_ERROR`, `AUTHENTICATION_REQUIRED`, `PERMISSION_DENIED`, `NOT_FOUND`
et `RATE_LIMITED`.

## Langues

Envoyer `Accept-Language: fr`, `ar` ou `en`. Django `LocaleMiddleware` traduit les
messages serveur et renvoie `Content-Language`. Les codes d'erreur restent stables
et ne doivent pas être traduits côté Android.

## Pagination, recherche et tri

Les grandes listes sont paginées :

```text
GET /api/v1/products/?page=1&page_size=25
GET /api/v1/products/?search=chocolat&ordering=name
GET /api/v1/sales/?start_date=2026-08-01&end_date=2026-08-31
```

`page_size` est limité à 100. Une réponse paginée contient `count`, `next`,
`previous` et `results` dans `data`.

## Endpoints v1

| Domaine | Endpoints principaux |
|---|---|
| Référentiels | `/categories/`, `/brands/`, `/units/` |
| Produits | `/products/`, `/products/{id}/`, `/products/barcode/{barcode}/`, `/products/qr/{reference}/`, `/packagings/` |
| Clients | `/clients/`, `/clients/{id}/history/` |
| Fournisseurs | `/suppliers/`, `/suppliers/{id}/history/` |
| Ventes | `/sales/`, `/sales/{id}/` |
| Achats | `/purchases/`, `/purchases/{id}/` |
| Factures | `/invoices/`, `/invoices/{id}/pdf/`, `/invoices/{id}/ticket/?width=80`, `/invoices/{id}/print-data/` |
| Impression | `/printers/`, `/printers/default/`, `/printers/{id}/test-payload/`, `/print-profiles/`, `/printing/` |
| Stock | `/stock/`, `/stock/movements/`, `/stock/alerts/` |
| Charges | `/expenses/`, `/expense-categories/` |
| Dashboard | `/dashboard/?period=month` |
| Alertes | `/alerts/` |

La documentation OpenAPI est disponible aux administrateurs sur `/api/docs/` et
le schéma sur `/api/schema/`.

## Création d'une vente

`POST /api/v1/sales/`

```json
{
  "client": 12,
  "discount": "100.00",
  "tax_rate": "19.00",
  "payment_type": "cash",
  "pay_full": true,
  "items": [
    {
      "product": 42,
      "packaging_id": 9,
      "quantity": 2,
      "unit_price": "850.00"
    }
  ]
}
```

Django verrouille les produits, vérifie le stock et le prix minimum, réserve les
numéros `FAC-*` et `TCK-*`, crée les lignes, mouvements et paiement dans une seule
transaction. Une erreur annule toute l'opération.

`quantity` représente le nombre de conditionnements vendus. Django convertit vers
l'unité de stock avec `conversion_factor`. Sans `packaging_id`, la quantité est en
unité de base. Si `unit_price` est omis, le prix par défaut du conditionnement est
utilisé. Le prix transmis est toujours le prix d'un conditionnement complet.

Exemple : un carton `x24`, quantité `2`, consomme 48 unités de stock. Si le coût
d'achat unitaire est 50 DZD, le prix du carton doit être au moins 1 200 DZD.

## Impression locale Android

Le serveur ne se connecte jamais en Bluetooth. Android récupère le contrat avec :

```text
GET /api/v1/invoices/{id}/print-data/?paper_width=80&language=bilingual
GET /api/v1/printers/default/
GET /api/v1/printers/{id}/test-payload/
```

Le téléphone découvre et mémorise localement la RPP02N, décode le payload Base64,
puis l'envoie au transport Bluetooth. Pour l'arabe, utiliser un rendu bitmap si le
diagnostic indique `raster_arabic_recommended=true`.

## Permissions

Chaque endpoint appelle les mêmes permissions Django que le Web. Un bouton caché
sur Android ne constitue jamais une protection. Une opération interdite retourne
HTTP 403. Les refus individuels priment sur le rôle et les permissions directes.

Le prix d'achat n'est retourné que si l'utilisateur possède
`inventory.change_product`.

## Fichiers

Les photos et justificatifs utilisent les validateurs existants et le stockage
objet configuré par Django. Utiliser `multipart/form-data` pour envoyer un fichier.
Ne jamais conserver une URL signée comme URL permanente : elle expire et doit être
redemandée à l'API.

## CORS

Une application Android native n'est pas protégée par CORS. Aucun
`CORS_ALLOW_ALL_ORIGINS` n'est activé. La sécurité repose sur HTTPS, JWT, rotation,
révocation, rate limiting et permissions serveur.
