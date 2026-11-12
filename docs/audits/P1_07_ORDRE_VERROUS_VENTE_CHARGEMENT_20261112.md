# R-02 — Ordre des verrous ventes et chargements

Date : 12 novembre 2026. Reproduction baseline confirmée sur PostgreSQL isolé ;
correctif implémenté et qualifications PostgreSQL intégrée et SQLite complète
réussies. Aucun accès à une
base de production, changement de rôle réel, vente ou mouvement métier réel.

## Reproduction avant correction

Base : `1006fe978e86f88bbcbcb3f1db2f9a71209285b1`, archive Git intacte avec un seul
test-probe synthétique ajouté dans
`tmp/p0c-admin-tests-20261111/snapshot-64c2035a9422440c8ee5cc88c3cd7501`.

Le probe utilise les vrais services `create_sale()` et `close_loading_order()`
sur deux connexions PostgreSQL/PID distincts. Il met la clôture en pause après
ses verrous chargement/stock opérateur, avant son appel réel au stock dépôt. Il
démarre ensuite une création de vente et vérifie dans `pg_stat_activity` que
cette connexion attend réellement le chargement détenu par la clôture. Le
service de clôture est ensuite relâché pour demander son verrou produit.

L'ancien ordre `Product → LoadingOrder` de la création entre alors en cycle avec
`LoadingOrder → OperatorStock → Product` de la clôture. Le test exige un vrai
SQLSTATE PostgreSQL `40P01`, une transaction gagnante et un rollback cohérent de
la transaction perdante : il ne se contente pas de déduire un risque du code.

Résultat : **PASS, reproduction confirmée**, un test exécuté sans skip ; rapport
`postgresql_tests-1794508559370863100.json` et log associé sous
`tmp/p0c-admin-tests-20261111`. Le parent a confirmé l'arrêt du cluster synthétique
et la suppression de son runtime. Aucun incident ou taux de deadlock en
production n'a été observé ni mesuré dans ce travail.

## Correctif

- `commerce.services.create_sale()` prend le chargement actif avant les verrous
  produits et conditionnements. Le fallback dépôt existant après une clôture
  gagnante est conservé ; ce lot ne redéfinit pas le périmètre métier d'un
  opérateur sans chargement.
- Les écritures directes `SaleLine.save()`/`delete()` acquièrent les parents
  vente puis chargement avant la ligne et les produits. Le statut du chargement
  est relu sous verrou avant l'application du delta de stock, au lieu de dépendre
  d'une instance relationnelle potentiellement ancienne.
- La suppression de lignes via QuerySet acquiert aussi les parents d'abord,
  puis les lignes sélectionnées et les produits triés, pour ne pas réintroduire une inversion ligne/parent
  avec le QuerySet stock-aware générique.
- Une lecture initiale de `sale_id` n'est pas considérée comme immuable : après
  verrouillage de la ligne réelle, `save()`, `delete()` et la suppression QuerySet
  vérifient que son parent appartient toujours à l'ensemble déjà verrouillé.
  Une réaffectation concurrente vers une troisième vente provoque un refus de
  concurrence et un rollback avant tout delta, sans retry et sans rendre la
  relation immuable par une nouvelle règle métier.
- `update_sale()` verrouille d'emblée l'union des anciens et nouveaux produits
  triée par PK, car le remplacement restaure les anciennes lignes avant de
  consommer les nouvelles. La suppression complète d'une vente prend également
  les produits concernés dans un ordre stable.
- La suppression ORM de plusieurs ventes verrouille explicitement toutes les
  ventes par PK, puis tous leurs chargements par PK et l'union de leurs produits
  par PK, avant la première suppression. Elle n'utilise plus l'ordre implicite
  décroissant de date des ventes, incompatible avec l'ordre parent ascendant
  des suppressions multi-lignes. Le QuerySet stock-aware commun trie également
  explicitement ses objets par PK ; aucune règle de paiement n'est modifiée.
- `validate_loading_order()` borne le `FOR UPDATE` à la ligne chargement avec
  `of=('self',)` : le `User` joint pour lecture n'est plus verrouillé implicitement.
- Les QuerySets de suppression bornent également le verrou à `self`, même si
  l'appelant utilise un filtre JOIN ou `select_related()` sur Client. La lecture
  initiale des identités de lignes est un clone explicitement non verrouillant,
  même si l'appelant a chaîné `select_for_update()` ; les parents sont acquis
  avant tout verrou de ligne. Cela évite de créer accidentellement un ordre
  Sale → Client incompatible avec le sous-domaine de créances Client → Sale.

