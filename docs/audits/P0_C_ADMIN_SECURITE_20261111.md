# P0-C — Sécurisation de l’administration Django

Date : 11 novembre 2026. Références de l’audit global : SEC-01, SEC-02, BUS-01.

État avant publication : corrections P0-C validées localement ; limite du banc
PostgreSQL complet documentée ci-dessous. Ces tests n'ont effectué aucune
connexion à la production. La publication a ensuite été autorisée par
l'exploitant le 11 novembre 2026, sous réserve des contrôles de validation.

## Politique retenue

- La gestion des utilisateurs et groupes dans `/admin/` est réservée aux
  utilisateurs actifs, staff **et superusers**. Le rôle métier Administrateur et
  les permissions de modèle ne suffisent pas. Les refus s’appliquent aussi aux
  URL directes, changements du mot de passe d’un tiers et autocomplétions.
- Un staff conserve le changement de son propre mot de passe dans l’admin.
  Les deux chemins de changement de mot de passe enregistrent le mot de passe et
  incrémentent la version des JWT dans une même transaction. Les anciens jetons
  access et refresh sont alors refusés ; la validation Django, le contrôle CSRF
  et la conservation de la session courante lors du changement personnel restent
  assurés par Django. Les autres sessions utilisent son invalidation habituelle
  par empreinte du mot de passe.
- Une édition ordinaire de profil n’enregistre que les champs concrets éditables
  réellement modifiés. Elle ne peut pas rétablir un ancien mot de passe ou une
  ancienne version de jeton après une modification concurrente. Les relations
  M2M gardent le comportement Django habituel : pas de garantie supplémentaire
  contre les éditions concurrentes des groupes ou permissions.
- Les huit administrations commerce sont en lecture seule, **même pour un
  superuser** : ventes, lignes de vente, achats, lignes d’achat, paiements,
  règlements du solde initial, séquences de factures et de tickets. Les droits
  Django par modèle restent nécessaires pour consulter. Ajouts, modifications,
  suppressions, actions de masse, éditions de liste et inlines sont bloqués ; les
  hooks d’écriture directs lèvent également `PermissionDenied`.

Les écrans métier Web/API, leurs services transactionnels et leur délégation de
droits ne sont pas modifiés. Les autres modules de l’admin ne deviennent pas
automatiquement en lecture seule. Aucun modèle, migration ou dépendance ajouté.

La révocation ajoutée concerne les deux routes de mot de passe de Django admin,
pas une interception globale de `User.set_password()`, des écritures ORM ou de la
commande Django standard `changepassword`. Les scripts et commandes hors de ces
routes doivent gérer explicitement la révocation, idéalement dans la même
transaction. La commande du projet `reset_admin` l’effectue déjà.

## Fichiers du lot

- `apps/accounts/admin.py`
- `apps/accounts/admin_apps.py`
- `apps/accounts/admin_site.py`
- `apps/accounts/tests/test_admin_security.py`
- `apps/commerce/admin.py`
- `apps/commerce/tests/test_admin_security.py`
- `gestio_stock/settings.py` : remplacement de la seule entrée de configuration
  admin par `apps.accounts.admin_apps.SecureAdminConfig`.
- `.github/workflows/django-ci.yml` : correction de publication limitée au runner
  `ubuntu-24.04`, après l'échec de préparation Python décrit ci-dessous. Les
  modifications locales préexistantes de ce workflow ne sont pas publiées.
- Le présent compte rendu.

