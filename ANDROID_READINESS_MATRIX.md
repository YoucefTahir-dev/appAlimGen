# Matrice Web / API / Android — 13 septembre 2026

| Fonctionnalité | Web | API | Isolation/sécurité | Android |
|---|---|---|---|---|
| Login, refresh, logout, profil | Oui | `auth/*` | JWT versionné, comptes inactifs/forcés refusés | Ready |
| Changement de mot de passe | Oui | `auth/password/change/` | anciens access/refresh = `TOKEN_REVOKED` | Ready |
| Produits, prix, conditionnements | Oui | `products`, `packagings` | RBAC et prix/coûts filtrés | Ready |
| Clients et GPS | Oui | `clients` | validation serveur | Ready |
| Ventes multi-produits | Oui | `sales` | transaction, stock, prix, idempotence | Ready |
| Achats | Oui | `purchases` | transaction, stock, idempotence | Ready |
| Paiements partiels | Oui | `payments` | verrou document, surpaiement refusé, idempotence | Ready |
| Factures PDF / données ticket | Oui | `invoices/*` | permissions et coûts masqués | Ready |
| Chargement brouillon | Oui | `loading-orders` | RBAC, lignes uniques et positives | Ready |
| Validation chargement | Oui | `loading-orders/{id}/validate/` | verrouillage, un seul actif, journal | Ready sous CI PostgreSQL |
| Chargement courant | Oui | `loading-orders/current/` | queryset limité à l’opérateur | Ready |
| Stock opérateur | Oui | `operator-stock` | jamais déduit deux fois du dépôt | Ready |
| Journal stock opérateur | Admin | `operator-stock/movements/` | append-only | Ready |
| Clôture et reliquat | Oui | `loading-orders/{id}/close/` | retour atomique au dépôt | Ready sous CI PostgreSQL |
| Annulation brouillon | Oui | `loading-orders/{id}/cancel/` | état contrôlé | Ready |
| Impression RPP02N | Config Web | payload local | serveur ne contacte pas le Bluetooth | Recette physique requise |
| OpenAPI | Docs staff | `/api/schema/`, `/api/docs/` | JWT Bearer et erreurs documentés | Ready |

Règle d’intégration : utiliser `Idempotency-Key` sur ventes, achats, paiements, création/validation/clôture des chargements. Stocker atomiquement le nouveau refresh token renvoyé par l’API.
