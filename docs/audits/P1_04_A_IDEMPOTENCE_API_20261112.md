# P1-4A — Reprises API autorisées et modification d'achat idempotente

Date : 12 novembre 2026. Qualification locale réussie avant commit ou push,
sans accès production. Base : `b0e9f3aa0de9d9bdc6a1b5d20fca0b6817ddb802`.
Branche locale : `security/p1-idempotency-api-20261112`.
Le lot de limitation d'authentification déjà publié reste inchangé.
Après qualification, le propriétaire autorise le commit ciblé, le push de cette
branche et l'ouverture d'une PR. La fusion et le déploiement ne sont pas inclus
dans cet accord ; les contrôles GitHub seront suivis sans fusion automatique.

## Périmètre compatible BUS-02

Ce premier sous-lot ne clôture pas BUS-02. Les anciennes requêtes API sans
`Idempotency-Key` restent acceptées. Aucun changement Flutter, formulaire Web,
permission attribuée, calcul de montant, service de stock ou modèle/migration.
Le registre existant, son unicité `(user, operation, key)` et le format de
hachage sont conservés, notamment pour les enregistrements déjà créés.

Le helper `idempotent` accepte un contrôle de reprise optionnel. Ce contrôle
s'exécute seulement si la clé existe déjà, si le corps est identique et si la
première opération est terminée, avant restitution de sa réponse. Un refus
403/404 n'exécute pas la mutation, ne remplace pas la réponse enregistrée et
ne modifie pas le registre d'origine. Le contrôle intervient après sortie de
la transaction ayant reçu l'erreur d'unicité, pas dans une transaction cassée.
Les premières opérations suivent leurs serializers/services habituels.

| Reprise couverte | Contrôle actuel avant réponse mémorisée |
| --- | --- |
| Création de paiement | Paiement vivant puis droits de création sur sa vente/son achat et périmètre opérateur |
| Modification de vente | Existence de la vente et périmètre courant, permissions d'objet |
| Modification d'achat | Existence et périmètre de l'achat, permissions d'objet |
| Validation/clôture de chargement | Chargement encore visible à l'opérateur courant et permissions d'objet |

La politique des paiements est partagée avec le serializer existant :
`commerce.add_customerpayment` pour une vente, `commerce.change_purchase` pour
un achat, restriction de vente d'un autre opérateur dans le cas déjà prévu.
Aucun droit supplémentaire `view_sale` ou `view_purchase` n'est exigé pour
reprendre une création autorisée. La cible du paiement est relue en base,
plutôt que de faire autorité à partir du seul ancien payload. Pour les anciens
registres sans `resource_id`, l'ID du payload serializer brut sert de secours.
Sans cible fiable/existante, la réponse mémorisée n'est pas restituée.

Le contrôle ne revalide ni le solde restant ni l'état financier d'un paiement
déjà enregistré : un règlement intégral doit pouvoir être rejoué, sans nouvel
encaissement, bien que la facture soit désormais soldée. De même, les filtres
de liste transitoires comme `?status=draft` ne doivent pas rendre invalide la
reprise d'une validation qui a changé ce statut. Les contrôles d'autorisation
de `get_queryset()` et d'objet restent appliqués.

`PurchaseViewSet.update` utilise désormais `purchase.{pk}.update` avec la même
clé facultative que les autres opérations. PATCH conserve `partial=True`, PUT
son contrat complet. Le client Flutter envoyait déjà cette clé ; le serveur
l'ignorait sur cette opération. La reprise évite de remplacer une seconde fois
les lignes, mouvements et réponse. Deux corps différents sous la même clé
produisent le refus existant `IDEMPOTENCY_KEY_REUSED` (400), plutôt que d'ignorer
la clé. Une clé fournie mais malformée est également rejetée selon les règles
déjà appliquées aux autres opérations (8 à 128 caractères sûrs). Une opération
refusée n'est pas mémorisée comme réussie.

