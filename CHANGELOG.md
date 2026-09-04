# Changelog

Toutes les modifications notables de ce projet seront documentées ici.

## [Non publié]

### Paiements et coûts historiques

- Suivi transactionnel des règlements clients et fournisseurs avec paiements partiels, soldes et statuts.
- Références de règlement automatiques et audit de l’utilisateur ayant saisi le paiement.
- Coût d’achat figé sur chaque ligne de vente pour conserver des bénéfices historiques stables.
- Index de dates commerciales et pagination des listes ventes/achats à 25 lignes.
- Les règlements orphelins, liés à deux documents, négatifs ou en surpaiement sont bloqués sous verrou transactionnel.
- Les documents historiques dont l’état de paiement est inconnu sont à rapprocher et ne créent pas de fausses alertes d’impayé.
- Le Dashboard utilise le coût historique vendu plutôt que le prix d’achat courant du produit.

### Ajouté

- API REST v1 centralisée pour Web, Android et futur client Windows.
- JWT avec rotation, blacklist, révocation et clé de signature dédiée.
- Conditionnements produit partagés Web/API avec conversion du stock en unité de base.
- Profils imprimantes et formats 58/80/A4, préférences utilisateur et payloads locaux.
- Diagnostic RPP02N sans connexion Bluetooth depuis le serveur.
- Contrat OpenAPI versionné et pipeline CI étendu aux branches de fonctionnalités.
- Journal de stock traçable avec variation appliquée, soldes avant/après, origine et auteur.
- Annulation des mouvements par contrepassation sans suppression de l’historique.
- Ticket de caisse thermique 58 mm et 80 mm.
- Numérotation indépendante des tickets au format `TCK-AAAA-000001`.
- Module Charges avec catégories, CRUD, recherche, impression, export PDF et export Excel.
- Indicateurs dashboard pour charges du jour, du mois, de l'année, bénéfice brut et bénéfice net.

### Corrigé

- Le produit, son stock, ses conditionnements et le tarif du type de client remontent désormais automatiquement sur toutes les lignes de facture, y compris les lignes ajoutées dynamiquement.
- Le changement de client, de produit ou de conditionnement recalcule le prix sans rechargement et affiche une erreur explicite si la récupération échoue.
- Mise à niveau de Django REST Framework vers `3.17.2` afin de corriger CVE-2026-73228 et CVE-2026-73229.
- Lock multi-runtime corrigé en épinglant `typing-extensions`, requis par Python 3.12 en CI/Render mais omis lors d'une résolution sous Python 3.13.
- Les entrées, sorties et ajustements manuels mettent désormais à jour le stock dans une transaction verrouillée.
- Les créations, modifications et suppressions de lignes d’achat/vente, les stocks initiaux et les imports Excel utilisent désormais le même journal transactionnel avec verrouillage et origine métier.
- Les ventes concurrentes et les réductions d’achat impossibles échouent sans stock négatif ni écriture commerciale partielle.
- Une sortie manuelle ne peut plus produire un stock négatif.
- L’historique de stock est protégé contre les modifications et suppressions directes.

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
# API Android

- ajout d'une API REST versionnée `/api/v1/` sans modification des sessions Web ;
- authentification JWT avec rotation, blacklist et révocation ;
- partage du RBAC Django entre Web et API ;
- endpoints produits, clients, fournisseurs, ventes, achats, factures, stock,
  charges, Dashboard et alertes ;
- création transactionnelle des ventes/achats via une couche service partagée ;
- documentation OpenAPI et guide d'intégration Android.
