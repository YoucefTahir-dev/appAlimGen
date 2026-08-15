# Audit final avant mise en production

Date : 14 août 2026  
Branche d’audit : `security/preproduction-audit`  
Révision de départ : `f1fcbdb` (`feat(dashboard): add decision analytics`)  
Décision : **NON PRÊT POUR LA PRODUCTION**

## 1. Résumé exécutif

L’application démarre, les migrations sont cohérentes, les fichiers statiques se collectent et les 68 tests désormais découverts passent. Plusieurs durcissements à faible risque ont été appliqués sur une branche séparée. La facture A4 a également été générée, rendue en PNG avec Poppler et inspectée visuellement.

La mise en production ne doit toutefois pas être autorisée tant que les quatre sujets suivants ne sont pas résolus et validés sur une copie de la base PostgreSQL :

1. le stock courant et le journal des mouvements sont deux systèmes indépendants ;
2. les médias sont stockés sur le disque éphémère de Render ;
3. aucune sauvegarde/restauration complète n’est démontrée ;
4. des incohérences existent déjà dans la base locale et un ancien mot de passe administrateur demeure dans l’historique Git.

## 2. Architecture détectée

- Backend : Python, Django 5.2 LTS, vues serveur et templates Django.
- Base : SQLite en développement, PostgreSQL attendu en production.
- Frontend : Bootstrap 5 et Bootstrap Icons via CDN, CSS et JavaScript maison.
- Documents : ReportLab, OpenPyXL, QR Code et codes-barres.
- Déploiement : Render Blueprint, Gunicorn, WhiteNoise ; Docker disponible en complément.
- CI : GitHub Actions avec PostgreSQL de service.
- Applications Django : `accounts`, `core`, `inventory`, `commerce`, `expenses`.
- L’application n’expose pas une API REST générale ; les routes sont essentiellement des pages HTML authentifiées.

## 3. Vérifications exécutées

| Contrôle | Résultat |
|---|---|
| Suite complète Django | **68/68 tests réussis** |
| `manage.py check` | Aucun problème |
| Dérive de migrations | Aucune |
| Migration locale de l’index d’audit | Réussie |
| Compilation Python | Réussie |
| Compilation FR/AR | Réussie |
| Collecte statique à blanc | 131 fichiers détectés |
| `pip check` | Aucun conflit |
| `docker compose config --quiet` | Réussi |
| Construction Docker | Non exécutée : moteur Docker Desktop arrêté |
| Démarrage local | Réussi ; `/`, `/healthz/` et `/readyz/` répondent 200 |
| Contrôle production Django | Réussi avec uniquement `security.W021` (préchargement HSTS volontairement désactivé) |
| PDF A4 | Généré, rendu en PNG et inspecté ; pas de chevauchement, arabe lié, logo/tableau/totaux/QR lisibles |
| Audit `pip-audit` PyPI et OSV | Non concluant : certificat TLS local non reconnu par les deux services |
| PostgreSQL réel | Non testé localement : aucun serveur PostgreSQL disponible |
| CI distante GitHub | Statut non récupérable dans l’environnement d’audit |
| Render public actuel | Page de connexion 200 et en-têtes de sécurité présents ; `/healthz/` et `/readyz/` encore 404 car la branche d’audit n’est pas déployée |

Le service Render public a nécessité environ 37 secondes pour les trois premières requêtes, ce qui est cohérent avec un réveil d’instance gratuite.

## 4. Correctifs à faible risque appliqués

