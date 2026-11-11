# P1-2 — Permissions API explicites (SEC-03/04)

Date : 11 novembre 2026. Travail local autorisé par l'exploitant après P1-1.
Base : `106a1b0a5bb75eb446b5d65dcfcafca27162246a`. La qualification locale
a été réalisée sans push, déploiement, accès aux données de production,
changement de rôle réel ou migration.
L'exploitant a ensuite autorisé le commit et le push ciblés après vérification.
Branche de livraison : `security/p1-api-permissions-20261111` ; publication par PR
et CI obligatoire. La fusion vers `main` reste conditionnée à la confirmation
des droits opérateurs attendus, décrite ci-dessous ; aucune attribution de droit
en production n'est incluse dans cette autorisation de commit/push.

## Changements

`BusinessPermission` refuse désormais une action absente ou une déclaration
malformée, y compris pour un superutilisateur. Aucun fallback `*` ; les anciennes
règles génériques des références, créances et stocks deviennent des actions
nommées. Les noms de permissions et d'actions sont validés avant consultation.

Les lectures des chargements et du stock opérateur nécessitent au moins l'un de
ces droits : `inventory.view_loadingorder` ou
`inventory.view_all_loadingorders`. Cette combinaison correspond au service de
permissions du client Flutter existant. Le premier droit conserve les filtres
sur l'opérateur courant ; le second conserve la consultation globale et le
filtre opérateur. `accounts.view_stock` concerne le dépôt et ne donne pas à lui
seul accès au module des chargements. Les refus individuels existants restent
pris en compte. Aucun droit de mutation n'est ajouté par ces lectures.

Les comptes sans ces deux droits recevront intentionnellement un refus 403 sur
ces lectures, même si un ancien client les appelait auparavant. Avant publication,
l'exploitant devra vérifier les droits attendus des rôles opérateurs ; aucune
attribution automatique ni extension du rôle vendeur historique n'a été faite.

Le décorateur des mouvements opérateur contenait `methods=('get')`, une chaîne
au lieu du tuple `('get',)` : la route GET attendue n'était pas correctement
exposée. Cette faute est corrigée, sous la nouvelle permission explicite.
L'inventaire de tests vérifie aussi la validité des verbes HTTP routés.

## Exceptions explicites et compatibilité

Les déclarations vides sont limitées et inventoriées dans les tests :

- compte courant, déconnexion et changement de mot de passe : authentification
  requise, gardes de compte actif/changement forcé inchangées ;
- racine de l'API : liste de liens pour un compte authentifié, pas d'accès aux
  ressources lié à ces liens ;
- recherche produit : droits d'ajout/modification du contexte vente, achat ou
  chargement vérifiés par le service existant ; contrôle effectué aussi avant
  résolution du client fourni en paramètre ;
- paiements : lecture filtrée par les droits vente/achat, création et annulation
  contrôlées selon la cible précise par le sérialiseur ou le handler existant ;
- OPTIONS/métadonnées : introspection authentifiée explicite. Les requêtes
  PUT/POST clonées par DRF pour produire ces métadonnées doivent passer la vraie
  règle de création/modification et les filtres d'objet ; la règle OPTIONS vide
  n'est pas réutilisée pour ces opérations.

