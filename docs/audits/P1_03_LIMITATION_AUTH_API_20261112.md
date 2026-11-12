# P1-3 — Limitation partagée des authentifications API (SEC-05)

Date : 12 novembre 2026. Statut : qualification locale réussie ; livraison par PR,
sans autorisation de fusion ou de déploiement dans cette étape.
Base Git : `3b95649744ec011143aaf2e583edca35fb82275e`.
Branche locale : `security/p1-auth-rate-limit-20261112`.
La qualification a été réalisée sans commit, push, accès aux données de
production, migration de production ou déploiement. Après ce bilan, l'exploitant
a autorisé le commit ciblé, le push de cette branche et l'ouverture d'une PR,
sous réserve de l'absence de régression identifiée. Les changements intentionnels
de quotas sont explicités ci-dessous. La fusion et le déploiement ne sont pas
inclus dans cette étape ; la production reste inchangée.

## Périmètre et comportement

Les quatre routes publiques API utilisent désormais un limiteur stocké dans la
base Django existante, sans Redis, cache local ni nouveau service payant.
Les limitations Web/admin ne sont pas modifiées. Ce lot ne constitue donc pas
un budget unique commun à tous les canaux d'authentification.

| Opération | Budget IP | Budget compte | Identité du compte |
| --- | --- | --- | --- |
| Login | `auth`, 10/min par défaut | Même taux, partagé entre IP | Nom d'utilisateur normalisé, sans conversion de casse |
| Refresh | `auth`, 10/min par défaut | Même taux, partagé entre IP | ID extrait seulement après vérification cryptographique et du type refresh |
| Demande de réinitialisation | `password_reset`, 10/h ; plafond IP historique supplémentaire, 10/h par défaut | `PASSWORD_RESET_LIMIT`, 5/h par défaut | Adresse électronique normalisée, existante ou non |
| Confirmation de réinitialisation | `password_reset`, 10/h par défaut | Même taux, partagé entre IP | UID décodé et ID entier canonique, sans recherche utilisateur |

Les opérations ont des espaces de compteurs distincts : login, refresh, demande
et confirmation. Le login et le refresh partageaient auparavant le scope cache
`auth` ; leurs budgets sont maintenant indépendants. Les variables de taux API
existantes restent utilisées. Les plafonds Web d'échecs de connexion ne sont pas
réutilisés pour ces budgets API : toutes les tentatives API, y compris réussies,
consomment une place.

Le budget IP est réservé avant lecture du corps JSON, vérification du mot de
passe, validation du JWT ou création d'un compteur de compte. Un refus du compte
consomme quand même le budget IP. Une IP épuisée ne peut pas créer de nouveaux
compteurs en variant les noms de compte. Une IP absente/invalide utilise un
budget commun, jamais une désactivation. Le choix de l'IP conserve le helper
existant : adresse distante, ou uniquement la valeur X-Forwarded-For la plus à
droite lorsque la confiance au proxy est explicitement activée.

Les identités suivent les conversions des serializers : nombres JSON acceptés
comme noms d'utilisateur et espaces des champs texte. Le limiteur applique en
plus une normalisation NFKC conservatrice du nom, sans modifier le serializer
d'authentification. L'adresse électronique et les variantes d'UID sont également
normalisées. Aucun claim d'un
JWT non vérifié ni la valeur du jeton rotatif ne sert de clé de compte.
Les réinitialisations restent publiques et ignorent désormais un ancien header
Bearer ; le token de réinitialisation et les mots de passe restent validés par
les services existants. Cette exception ne s'applique à aucun endpoint métier.