- Découverte des tests rétablie par les fichiers `__init__.py` manquants : 16 tests détectés avant correction, 68 après les ajouts.
- Déconnexion convertie en POST avec jeton CSRF.
- SQLite interdit par défaut lorsque `DEBUG=False` ; PostgreSQL devient obligatoire en production.
- CI alignée sur Python 3.12.8, avec `check --deploy` et `collectstatic`.
- Validation réelle des images, PDF et classeurs XLSX ; limites de taille et protections contre les archives décompressées excessives.
- Import Excel rendu transactionnel : aucune ligne n’est conservée si une ligne est invalide.
- Neutralisation des formules Excel et échappement des valeurs utilisateur dans les PDF.
- Validation des montants, quantités et TVA ; une remise ne peut plus ramener la vente sous son coût d’achat.
- Opérations commerciales enveloppées dans des transactions ; suppressions groupées dangereuses désactivées dans l’admin.
- Mot de passe temporaire imposant un changement au prochain accès.
- Limitation des tentatives de connexion et de récupération, journalisation, index d’audit et traitement contrôlé de `X-Forwarded-For`.
- En-têtes de sécurité conservés également sur les réponses HTTP 429.
- Endpoints de liveness `/healthz/` et readiness `/readyz/` ajoutés ; Render pointe sur la readiness.
- Docker durci avec `.dockerignore` et utilisateur non-root ; migration retirée de la phase de build pour éviter la double exécution.
- Django relevé au minimum à 5.2.17 et Pillow à 12.3.0 ; ces versions corrigent des avis publiés en 2026.
- Police DejaVu Sans embarquée avec sa licence et dépendances de façonnage arabe ajoutées pour les PDF.
- Affichage des erreurs de formulaire complété sur les principaux écrans.

## 5. Blocages P0

### P0-1 — Intégrité du stock

`StockMovement` ne modifie jamais `Product.quantity` (`apps/inventory/models.py:114-135`, `apps/inventory/views.py:168-185`). À l’inverse, les lignes d’achat et de vente modifient directement la quantité sans créer de mouvement (`apps/commerce/models.py:38-67`, `apps/commerce/models.py:149-178`). Les modifications directes d’un produit et l’import Excel contournent également le journal.

Impact : l’état du stock et son historique divergent ; l’historique ne peut ni expliquer ni reconstruire le stock réel. Les transactions ajoutées réduisent les mises à jour partielles mais n’empêchent pas deux ventes concurrentes, car les produits ne sont pas verrouillés avec `select_for_update()`.

Correction requise : créer un service stock atomique unique, verrouiller les produits, rendre les mouvements immuables et liés aux lignes commerciales, puis rapprocher les données existantes avant activation.

### P0-2 — Fichiers médias perdus sur Render

Le stockage est local (`gestio_stock/settings.py:217-232`) et les médias ne sont servis par Django qu’en mode debug (`gestio_stock/urls.py:19-21`). Le plan Render est gratuit (`render.yaml:5`) et ne déclare ni disque ni stockage objet.

Photos, logos, QR codes, codes-barres et justificatifs peuvent donc disparaître à chaque redémarrage, mise en veille ou redéploiement. Render documente que le système de fichiers Free est éphémère et que les disques persistants sont réservés aux services payants :

- https://render.com/docs/free
- https://render.com/docs/disks

Correction requise : stockage objet durable S3-compatible/Cloudinary équivalent, justificatifs privés, migration des fichiers existants et test après redémarrage/redéploiement.

### P0-3 — Sauvegarde et restauration non démontrées

Aucun `pg_dump`, job de sauvegarde, plan de rétention, sauvegarde des médias, RPO/RTO ni compte-rendu de restauration n’existe dans le dépôt. Le fournisseur et le niveau de protection effectif de la base externe ne sont pas vérifiables depuis le code.

Correction requise : sauvegarde chiffrée hors fournisseur, PITR si disponible, sauvegarde des objets et test réel de restauration consigné avant ouverture.

### P0-4 — Données existantes et secret historique

L’échantillon SQLite local contient :

- 3 ventes dont le total stocké diffère du total recalculé ;
- un nom dupliqué six fois dans chacune des tables catégorie, marque et unité ;
- 7 lignes de vente et 2 lignes d’achat pour seulement 1 mouvement de stock ;
- aucun règlement enregistré.