HEAD réutilise la permission GET (ou l'action résolue par le routeur) ; une règle
HEAD explicitement déclarée prime GET. Login, refresh et réinitialisation
conservent leur politique `AllowAny` explicite et leurs contrôles internes.
Schéma et documentation conservent `IsAdminUser` ; ce lot ne redéfinit pas leur
politique d'administration.

## Qualification isolée

Sept fichiers source/tests ciblés, sans les modifications préexistantes de
sauvegarde ou de workflows, sont superposés à une archive propre de la base Git.
Fichiers : `apps/api/permissions.py`, `apps/api/views.py`,
`apps/api/authentication.py`, `apps/api/urls.py`,
`apps/api/tests/test_business_permissions.py`,
`apps/api/tests/test_permission_routes.py` et
`apps/api/tests/test_loading_and_idempotency.py`.
Snapshot final : `tmp/p0c-admin-tests-20261111/snapshot-d089e44fb40741c3a650461a95d0fd7b`.
Les sept empreintes ont été comparées à celles du worktree avant exécution.
Cette égalité a été reconfirmée après la qualification finale ; le présent bilan
documentaire est le seul fichier supplémentaire prévu pour la livraison ciblée.

Les runners dédiés désactivent dotenv, remplacent l'environnement par des valeurs
synthétiques, refusent les imports du worktree et les accès réseau/native dans
l'enfant. SQLite et le cluster PostgreSQL 18 neuf utilisent uniquement des
utilisateurs et données fictifs ; PostgreSQL est sur loopback avec TLS épinglé.
Les fichiers de rapport ne contiennent ni phrase de chiffrement ni donnée ERP.

Résultats sur le snapshot final :

| Contrôle | Résultat | Rapport local sous `tmp/p0c-admin-tests-20261111/` |
| --- | --- | --- |
| Nouveaux tests de permissions, SQLite | PASS : 46 exécutés, aucun échec/erreur/saut | `sqlite_tests-1794425261195592800.json` |
| API et comptes, PostgreSQL 18 | PASS : 232 exécutés, aucun échec/erreur/saut ; concurrence des paiements incluse | `postgresql_tests-1794425519149393100.json` |
| Concurrence commerce et inventaire, PostgreSQL 18 | PASS : 3 exécutés, aucun échec/erreur/saut | `postgresql_tests-1794425871549887800.json` |
| Suite complète SQLite | PASS : 847 cas comptés, 835 réussis, 12 sauts spécifiques PostgreSQL, aucun échec/erreur | `sqlite_tests-1794425573012944100.json` |
| Cohérence migrations, configuration de déploiement synthétique, statiques, OpenAPI et compilation | PASS, mêmes sources isolées | `sqlite_tests-1794425573012944100.json` |

Les neuf tests de concurrence/locking API et comptes ignorés par SQLite ont bien
été exécutés dans les 232 tests PostgreSQL. Les trois tests restants de concurrence
commerce/inventaire ont été exécutés avec succès dans un autre cluster jetable.
Les douze sauts SQLite sont ainsi couverts sur PostgreSQL pour ce candidat ; il
ne s'agit pas pour autant d'une exécution intégrale des 847 cas sur PostgreSQL.

Les deux parents PostgreSQL finaux confirment l'arrêt des clusters et la suppression
de leurs runtimes synthétiques. Les rapports finaux confirment aussi les imports limités au snapshot
et l'absence de modification de ce dernier. Le label de révision des rapports
désigne la base Git et non le commit de livraison : les sources qualifiées sont
cette base avec les sept overlays décrits ci-dessus. Leur contenu est inchangé
pour la préparation du commit, hormis le présent bilan documentaire.

La première recette a correctement échoué : route GET des mouvements malformée,
deux attentes erronées des fixtures de chargement (le service retourne
`in_progress`, pas `validated`) et un timeout de connexion au cluster PostgreSQL
local lors d'un test concurrent. Ces rapports sont conservés ; les droits ne sont
pas assouplis pour les faire passer. Les fixtures comparent maintenant les états
avant/après pour vérifier l'absence de mutation.

Historique non final conservé : `sqlite_tests-1794424689743070200.json`,
`postgresql_tests-1794424712164057700.json` et
`sqlite_tests-1794424840760510000.json`. Les résultats PASS ci-dessus portent sur
le snapshot final corrigé, pas sur ces exécutions en échec.

## Limites et suite de publication

- Aucun changement de règle métier globale sur les vendeurs sans chargement ni
  de filtre Web. `loading_order_list` Web conserve sa politique préexistante.
  Les autres filtres de ventes/factures restent conditionnels au statut
  d'opérateur existant : ce n'est pas une isolation multi-tenant universelle.
- Les paiements conservent un résiduel préexistant : le replay idempotent d'une
  création peut retourner le résultat antérieur du même utilisateur sans repasser
  le contrôle contextuel du sérialiseur après révocation d'un droit. Ce lot ne
  prétend pas corriger ce cas ; à traiter dans le lot idempotence BUS-02 avec les
  tests de révocation/reprise. Aucune nouvelle écriture n'est réalisée par ce replay.
- Pas de build Flutter, d'artefact Windows ni de recette physique des clients.
- Les travaux de sauvegarde/workflows déjà présents sont exclus et préservés.
  Le job programmé de sauvegarde précédemment en échec n'est pas corrigé ici.
- Publication uniquement par une branche/PR ciblée et CI obligatoire conformément
  à P1-1, après accord de l'exploitant. Aucun contournement de la protection de
  `main`, aucun test ou changement de données en production implicitement autorisé.