Un quota technique épuisé produit 429 avec `Retry-After` correspondant à sa durée
restante. L'ancien contrôle AuditLog additionnel conserve son délai historique
égal à la fenêtre entière.
Une panne du stockage/nettoyage des compteurs ou une configuration invalide
produit un 503 générique `AUTH_RATE_LIMIT_UNAVAILABLE`, sans authentification ou
envoi de mail. Les valeurs absentes, nulles, non positives ou hors bornes ne
désactivent pas le limiteur. La vérification historique AuditLog du reset reste
additionnelle, notamment pour la compatibilité avec le Web ; une panne de cette
lecture produit également 503 avant envoi. Le journal login distingue désormais
les échecs ordinaires, les refus 429 et les indisponibilités 503.

## Stockage, concurrence et migration

La migration additive `core.0005_authratelimitbucket` crée uniquement une table
technique avec clé primaire HMAC-SHA256, compteur et expiration indexée. Aucune
relation utilisateur ni permission CRUD Django n'est ajoutée. Les nouvelles clés
ne contiennent ni IP brute, nom, adresse électronique, mot de passe ou JWT. Le
HMAC utilise le SECRET_KEY existant et un encodage structuré des trois composants
pour éviter les collisions de séparateurs. Aucune rotation n'est effectuée.
Une éventuelle rotation du SECRET_KEY change ces clés et réinitialise de fait
les budgets, en plus de ses effets habituels sur les sessions et éventuellement
les JWT lorsque leur clé de signature utilise ce même secret.
Les anciens journaux de sécurité ne sont pas réécrits par ce lot.

Chaque budget est consommé dans une transaction courte avec verrou PostgreSQL
de ligne et création protégée par l'unicité de la clé. L'UPDATE comporte aussi
un prédicat de quota et une incrémentation SQL, sans lecture/incrémentation en
Python. L'heure est reprise après acquisition du verrou. À expiration, une seule
tentative renouvelle la fenêtre ; les autres voient le nouveau compteur.
Les refus n'incrémentent pas le compteur épuisé et ne prolongent pas la fenêtre.
Les budgets IP et compte sont réservés séquentiellement, pas sous plusieurs
verrous simultanés.

Un nettoyage préalable supprime au plus 100 lignes expirées, en transaction
séparée, avec `skip_locked` et nouvelle vérification de l'expiration au DELETE.
Il ne vide jamais les budgets actifs. Le nettoyage est opportuniste : sans
trafic, des clés opaques expirées peuvent rester en base jusqu'à une demande
ultérieure. Aucun job de rétention ni ordonnanceur n'est ajouté.

