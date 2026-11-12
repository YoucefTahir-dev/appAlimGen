# P1-4B — Reprises des formulaires Web ventes et achats

Date : 12 novembre 2026. Base :
`16afc6de7d132f09a9b8af13963e3dad761b15f9`.
Branche locale : `security/p1-idempotency-web-20261112`.
Qualification locale complète réussie avant commit ou push, sans accès production.
La préparation du commit ciblé et de la PR suit cette qualification ; aucune
fusion automatique ni publication en production n'est effectuée à cette étape.
Le sous-lot API P1-4A déjà déployé n'est pas modifié.

## Périmètre

Ce sous-lot couvre uniquement les quatre formulaires Web de création et de
modification de vente et d'achat. Le règlement automatique saisi dans le
formulaire de vente fait partie de la même opération transactionnelle.
Les formulaires de paiement direct et d'affectation de créance, leurs anciens
jetons, Flutter, les services de calcul/stock, les modèles et les migrations
restent inchangés. Aucune permission de rôle n'est ajoutée ou retirée.

Le nouveau helper `apps/commerce/web_idempotency.py` réutilise la table
`IdempotencyRecord` existante, dans un espace d'opérations distinct de l'API :

| Formulaire | Opération |
| --- | --- |
| Nouvelle vente | `web.sale.create` |
| Modification de vente | `web.sale.{pk}.update` |
| Nouvel achat | `web.purchase.create` |
| Modification d'achat | `web.purchase.{pk}.update` |

Chaque nouvelle page possède un champ caché `request_token`, signé et horodaté
avec `TimestampSigner`, un sel dédié et une intention aléatoire. Ce jeton ne
remplace ni la session, ni CSRF, ni les contrôles de permissions. Sa durée de
validité est de douze heures depuis son émission. La purge existante du registre
conserve au minimum un jour : un ancien jeton expirera avant qu'une purge normale
puisse supprimer sa réservation. Un jeton expiré est refusé avant recherche ou
création de registre, même si son ancien résultat existe encore ou a été purgé.
Le message invite à vérifier si l'opération est déjà enregistrée, puis à rouvrir
le formulaire ; il ne réémet pas automatiquement une nouvelle intention.

## Reprise et compatibilité

Les formulaires déjà ouverts sans jeton, ou avec un jeton vide, restent acceptés
selon le contrat historique. Ils ne bénéficient pas de la nouvelle garantie.
Une page d'erreur de validation leur ajoute un jeton pour le prochain POST,
en copiant les données du formulaire sans modifier `request.POST`.
Une erreur sur un formulaire signé conserve son jeton pour correction.

Pour un jeton signé, l'unicité du registre reste `(utilisateur, opération, clé)`.
L'empreinte porte sur toutes les valeurs du POST, triées par nom de champ, avec
conservation de l'ordre et de toutes les valeurs d'un champ répété. Seuls le
jeton et CSRF sont exclus ; le renouvellement de CSRF n'invalide pas une reprise.
Une clé dupliquée dans le POST, une signature invalide ou une intention expirée
sont refusées explicitement. Une même clé avec un contenu différent produit un
refus 400 ; une réservation incomplète, un refus 409.

L'insertion du registre arbitre les requêtes concurrentes dans une transaction
qui englobe la mutation et sa réponse. La capture d'`IntegrityError` se limite à
cette insertion sous savepoint, pas aux contraintes métier du callback.
Une exception, ou une réponse de validation hors 302/303, annule la réservation
et toutes les écritures de cette opération. Le même jeton peut ensuite servir
à corriger une tentative qui n'a pas été enregistrée.

Seules les redirections réussies sont mémorisées : statut, chemin local et ID de
la ressource. Aucun corps de facture, montant ou formulaire financier n'est
copié dans la réponse mémorisée. Une réponse réussie doit identifier sa ressource,
sinon la transaction est annulée. La reprise restitue la redirection sans
réexécuter les formulaires, le stock, les lignes ou le règlement automatique.
Elle intervient donc avant les validations de solde, de stock ou de référence
unique qui peuvent légitimement ne plus être satisfaites après le premier succès.

Les permissions existantes du middleware et des décorateurs sont réévaluées
à chaque POST. La reprise contrôle en plus l'existence de la ressource et le
périmètre courant `visible_sales()` pour une vente, sans exiger un nouveau droit
de lecture. Une révocation de droit ou une vente devenue invisible ne restitue
pas l'ancienne réponse. Les modifications sont liées au document de leur route.

Le middleware de sécurité n'annonce pas une deuxième création/modification de
vente pour une reprise réussie. Ce n'est pas une promesse de « zéro écriture en
base » : les sessions Web, journaux techniques et séquences PostgreSQL peuvent
encore évoluer. La garantie concerne l'absence de seconde mutation métier,
de nouvelle ligne de registre et de nouvel audit de mutation pour la reprise.