`SecureAdminConfig` conserve le nom et le label `django.contrib.admin` / `admin`,
ses migrations, ses ressources et l’autodécouverte. Le site personnalisé garde
la vue Django standard pour le changement personnel, avec un formulaire qui
ajoute la révocation des JWT. Références :
[site admin personnalisé](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/#overriding-the-default-admin-site),
[formulaires d’authentification](https://docs.djangoproject.com/en/5.2/topics/auth/default/#module-django.contrib.auth.forms).

## Validation isolée

Base de source : `56ec273ce66b6a8d2f5b5186a97984579cb1caf3`, extraite par
`git archive`, puis uniquement les sept fichiers Python du lot superposés.
Candidat final : `tmp/p0c-admin-tests-20261111/snapshot-166e427ce75b46e28331d772633d4adf`.

Environnement de dépendances verrouillées : Python 3.13, Django 5.2.17. Les tests
utilisent exclusivement des comptes, mots de passe, fichiers et bases fictifs.
Les mots de passe des fixtures utilisent MD5 pour accélérer les tests seulement ;
la configuration de hachage de l’application n’est pas modifiée.

Le lanceur dédié désactive dotenv, remplace l’environnement, interdit les imports
du worktree et les accès réseau/sous-processus des tests. Il force une base SQLite
jetable ou un cluster PostgreSQL 18 neuf, limité au loopback, authentifié et TLS
avec certificat épinglé, dont l’identité est vérifiée. Aucun serveur ERP démarré, média
réel, archive de sauvegarde, secret OAuth, e-mail externe ou service cloud utilisé.
Il s’agit de garde-fous du banc de test, pas d’une qualification de sandbox OS.

37 nouveaux tests : 28 comptes et mots de passe, 9 commerce avec de nombreux
sous-cas sur les huit modèles, les profils staff/superuser et les opérations
interdites. Les tests vérifient les réponses HTTP, les JWT, les sessions, les
transactions et l’absence de modification des données métier fictives.

Résultats sur le candidat final :

| Contrôle | Résultat |
| --- | --- |
| Nouveaux tests P0-C | 37 réussis sur SQLite et 37 sur PostgreSQL 18 |
| Suite complète SQLite | 801 cas : 789 réussis, 12 cas propres à PostgreSQL ignorés, aucun échec ni erreur |
| Suite complète PostgreSQL 18 | 801 cas : 800 réussis, 1 erreur de prérequis du banc, aucun test ignoré |
| Concurrence PostgreSQL existante | Les 12 cas ignorés sous SQLite ont réussi sous PostgreSQL |
| Migrations | `makemigrations --check --dry-run` réussi, aucune migration nécessaire |
| Vérifications Django de déploiement | Réussies sur configuration synthétique sécurisée ; pas une inspection de Render |
| Ressources et schéma API | `collectstatic`, validation OpenAPI et compilation des sources réussis |
| Sources et isolement | Imports uniquement depuis le snapshot ; snapshot inchangé avant/après |
| Nettoyage | Connexions fermées, cluster PostgreSQL arrêté, runtimes jetables supprimés |

La suite PostgreSQL complète **n’est pas déclarée entièrement verte**. Son unique
erreur concerne le test préexistant
`apps.core.tests.test_backups.BackupCommandTests.test_backup_command_creates_a_verified_snapshot_and_checksum` :
la commande attend `pg_dump`, que l’environnement volontairement réduit du banc
P0-C n’expose pas. Les sous-processus natifs restent interdits dans les tests.
Ce cas ne valide donc pas une sauvegarde PostgreSQL dans ce banc ; il n’établit
pas une absence de `pg_dump` sur Render ni une régression applicative. Le code de
sauvegarde et ce test sont ceux de la révision de base, hors modifications P0-C.
Les garde-fous n’ont pas été assouplis et aucun fichier utilisateur de sauvegarde
n’a été modifié pour obtenir artificiellement un résultat vert.

Preuves locales non secrètes dans `tmp/p0c-admin-tests-20261111/` :

- `sqlite_tests-1794400998721514900.json` et `.log` : résultat final SQLite.
- `postgresql_tests-1794401102432986300.json` et `.log` : résultat PostgreSQL complet,
  incluant les 37 succès P0-C et la limite explicite ci-dessus.
- `postgres-parent-9138924f4fc585f7.json` : identité du cluster neuf, arrêt et
  suppression du runtime confirmés malgré le code retour d’erreur du cas natif.
- Revue indépendante en lecture seule : les sept fichiers Python courants sont
  identiques par SHA-256 au candidat testé ; aucune modification de schéma.

Les cinq fichiers déjà modifiés avant ce lot (`.env.example`, les deux workflows
database-backup/django-ci, `apps/core/backup.py` et son test) ont conservé leurs
empreintes initiales. Les autres travaux non suivis n’ont pas été incorporés au
snapshot ni modifiés.

Historique conservé dans `tmp/p0c-admin-tests-20261111/` :

- Première suite complète SQLite : 801 tests, deux erreurs de fixtures nouvelles,
  aucun autre échec, 12 cas PostgreSQL ignorés. La création utilisait un mot de
  passe synthétique trop similaire au nom utilisateur ; le formulaire inchangé
  n’envoyait pas les champs cachés de date réellement transmis par le navigateur.
  Les fixtures ont été corrigées, sans changer les validateurs ni affaiblir les
  assertions.
- Premier lancement natif : délai d’initialisation du cluster fictif dépassé.
  Aucun serveur démarré ; arrêt confirmé et runtime dédié supprimé ensuite.
  Le délai et le diagnostic du lanceur ont été améliorés.
- Premier lancement PostgreSQL initialisé : garde du banc trop restrictive pour
  la connexion administrative Django `NAME=None` à `postgres`. Correction limitée
  à cet alias interne ; la vérification des véritables identifiants, base, hôte,
  port, certificat et répertoire du cluster demeure obligatoire. Cluster arrêté
  et runtime supprimé après cet essai.

## Contrôles de publication

La publication autorisée se limite aux neuf fichiers décrits ci-dessus. Envoyer
d'abord le candidat sur une branche `security/**`, couverte par la CI existante,
puis exiger une CI verte sur son commit exact avant promotion sur `main`, suivie
du contrôle du déploiement Render de ce même commit. Le présent compte rendu de
prépublication ne constitue pas une preuve que ce déploiement a déjà réussi.

Le premier candidat `65234eca48a83d19ef0db1d1d1e2aab48b903ea3` a déclenché
la [CI 43032303057](https://github.com/YoucefTahir-dev/appAlimGen/actions/runs/43032303057),
arrêtée avant installation des dépendances et tests : Python 3.12.8 n'est pas
disponible pour Ubuntu 26.04, désormais sélectionné par `ubuntu-latest`.
La correction épingle uniquement le runner de cette CI à `ubuntu-24.04`, en
conservant Python 3.12.8, les contrôles et les dépendances. C'est l'option de
transition [documentée par GitHub](https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration/).
Les empreintes des sept fichiers Python restent celles du candidat testé.

Avant la promotion, vérifier avec l’exploitant qu’un compte propriétaire
superuser est disponible, sans communiquer ses identifiants. Prévenir
les staff concernés : les opérations financières passent par les écrans métier,
plus par Django admin ; la gestion des comptes dans cet admin devient réservée
au superuser. Aucun changement automatique des comptes existants n’est prévu.

Après publication autorisée : exiger la CI verte sur le commit exact et un contrôle
des accès admin approprié. Les changements de mots de passe futurs révoqueront
les JWT ; ce lot n’effectue pas de révocation globale rétroactive. Aucune
restauration ni écriture financière de validation ne doit être faite en production.
