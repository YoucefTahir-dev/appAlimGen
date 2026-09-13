# Matrice Web / API / Android — 13 septembre 2026

« Oui » signifie présence dans le code et contrôles locaux correspondants,
pas validation du matériel ou de l’hébergement. « Partiel » explicite les écarts.
Tous les chemins API ci-dessous sont sous `/api/v1/`.

| Fonctionnalité | Web OK | API OK | Sécurité OK | Android Ready | Action requise / endpoint |
|---|---|---|---|---|---|
| Connexion / refresh / logout / profil courant | Oui | Oui | Partiel | Réserve | auth/login, refresh, logout, me ; révocation après changement de mot de passe à durcir |
| Changement/reset mot de passe | Oui | Non | Web testé | Non natif | Actuellement passage par Web requis |
| Produits / tarifs / conditionnements | Oui | Oui | Oui dans RBAC actuel | Oui hors tournée | products, products/{id}/price, packagings |
| Recherche / code-barres / QR | Oui | Oui | Oui | Oui | products?search=, products/barcode/{barcode}, products/qr/{reference} |
| Catégories / marques / unités | Oui | Lecture | Oui | Partiel | categories, brands, units : références non paginées |
| Clients / type / GPS | Oui | Oui | Oui dans RBAC actuel | Oui hors affectations | clients ; lat/lng validés et facultatifs |
| Reverse géocodage | Oui | Pas de route JWT dédiée | Route Web protégée | Partiel | Réutiliser fournisseur côté backend via future API dédiée |
| Historique client | Oui | Oui | Corrigé | Oui | clients/{id}/history exige aussi view_sale |
| Fournisseurs / historique | Oui | Oui | Corrigé | Oui selon droits achats | suppliers ; history exige view_purchase |
| Création vente multi-produit | Oui | Oui | Corrigé | Oui hors tournée | sales : service commun, prix/coût, stock et TVA vérifiés |
| Modification vente | Oui | Non | Web testé | Non | PATCH/PUT sales absent ; ne pas simuler par suppression/recréation |
| Achats création / lecture / suppression | Oui | Oui | Corrigé | Oui selon droits | purchases ; référence fournie, unique |
| Modification achat | Oui | Non | Web testé | Non | Pas de PATCH/PUT purchases |
| Paiements partiels / règlement ultérieur | Oui | Non | Modèle protégé | Non | Ajouter une API Payment ; pay_full à création vente seulement |
| Factures / PDF A4 | Oui | Oui | Corrigé coûts cachés | Oui | invoices, invoices/{id}/pdf |
| Ticket 58/80 | Oui | Oui HTML + JSON | Oui dans RBAC actuel | Partiel | ticket retourne HTML ; utiliser print-data pour Android |
| Impression physique RPP02N | Navigateur/local | Payload seulement | Transport local | Non validé matériel | Test téléphone/imprimante et rendu arabe raster obligatoire |
| Imprimantes / défaut / profils | Oui | Oui | Permissions vérifiées | Oui configuration | printers, default, set-default, test-payload ; print-profiles |
| Préférence personnelle imprimante | Modèle présent | Pas d’endpoint d’écriture dédié | À compléter | Partiel | UserPrinterPreference non exposé par le routeur |
| Stock global / mouvements / alertes | Oui | Lecture | Journal protégé | Oui global | stock, stock/movements, stock/alerts |
| Ajustement / contrepassation stock | Oui | Pas de route dédiée | Web/modèle testés | Partiel | Modification quantité produit n’est pas une API de retour métier |
| Bons de chargement | Non | Non | Non implémenté | Non | Modèles, états, validation concurrente à concevoir |
| Stock opérateur en tournée | Non | Non | Non implémenté | Non | Affectations et filtrage serveur sur toutes les routes nécessaires |
| Retours vente/achat métier | Pas de module dédié | Non | À concevoir | Non | Distinguer retour, annulation et suppression |
| Charges CRUD / justificatifs | Oui | Oui | Montant API corrigé | Oui | expenses, expense-categories |
| Rapports charges PDF/Excel | Oui | Non dédié | Web contrôlé | Partiel | Prévoir routes JWT de téléchargement |
| Dashboard / filtre utilisateur | Oui | Oui | Réserves métier | Partiel | dashboard ; visibilité globale/coûts à préciser par rôle |
| Alertes | Oui | Oui plafonné | Permissions documents corrigées | Partiel | alerts : top 100/type, pas de centre de notifications persistant |
| Exports Dashboard | Oui | Pas de routes JWT | Injection Excel corrigée | Partiel | Web session seulement |
| Utilisateurs / rôles / refus individuels | Oui | me seulement | Tests RBAC | Partiel | Pas d’administration REST des utilisateurs/rôles |
| Paramètres entreprise / sauvegardes / audit logs | Oui | Non dédié | Administration Web | Partiel | Garder administration Web ou définir endpoints protégés |
| FR/AR/EN et RTL | Présent | Accept-Language | Partiel | Partiel | UI RTL Android locale ; PDF et nouveaux messages restent à compléter |

La matrice n’autorise pas un client Android à télécharger le stock global puis
à masquer les autres lignes. L’isolation des tournées doit être dans les
querysets et dans les services de vente avant de déclarer ce parcours prêt.