Références techniques : [permissions d'objet DRF](https://www.django-rest-framework.org/api-guide/permissions/#object-level-permissions)
et [transactions Django](https://docs.djangoproject.com/en/5.2/topics/db/transactions/#controlling-transactions-explicitly).

## Qualification isolée

Sources ciblées : `apps/api/idempotency.py`, `apps/api/views.py`,
`apps/api/serializers.py` et nouveau
`apps/api/tests/test_idempotency_replay_access.py`.
Le présent bilan est le cinquième fichier du sous-lot.
Snapshot de la base propre avec ces quatre seuls overlays :
`tmp/p0c-admin-tests-20261111/snapshot-5691e6a250344a089c130b72d5d1f8fc`.
Le label de révision des rapports désigne la base, pas un nouveau commit.

Les runners réutilisés désactivent dotenv, remplacent l'environnement par des
valeurs synthétiques et interdisent les imports du worktree ainsi que le réseau
externe. SQLite et PostgreSQL 18 jetable n'utilisent que des données fictives.
Le cluster PostgreSQL est neuf, loopback et TLS épinglé, puis arrêté/supprimé.
Aucun compte réel, secret, archive, opération ou accès production n'est utilisé.

Les quatre empreintes ont été comparées à celles du worktree avant exécution.
La revue indépendante des sources et des tests ne signale aucun blocage.
Aucun résultat de la release précédente n'est présenté comme une nouvelle
qualification de ce sous-lot.

| Contrôle | Résultat | Rapport sous `tmp/p0c-admin-tests-20261111/` |
| --- | --- | --- |
| Nouveaux tests SQLite | PASS : 23 cas comptés, 22 réussis et 1 saut PostgreSQL | `sqlite_tests-1794486370773157600.json` |
| Nouveaux tests PostgreSQL 18 | PASS : 23 réussis, aucun saut, échec ou erreur | `postgresql_tests-1794486386120825100.json` |
| Régression API/comptes et limiteur PostgreSQL | PASS : 308 réussis, aucun saut, échec ou erreur | `postgresql_tests-1794486448296716600.json` |
| Concurrence commerce/inventaire PostgreSQL | PASS : 3 réussis, aucun saut, échec ou erreur | `postgresql_tests-1794486590777920300.json` |
| Suite complète SQLite | PASS : 923 cas comptés, 905 réussis, 18 sauts PostgreSQL, aucun échec/erreur | `sqlite_tests-1794486433002358500.json` |
| Cohérence migrations, configuration synthétique, statiques, OpenAPI et compilation | PASS, mêmes sources isolées | `sqlite_tests-1794486433002358500.json` |

Les 18 sauts SQLite ont tous un résultat individuel réussi dans les deux rapports
PostgreSQL de régression. `check --deploy` conserve uniquement l'avertissement
HSTS preload existant `security.W021`, non modifié par ce lot. Aucune nouvelle
migration n'est nécessaire. Les quatre sources/test correspondent toujours au
snapshot qualifié, sans changement de code après qualification ; le présent
bilan est le seul autre fichier prévu dans ce sous-lot.

Le test concurrent PATCH achat emploie deux connexions PostgreSQL dont les PID
sont vérifiés distincts : une réponse initiale et une reprise, une seule écriture
de registre et un seul audit de modification, mêmes réponse/lignes/montants et
aucun deuxième remplacement de stock. Ce n'est ni un test de charge ni une
mesure de transactions de production. Les tests couvrent aussi PUT/PATCH
partiel, rollback après mutation puis correction sous la même clé, révocation,
cible supprimée, réaffectation et anciens registres sans `resource_id`.

## Résiduels et prochaine étape

- Les clés restent facultatives. Une requête sans clé peut toujours produire
  une seconde écriture valide : ne pas annoncer une idempotence obligatoire.
- Aucun contrôle de reprise universel n'est revendiqué pour toutes les routes :
  seules les cinq opérations listées sont durcies dans ce sous-lot. Le registre
  existant reste utilisé par les autres opérations qui le possédaient déjà.
- Le digest existant ne distingue pas PUT/PATCH ni les paramètres de requête ;
  le modifier nécessiterait une stratégie pour les anciens enregistrements.
- Les formulaires Web vente/achat ne sont pas couverts ici. Les paiements Web
  ont déjà un jeton facultatif, avec des défauts de reprise/périmètre à corriger.
  La transition des anciens formulaires ouverts ne doit pas être masquée.
- Flutter garde une clé par écran pour les formulaires vente/achat/paiement,
  mais l'allocation de créance et certaines actions de chargement recréent une
  clé à chaque tentative. Une réponse perdue n'est alors pas toujours reprise
  sous la même clé : un lot client reste nécessaire.
- Avant de rendre les clés obligatoires : inventorier les versions réellement
  installées et choisir une transition compatible. Aucun minimum de version,
  délai d'obsolescence ou nouveau refus d'ancien APK n'est décidé ici.
- Les éventuelles collisions de verrous entre flux métier distincts et la
  stratégie Render expand/contract restent des sujets INF-02/R-02 séparés.
- La sauvegarde programmée en échec reste une alerte distincte, inchangée.

Après ouverture de la PR et validation de sa CI obligatoire, la fusion et le
déploiement nécessiteront un accord distinct. Les cinq fichiers de sauvegarde,
configuration et workflows déjà modifiés avant ce travail sont conservés,
sans inclusion dans la qualification ni changement de leurs empreintes.