Ces chiffres ne prouvent pas l’état de PostgreSQL, mais imposent une procédure de rapprochement avant migration.

Le mot de passe administrateur n’est plus présent dans la version courante de `LOGIN_INFORMATION.md`, mais il reste dans le commit historique `11490a0`. Il faut considérer ce secret compromis : rotation en production, révocation des sessions et, si le dépôt a été partagé, nettoyage coordonné de l’historique.

## 6. Risques P1

### Paiements incomplets

`Payment.sale` et `Payment.purchase` sont tous deux optionnels (`apps/commerce/models.py:184-206`) sans contrainte XOR, montant positif, plafond ni workflow utilisateur. Aucune route de paiement n’est exposée. Une vente comptant ne crée pas de règlement, alors que le Dashboard calcule les impayés avec `payments__amount` (`apps/core/dashboard.py:122-128`). Les indicateurs d’impayés sont donc faux.

### Bénéfices historiques instables

`SaleLine` ne mémorise pas le coût au moment de la vente. Le Dashboard utilise le prix d’achat actuel du produit (`apps/core/dashboard.py:108-115`, `166-175`, `240-245`). Modifier un produit aujourd’hui réécrit virtuellement les bénéfices passés.

### Validations contournables hors formulaires

Les contrôles de prix, quantité, remise, TVA et charge sont principalement dans les formulaires. L’admin, l’ORM ou un script peuvent les contourner. Des contraintes SQL devront être ajoutées après nettoyage des données réelles.

### Autorisations trop larges

Un vendeur peut consulter/exporter le Dashboard financier complet et lire toutes les ventes/factures par identifiant. Il faut confirmer si cette visibilité globale correspond réellement aux règles métier ; sinon filtrer par utilisateur/caisse et masquer charges, marges et classements fournisseurs.

### Récupération par e-mail non prête

Le backend e-mail par défaut reste la console (`gestio_stock/settings.py:242`). Sans SMTP/API configuré, les liens de récupération peuvent se retrouver dans les logs Render et aucun e-mail n’est livré.

### Dépendances non reproductibles

Django et Pillow ont désormais des minimums corrigés, mais le dépôt ne contient toujours pas de lock avec versions exactes et hashes. Deux builds du même commit peuvent résoudre des versions différentes. L’audit automatisé en ligne a échoué à cause de la chaîne de certificats locale.

Références :

- https://docs.djangoproject.com/en/5.2/releases/5.2.17/
- https://github.com/advisories/GHSA-pg7v-jwj7-p798

## 7. Risques P2

- Aucune pagination serveur sur produits, clients, fournisseurs, mouvements, ventes, achats et charges.
- Les exports chargent les jeux de données complets en mémoire.
- Les dates des ventes, achats et charges ne disposent pas des index nécessaires aux tableaux de bord volumineux.
- Le Dashboard effectue de nombreuses agrégations séparées et plusieurs balayages similaires.
- L’import produit ne rattache pas encore catégorie, marque et unité malgré les colonnes annoncées.
- `CompanySettings.objects.first()` n’impose pas un singleton.
- Certaines vues GET ont un effet de bord : génération de QR/code-barres sur la fiche produit et allocation du numéro de ticket à la prévisualisation (`apps/inventory/views.py:191-214`, `apps/commerce/views.py:202-215`).
- La traduction reste partielle dans des messages Python, modèles et impressions ; le PDF A4 est maintenant techniquement capable d’afficher l’arabe, mais les libellés restent majoritairement français.
- Bootstrap et les icônes sont chargés depuis un CDN sans SRI ; la CSP autorise encore `unsafe-inline` (`gestio_stock/settings.py:103-112`).
- Aucun test navigateur réel Android/iPhone n’a pu être exécuté dans cet environnement.
- Le montage `.:/app` de Docker Compose masque les permissions et statiques préparés dans l’image ; à revoir pour un usage autre que le développement.
- La readiness interroge PostgreSQL à chaque sonde. C’est conforme à la recommandation Render pour un contrôle opérationnel, mais il faut surveiller l’impact sur un fournisseur serverless et distinguer clairement liveness/readiness.

