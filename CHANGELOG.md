# Changelog

Toutes les modifications notables de ce projet seront documentées ici.

## [v1.0.0] - 2026-06-29

### Ajouté

- ERP Django pour gestion commerciale et stock.
- Authentification avec rôles : administrateur, gestionnaire, vendeur.
- Dashboard avec indicateurs de stock, ventes, achats, clients et fournisseurs.
- Gestion produits avec référence, code-barres automatique, QR code, photo et stock.
- Gestion clients, fournisseurs, achats, ventes et factures PDF.
- Génération automatique des numéros de facture au format `FAC-AAAA-000001`.
- Déploiement Render via `render.yaml`, `build.sh` et `start.sh`.
- Workflow CI GitHub Actions avec tests Django.

### Modifié

- Interface responsive pour desktop, tablette et mobile.
- Factures PDF améliorées avec identité visuelle et aperçu imprimable.
- Suppression du module conditionnements pour revenir à une gestion de stock simple par quantité produit.

### Sécurité

- Durcissement des sessions, cookies et headers HTTP.
- Ajout CSP, Permissions-Policy, COOP et CORP.
- Journalisation des connexions, déconnexions, échecs de connexion et erreurs HTTP sensibles.
- Validation et renommage sécurisé des uploads.
- Suppression des mots de passe générés affichés en clair dans l’administration.
- Audit dépendances via `pip-audit`.

### Tests

- Tests Django pour authentification, permissions, dashboard, inventory, commerce et sécurité.