Les opérations Payment et OpeningBalancePayment, calculs de tarifs, montants,
comptabilité, permissions et états métier ne sont pas modifiés. Aucune migration,
nouvelle dépendance, configuration de production, relance aveugle de transaction
ou interception générale des erreurs de base n'est ajoutée.

## Régressions à qualifier

Snapshot intermédiaire du correctif, avant le bornage des verrous des JOIN :
`tmp/p0c-admin-tests-20261111/snapshot-221feea5d9a34a7a91c07a7b6da3465e`,
même base avec quatre overlays seulement : modèles/services commerce, services
inventory, nouveau `commerce.tests.test_loading_lock_order`.

Ce module ajoute quatre tests SQLite/PG de remplacement produits, de suppression
stock-aware, de garde sur parent changé et de préverrouillage multi-ventes, puis
six courses PostgreSQL avec PID distincts et attente réelle :
clôture gagnante face à création, modification service, sauvegarde directe de
ligne, suppression directe de ligne et suppression QuerySet ; vente gagnante
face à clôture. Les assertions contrôlent stock dépôt/opérateur, rattachement
de vente, quantité de ligne, absence de paiement supplémentaire et cohérence de
la clôture. Un refus métier après clôture est distingué d'une erreur SQL.

La version finale ajoute un septième cas PostgreSQL : une connexion détient
Client en `FOR UPDATE`, pendant qu'une autre doit terminer une suppression de
Sale ou SaleLine via un QuerySet filtré/joint au Client, sans attendre ce verrou
Client. Le cas ligne inclut aussi un `select_for_update()` chaîné par l'appelant.
Le module final comporte donc onze tests (quatre unitaires et sept PostgreSQL).

Les SHA-256 du gel final sont :

| Source | SHA-256 |
| --- | --- |
| `apps/commerce/models.py` | `5036502fd236429cfb2dc38f1fbb8cb3e244a34c3e53b90f1a1e185bf89b47b3` |
| `apps/commerce/services.py` | `0635cc23bc2eaee4a64076d5c39007548f2a491950d82c04f2f3d7119937b0d9` |
| `apps/inventory/services.py` | `0793b0ea5e154fbc83c6a9db2dfaacb16861e7d29f16c793f52c1a781c5781da` |
| `apps/commerce/tests/test_loading_lock_order.py` | `ec386d35e516a9d5b799ac1fbb0ba021c52ebd68ad5ac799f5450b8f733d3a74` |

Avant ce dernier bornage JOIN, les régressions intermédiaires ont réussi :
53/53 PostgreSQL (`postgresql_tests-1794510050988555500.json`) et dix cas SQLite
comptés, quatre réussis et six skips PostgreSQL
(`sqlite_tests-1794510008789491200.json`). Ces résultats ne sont pas présentés
comme la qualification de la source finale : cette dernière est intégrée à la
qualification combinée des lots 2 à 5 et doit couvrir le nouveau cas JOIN.

Le snapshot combiné final est
`tmp/p0c-admin-tests-20261111/snapshot-698c89e16ef340b38ec57669386b8833`
(base `1006fe9` et 42 overlays explicitement sélectionnés). Les quatre empreintes
R-02 ci-dessus ont été vérifiées identiques par l'agent de qualification avant
son lancement. La qualification PostgreSQL intégrée a réussi : **743/743 tests,
sans skip**, rapport `postgresql_tests-1794510820042142900.json` et log associé.
Le log confirme individuellement les onze tests R-02 (quatre tests communs et
sept tests PostgreSQL), dont les cas JOIN Client et préverrouillage multi-ventes,
tous terminés par `ok`. Le nettoyage du cluster synthétique est également PASS.
La suite SQLite complète a également réussi : **1 097 cas comptés, 1 061 réussis
et 36 skips PostgreSQL**, rapport `sqlite_tests-1794510670802399900.json` et log
associé. Les 36 tests ignorés sur SQLite ont chacun été vérifiés réussis dans la
qualification PostgreSQL intégrée ci-dessus.

Le correctif traite le graphe testé ventes/chargements. Il ne constitue pas une
preuve d'absence de tout deadlock possible entre toutes les opérations futures,
des écritures SQL hors services ou d'autres sous-domaines. PostgreSQL reste le
moteur de preuve des verrous ; des tests SQLite seuls ne suffisent pas.

Référence primaire : [verrous et deadlocks PostgreSQL 18](https://www.postgresql.org/docs/18/explicit-locking.html).