Les compteurs cache DRF standards ne sont pas atomiques ; SQLite ne fournit pas
les verrous de lignes PostgreSQL. Le prédicat SQL protège aussi les admissions
séquentielles SQLite ; une erreur de verrouillage SQLite produit 503 et ne
bascule pas sur un cache. Références : [throttling DRF](https://www.django-rest-framework.org/api-guide/throttling/)
et [verrouillage Django](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update).

## Qualification isolée

Sept fichiers source/tests sont superposés à une archive propre de la base Git :
`apps/core/models.py`, `apps/core/auth_rate_limit.py`,
`apps/core/migrations/0005_authratelimitbucket.py`,
`apps/core/tests/test_auth_rate_limit.py`, `apps/api/authentication.py`,
`apps/api/throttling.py` et `apps/api/tests/test_shared_auth_throttling.py`.
Snapshot : `tmp/p0c-admin-tests-20261111/snapshot-d2753528c28747cb979ba10b0048ef8a`.
Le label de révision dans les rapports désigne la base, pas un commit contenant
déjà ce correctif. Les sept empreintes du snapshot ont été comparées à celles
du worktree avant les exécutions.

Les runners réutilisés désactivent dotenv, remplacent l'environnement par des
valeurs synthétiques, interdisent les imports du worktree et le réseau externe.
Les données sont fictives. Le PostgreSQL 18 neuf utilise seulement loopback,
avec identité du cluster et certificat TLS vérifiés, puis arrêt et suppression
du runtime. Aucun accès Render, Neon, Drive, archive réelle ou mot de passe réel.

| Contrôle | Résultat | Rapport sous `tmp/p0c-admin-tests-20261111/` |
| --- | --- | --- |
| Nouveaux tests SQLite | PASS : 53 cas comptés, 48 réussis, 5 sauts PostgreSQL | `sqlite_tests-1794471781229451900.json` |
| Nouveaux tests PostgreSQL 18 | PASS : 53 réussis, aucun saut, échec ou erreur | `postgresql_tests-1794471804494580100.json` |
| Régression API/comptes et limiteur PostgreSQL | PASS : 285 réussis, aucun saut, échec ou erreur | `postgresql_tests-1794471891354814300.json` |
| Concurrence commerce/inventaire PostgreSQL | PASS : 3 réussis, aucun saut, échec ou erreur | `postgresql_tests-1794472094864526600.json` |
| Suite complète SQLite | PASS : 900 cas comptés, 883 réussis, 17 sauts PostgreSQL, aucun échec/erreur | `sqlite_tests-1794471846726684400.json` |
| Cohérence migrations, configuration synthétique, statiques, OpenAPI et compilation | PASS, mêmes sources isolées | `sqlite_tests-1794471846726684400.json` |

Les 17 tests ignorés par SQLite ont tous un résultat individuel réussi dans les
deux rapports PostgreSQL de régression ci-dessus. Le contrôle `check --deploy`
signale uniquement l'avertissement `security.W021` (HSTS preload non activé) ;
ce choix existant n'est pas modifié. Les sept fichiers candidats correspondent
toujours aux empreintes du snapshot testé. Le présent bilan est le huitième
fichier du lot, sans changement de code après qualification.

Les cinq tests de concurrence utilisent chacun 12 threads avec connexions Django
distinctes : première création, compteur presque épuisé, expiration simultanée,
compte partagé entre IP et IP partagée entre comptes. Aucun dépassement n'a été
observé. Ce sont des tests multi-connexions PostgreSQL, pas un essai réel de
plusieurs processus Gunicorn ou une mesure de charge de production.
Les 35 tests API couvrent aussi la normalisation, les succès comptabilisés,
les headers proxy/Bearer, JSON invalide, JWT forgés/de mauvais type, erreurs DB,
configuration invalide et journalisation du véritable statut HTTP.

## Limites et conditions avant une éventuelle publication

- Web/admin restent sur leurs contrôles AuditLog historiques non atomiques et
  sans budget global de compte entre IP. Ne pas annoncer ces canaux corrigés.
- Un attaquant connaissant un identifiant peut épuiser le budget du compte et
  bloquer temporairement un utilisateur légitime ; les NAT partagent le budget
  IP. Pas de bannissement permanent ni de prolongation par les refus.
- Ce limiteur nécessite des requêtes BD et ne protège pas contre un DDoS réseau.
  Aucun débit/latence de production, proxy réel ou multiprocessus réel mesuré.
- Le risque d'énumération historique en cas de panne du fournisseur de mails
  (R-03) n'est pas résolu ; les réponses nominales connues/inconnues restent
  identiques dans les tests. Aucun changement d'idempotence financière.
- La sauvegarde automatisée GitHub en échec et les médias absents de la
  sauvegarde manuelle restent des sujets séparés, inchangés.
- Avant toute publication : accord de l'exploitant, revue des changements et
  configuration des taux positifs, PR/CI obligatoire puis suivi du déploiement.
  Le build existant applique les migrations : la table doit exister avant que
  le nouveau code serve les routes d'authentification, sinon celles-ci refusent
  en 503. La migration ne modifie aucune table métier existante. Un retour au
  code précédent peut conserver la table technique ; ne pas programmer son
  effacement automatique ni réinitialiser les budgets pendant une bascule.

Les cinq fichiers déjà modifiés de sauvegarde/configuration/workflows sont
hors périmètre ; leurs empreintes d'origine sont conservées. Aucun fichier
préexistant non suivi n'est incorporé à la qualification de ce lot.