## 8. Notes de qualité

| Axe | Note |
|---|---:|
| Sécurité | 72/100 |
| Qualité du code | 66/100 |
| Performance | 52/100 |
| Base de données | 43/100 |
| Frontend / responsive / i18n | 58/100 |
| Compatibilité production | 52/100 |
| Déploiement Render | 38/100 |
| **Note globale** | **54/100** |

La réussite des tests ne compense pas les risques d’intégrité, de perte de fichiers et d’absence de restauration.

## 9. Plan de correction priorisé

### Avant tout déploiement — P0

1. Faire une sauvegarde de PostgreSQL et des médias, puis réussir une restauration sur un environnement isolé.
2. Faire tourner le mot de passe administrateur compromis et invalider les sessions.
3. Installer un stockage objet durable et migrer les médias.
4. Concevoir puis migrer le service de stock atomique avec verrouillage PostgreSQL et journal complet.
5. Rapprocher les totaux, séquences, doublons et quantités de la base réelle.
6. Exécuter la suite complète et des tests de concurrence sur PostgreSQL, pas seulement SQLite.

### Ensuite — P1

1. Implémenter le workflow de paiements et corriger les alertes d’impayés.
2. Ajouter le coût historique sur les lignes de vente et choisir une méthode de valorisation.
3. Ajouter les contraintes SQL après nettoyage.
4. Revoir les droits vendeur et l’accès aux documents.
5. Configurer l’e-mail de production et tester la récupération de mot de passe.
6. Produire un lock de dépendances avec hashes et activer un audit de dépendances en CI.

### Améliorations — P2/P3

Pagination, index métier, optimisation Dashboard, exports volumineux, traduction complète, SRI/CSP sans inline, observabilité, tests navigateur et optimisation des images.

## 10. Fichiers concernés par les correctifs d’audit

- Racine et dépendances : `.env.example`, `.gitignore`, `.dockerignore`, `requirements.txt`, `LOGIN_INFORMATION.md`.
- CI et déploiement : `.github/workflows/django-ci.yml`, `Dockerfile`, `docker-compose.yml`, `build.sh`, `render.yaml`.
- Configuration Django : `gestio_stock/settings.py`, `gestio_stock/urls.py`.
- Comptes : `apps/accounts/forms.py`, `apps/accounts/views.py`, `apps/accounts/tests/test_auth.py`, `apps/accounts/templates/accounts/profile.html`.
- Commerce : `apps/commerce/admin.py`, `forms.py`, `models.py`, `utils.py`, `views.py`, `tests/__init__.py`, `tests/test_commerce.py`, `templates/commerce/purchase_form.html`, `templates/commerce/sale_form.html`.
- Core : `apps/core/export_security.py`, `models.py`, `security.py`, `views.py`, `migrations/0003_auditlog_audit_action_ip_date_idx_and_more.py`, `tests/__init__.py`, `tests/test_security.py`.
- Inventaire : `apps/inventory/forms.py`, `views.py`, `tests/__init__.py`, `tests/test_inventory.py` et les templates de produit, import, client, fournisseur et mouvement.
- Charges : `apps/expenses/forms.py`, `apps/expenses/views.py`.
- Interface et documents : `templates/base/base.html`, `static/css/styles.css`, `static/fonts/DejaVuSans.ttf`, `static/fonts/DEJAVU-LICENSE.txt`.
- Rapport : `AUDIT_PRODUCTION_2026-08-14.md`.

## 11. Conclusion

Le code audité est plus robuste qu’au départ et tous les contrôles locaux disponibles sont verts. Néanmoins, le déploiement de cette branche vers `main` ou Render doit rester bloqué jusqu’à résolution et validation des portes P0. Aucun commit, push, merge ou déploiement n’a été effectué pendant cet audit.
