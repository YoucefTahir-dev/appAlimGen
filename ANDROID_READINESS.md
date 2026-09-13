# Android readiness — audit du 13 septembre 2026

## Verdict : 🔴 NOT READY pour l’ensemble du périmètre demandé

La base Django/DRF permet déjà un client connecté pour les ventes globales,
produits, clients, factures et configuration des imprimantes. Cela ne suffit pas
à valider toute l’application Android demandée avec stock opérateur, chargements,
paiements et mêmes fonctions que le Web.

Blocages à lever avant validation globale :

1. Concevoir les bons de chargement et l’affectation de stock opérateur, puis
   imposer l’isolation par objet dans recherche, listes, ventes, factures et stock.
   Aucun modèle/endpoint correspondant n’existe actuellement dans les apps suivies.
2. Ajouter l’API des paiements partiels/ultérieurs et les mutations commerciales
   requises. Ne pas contourner les routes manquantes avec du HTML ou un accès DB.
3. Révoquer les sessions JWT antérieures au changement/reset de mot de passe
   avec une stratégie de transition explicite. Voir H-R1 dans SECURITY_AUDIT.md.
4. Qualifier les retries : pas d’idempotence des créations, ni garantie de refresh
   à consommation unique sous concurrence. Pas de synchronisation offline à brancher
   directement sur des POST répétés.
5. Réaliser la recette HTTPS avec comptes restreints sur staging Neon/GCP,
   confirmer SMTP/cache/proxy et tester l’impression RPP02N physique en français/arabe.

## Contrat pour le futur développeur

Base `/api/v1/`, JWT Bearer, HTTPS en production. Auth/login reçoit username/password
et renvoie success/data avec access, refresh, user. Refresh renvoie un access et
un nouveau refresh : remplacer atomiquement le refresh stocké. Logout reçoit
le refresh de l’utilisateur connecté. Me expose identité et permissions effectives.
Les comptes force_password_change doivent passer par le Web pour changer leur mot
de passe ; pas encore d’endpoint natif dédié.

Listes paginées dans `data` avec count/next/previous/results, page et page_size ≤100.
Dates ISO, montants des serializers Decimal en chaînes ; certains agrégats Dashboard
utilisent encore des nombres JSON. Conserver précision décimale côté client.
Filtres dates existants : start_date/end_date (pas date_from/date_to). Ne pas renommer
les paramètres existants sans version/migration. `Accept-Language: fr|ar|en`.

Factures : pdf retourne application/pdf ; ticket retourne text/html ; print-data
retourne JSON métier adapté au terminal. Bluetooth et rendu raster arabe restent
locaux. Ne pas interpréter le test-payload ESC/POS comme certification de chaque
protocole listé : certains exigent un adaptateur constructeur.

Schéma `/api/schema/` et Swagger `/api/docs/` accessibles aux comptes staff via JWT.
Le fichier openapi.yaml est régénéré et l’enveloppe de réponse décrite par
`apps/api/schema.py`. Les schémas dynamiques de certains endpoints restent génériques.
La matrice ANDROID_READINESS_MATRIX.md détaille les routes disponibles/manquantes.

## Validation et limites

Base initiale main/f18cee2 ; CI de cette base confirmée success (run 34768437723).
Première suite ciblée : 115 tests, 1 échec de libellé de remise, 3 tests PostgreSQL
ignorés sur SQLite. Le libellé attendu a été rétabli dans le service partagé.
Vérification suivante : 28 tests passés. Tests supplémentaires de contrat OpenAPI
et concurrence inclus dans la suite finale.

Suite complète finale locale : **260 tests en 225,261 s, OK (skipped=3)**,
soit 257 réussis et 3 tests de concurrence PostgreSQL non exécutés sur SQLite.
16 tests ajoutés (13 API readiness/contrat, 2 concurrence, 1 injection export).
Dernière vérification du schéma/enveloppe après factorisation du composant APIError :
1 test passé. Django check, migrations check, OpenAPI --validate, compileall,
collectstatic et pip check réussis. Audit dépendances : aucune vulnérabilité connue.
Serveur local démarré sur 127.0.0.1:8769 : /healthz, /readyz et / renvoient 200,
/api/v1/products sans token renvoie 401. Aucun navigateur/matériel physique testé.
La CI du commit d’audit sera suivie après push ; son résultat exact est communiqué
dans la réponse finale, sans l’anticiper dans ce rapport préparé avant publication.

Les 3 tests concurrence nécessitent PostgreSQL ; Docker local n’a pas de moteur
démarré. Le pipeline Django CI utilise PostgreSQL 16 et doit les exécuter après
publication. Aucun test ni migration manuelle n’a ciblé Neon production.
Pas de linter configuré ajouté arbitrairement ; compileall et diff --check utilisés.

## Impact production et Git

Audit et corrections autorisés directement sur main. Le workflow de travail
indique l’auto-déploiement Render sur main. `render.yaml` définit build.sh puis
start.sh (migrate non interactif + gunicorn). Le réglage auto-deploy de la console
n’est pas lisible dans ce fichier : tout push main est traité comme potentiellement
déployant. Aucun redéploiement manuel ajouté, aucune migration nouvelle/destructive.

Les changements visibles sont des refus de données invalides et d’accès non
autorisés, une centralisation des créations Web et un export sûr. Les routes
existantes restent présentes ; suppression conditionnelle de unit_cost pour les
comptes sans permission et contrôle renforcé des historiques sont intentionnels.
Une application qui dépendait de ces fuites doit traiter les champs absents/403.

## ANDROID READINESS

| Critère | Résultat | Portée |
|---|---|---|
| Architecture Web/API | PASS | Monolithe avec services partagés ; aucun accès DB Android |
| Authentication | FAIL | H-R1, révocation au changement de mot de passe à compléter |
| Authorization | FAIL | RBAC existant testé ; isolation tournée absente |
| Business logic separation | PASS | Créations partagées ; réserves sur éditions Web |
| Stock integrity | PASS sous réserve PostgreSQL CI | Stock global ; pas de stock opérateur |
| Sales integrity | PASS connecté | Validation/transactions ; pas d’idempotence offline |
| Invoice integrity | PASS sous réserve PostgreSQL CI | Séquences et calculs existants |
| API completeness | FAIL | Paiements, chargements, éditions notamment |
| Printer API | PASS configuration | Matériel non validé |
| Security | FAIL validation globale | 1 HIGH restant et infrastructure non inspectée |
| OpenAPI | PASS syntaxe/enveloppe | Schémas dynamiques encore partiels |
| Tests | PASS local | 260 tests, 257 réussis, 3 PostgreSQL à exécuter en CI |

Critical vulnerabilities remaining : 0 confirmée dans le périmètre inspecté.
High vulnerabilities remaining : 1 (H-R1).

FINAL VERDICT : 🔴 NOT READY pour le périmètre complet demandé.