Références techniques : [signature horodatée Django](https://docs.djangoproject.com/en/5.2/topics/signing/#verifying-timestamped-values)
et [transactions et savepoints Django](https://docs.djangoproject.com/en/5.2/topics/db/transactions/#controlling-transactions-explicitly).

## Qualification isolée

Sept fichiers source/test sont figés ; le présent bilan constitue le huitième
fichier du sous-lot. Snapshot de la base Git propre avec ces seuls overlays :
`tmp/p0c-admin-tests-20261111/snapshot-b85133bba93142dfa6be4b468d209cfb`.
Le label de révision des rapports désigne la base, pas un nouveau commit.

Les runners existants désactivent dotenv, interdisent les imports du worktree,
remplacent l'environnement par des valeurs synthétiques et bloquent tout réseau
externe. Les seules connexions PostgreSQL autorisées concernent un cluster neuf,
loopback et TLS épinglé, supprimé après arrêt confirmé. Aucun secret, compte réel,
archive téléchargée, média ou accès production n'est utilisé.

La revue indépendante des sept fichiers ne signale aucun blocage. Les nouveaux
tests couvrent notamment les quatre opérations, le stock et les règlements
automatiques, l'audit, les corps multivaleurs, CSRF, l'expiration avant/après purge,
la correction après échec, le rollback après écriture et les droits actuels.
Le test concurrent utilise deux connexions PostgreSQL aux PID vérifiés distincts
et exige une vente, un mouvement, un règlement et un audit de création, avec une
réponse initiale et une reprise. Il ne constitue pas un test de charge.

| Contrôle | Résultat | Rapport sous `tmp/p0c-admin-tests-20261111/` |
| --- | --- | --- |
| Nouveaux tests SQLite | PASS : 35 cas comptés, 34 réussis, 1 saut PostgreSQL | `sqlite_tests-1794492040908507900.json` |
| Nouveaux tests PostgreSQL 18 | PASS : 35 réussis, aucun saut/échec/erreur | `postgresql_tests-1794492046060270500.json` |
| Suite complète SQLite | PASS : 958 cas comptés, 939 réussis, 19 sauts PostgreSQL, aucun échec/erreur | `sqlite_tests-1794492142680937300.json` |
| Cohérence migrations, configuration synthétique, statiques, OpenAPI et compilation | PASS, mêmes sources isolées | `sqlite_tests-1794492142680937300.json` |
| Régression API/comptes/limiteur, Web et concurrences PostgreSQL | PASS : 346 réussis, aucun saut/échec/erreur | `postgresql_tests-1794492150776922300.json` |

Les 19 sauts SQLite ont chacun leur résultat individuel réussi dans le log de
régression PostgreSQL. Les clusters des deux passages PostgreSQL ont été arrêtés
et leurs répertoires temporaires supprimés, selon leurs rapports parents.
`check --deploy` conserve uniquement l'avertissement HSTS preload existant
`security.W021`. Aucune nouvelle migration n'est nécessaire. Les sept empreintes
correspondent toujours au snapshot qualifié, sans changement de code après ces
exécutions ; seul le présent bilan est finalisé ensuite.

La régression préliminaire des 105 tests commerce existants a également réussi
(103 réussis, deux sauts PostgreSQL), sur un premier snapshot propre avec les
six mêmes sources, avant ajout du nouveau fichier de tests. Son résultat n'est
pas présenté comme la qualification finale du snapshot à sept fichiers.

## Limites et suite

- Les anciens POST sans jeton restent non protégés : pas d'annonce d'obligation
  universelle ni de clôture complète de BUS-02.
- Un formulaire signé ouvert depuis plus de douze heures doit être revérifié
  puis rouvert. Deux pages distinctes correspondent à deux intentions distinctes.
  Aucun rapprochement heuristique de transactions identiques n'est ajouté.
- Les formulaires de paiements directs et de créances gardent leur mécanisme
  historique ; leur reprise et leur liaison acteur/cible/contenu restent un lot
  distinct nécessitant de préserver les anciennes clés.
- Certaines actions Flutter doivent encore conserver leur clé lors d'une
  nouvelle tentative. Les clés API restent facultatives.
- La sauvegarde programmée en échec reste une alerte séparée, inchangée.

Les cinq fichiers de configuration, sauvegarde et workflows déjà modifiés avant
ce travail sont conservés et exclus du snapshot et du périmètre de publication.
La PR doit ensuite obtenir sa CI obligatoire. Sa fusion et le suivi du déploiement
feront l'objet de l'étape de publication suivante, sans contournement des
protections de `main` et sans opération financière de test en production.
