# P1 — Séparation locale des phases de déploiement

13 novembre 2026. **Qualification locale réussie ; publication non autorisée par ces seuls résultats. Aucun push, accès à la base de production ou changement Render.** Extension du commit local `4f6bafeec4331f80dab3812b3da7d77e4b83de46`. Le rapport P1_10 reste une preuve historique inchangée.

## Informations reçues

La capture utilisateur affiche le plan actif `0.5c-512mb`, 0,5 CPU, 512 Mo et 7 USD/mois. Le contrôle transmis affiche `POOLER = NON`. Cette dernière valeur signifie seulement qu'aucun marqueur connu n'a été détecté : elle ne vérifie ni le fournisseur, ni la configuration réellement chargée par Django, ni le routage ou la stabilité d'une session PostgreSQL.

Le pré-déploiement est disponible sur les services payants et précède le démarrage dans une instance séparée : [documentation Render](https://render.com/docs/deploys). Aucun changement de formule ou coût supplémentaire n'est engagé ici. `render.yaml` contient encore `plan: free` et aucun `preDeployCommand` ; cette divergence avec le tableau de bord reste à traiter explicitement avant publication, sans laisser un changement de Blueprint modifier implicitement l'abonnement.

## Contrat préparé, pas encore configuré sur Render

| Phase prévue | Commande | Comportement du candidat local |
| --- | --- | --- |
| Build | `bash build.sh` | Installation verrouillée, garde Drive et collecte statique ; suppression des appels de migration et de l'ancien bootstrap d'audit email |
| Pré-déploiement | `bash predeploy.sh` | Garde de configuration hors ligne, puis vraie commande `deploy_migrate` avec verrou PostgreSQL et plan approuvé |
| Démarrage | `bash start.sh` | Garde Drive, contrôle de schéma en lecture seule, puis Gunicorn seulement si aucune migration n'est en attente |

Le nouveau mode `deploy_migrate --check-applied` n'acquiert aucun verrou advisory et n'appelle jamais `migrate`. Il refuse un plan en attente ou non approuvé. Un pré-déploiement oublié ou incomplet ne déclenche donc aucune migration de secours au démarrage et n'autorise pas ce code à servir avec un schéma en retard.

Le pré-déploiement exige `MIGRATION_DIRECT_CONNECTION_CONFIRMED=true` ou `1`. C'est une **attestation opérateur après vérification**, pas un diagnostic automatique : ne pas la définir à partir du seul résultat `POOLER = NON`. Elle ne contourne ni les refus de syntaxe, ni les poolers connus, ni les options de routage détectées. Les variables de routage libpq connues et l'option URL `options` sont également refusées. Le rapport n'affiche jamais de valeurs de connexion ; ses champs `direct_connection_verified` et `deployment_ready` restent faux, même lorsque la garde de configuration passe.

La durée globale de migration est bornée de 1 à 900 secondes, 300 par défaut. Aucun bootstrap, `--fake`, downgrade, nouvel essai automatique ou suppression de données n'est ajouté. La seule expansion approuvée reste `inventory.0024_clientbalanceadjustment_and_client_permission`, inchangée, avec son empreinte source et son contrat d'opérations existants. L'ancien traitement des migrations email n'est pas remplacé par une approbation implicite : un historique plus ancien doit être examiné séparément.

**Ne pas enregistrer maintenant une commande de pré-déploiement pointant vers un fichier encore absent de la version publiée.** L'ordre de publication, la configuration réelle et la première bascule doivent être coordonnés avant toute action externe.

## Qualification de cet arbre

Snapshot : `tmp/p0c-admin-tests-20261111/snapshot-6569ed48d28a4a11a464622a7187f600`, archive propre de `4f6bafe` et exactement sept sources superposées. Aucun fichier utilisateur exclu, dotenv, secret, média ou sauvegarde réelle n'entre dans ce snapshot.

| Contrôle | Résultat |
| --- | --- |
| Scripts Bash, commandes remplacées par des stubs inertes | 12/12 ; ordre, refus, délais invalides, propagation des échecs et absence de serveur réel |
| Régression ciblée SQLite | 321 cas comptés : 303 réussis et 18 réservés à PostgreSQL |
| Même sélection PostgreSQL 18 | 321/321, aucun skip, échec ou erreur ; les 18 noms exclus sous SQLite sont retrouvés en succès |
| Préflight distinct | Migrations cohérentes, contrôles Django, collecte statique, OpenAPI et compilation de 277 fichiers Python ; zéro test exécuté |
| Vrais processus concurrents de migration | Trois scénarios réussis en 91,432 secondes ; onze handles de workers réapés, cluster arrêté et runtime supprimé |

Les trois scénarios utilisent la vraie commande et de vraies sessions distinctes sur une base synthétique arrêtée à `inventory.0023` : libération normale avec une seule expansion validée ; expiration d'un concurrent avant migration puis nouvel essai sans opération ; interruption du détenteur et reprise par son remplaçant après disparition de la session. Les preuves de catalogue, de graphe et de source sont vérifiées. Il ne s'agit pas d'une simulation de disponibilité Render ou d'une recette de la première bascule de production.

Les tests SQL utilisent uniquement un cluster local jetable PostgreSQL 18, TLS et identité épinglés ; les tests de scripts n'exécutent aucun vrai pip, enfant Python, Gunicorn ou serveur. Les sources sont vérifiées inchangées après les essais. Le seul avertissement de préflight est le préexistant `security.W021` sur le préchargement HSTS.

Cette sélection comprend l'API, l'inventaire ordinaire, les corrections de dette, le mode-pont, les permissions historiques de dette, les gardes de migration, les créances et le diagnostic d'environnement. **Ce n'est pas une nouvelle exécution de toute la suite backend.** Les 321 SQLite et PostgreSQL ne s'additionnent pas ; les anciennes qualifications complètes et mobile restent distinctes. La fixture temporaire de diagnostic R03 du rapport P1_10 n'est pas incluse ici, ce qui explique aussi le passage de 278 à 277 fichiers compilés.

## Preuves et intégrité

Manifeste scellé, sous `tmp/p0c-admin-tests-20261111/` : `source-manifest-phases-6569ed48d28a4a11a464622a7187f600.json`, SHA-256 `8a56e5a1b07765ffb0ecfec2100091326dabf6fe63bb6c64a0f6c62a0a4ad4d2`. Il contient les sept empreintes source, les cinq empreintes des modifications utilisateur conservées, la correspondance des exclusions et les empreintes des rapports et harnais.

Rapports principaux dans ce même dossier :

- `shell-flow-20261113-1794563859770693300.json` ;
- `sqlite_tests-1794563871443127600.json` et `postgresql_tests-1794563877957921600.json`, avec leurs logs ;
- `postgres-parent-e50ad5d947a316b5.json`, nettoyage confirmé ;
- `sqlite_preflight_only-1794563880102645700.json` et son log ;
- `deploy-process-parent-20261113-02c57d40b924b443.json`, SHA-256 `f26e281cdc47e16f7a83315c5dccb26f100b6609e1ef83c20e67ef3e3fe445a1`.

Les cinq modifications utilisateur initiales restent exclues du commit. `render.yaml`, la migration 0024 et le dépôt mobile ne sont pas modifiés. Aucun paramètre Render ou drapeau d'environnement réel n'est enregistré.

## Réserves avant publication et clôture

1. Vérifier réellement la connexion directe et ses paramètres sans divulguer les secrets ; préparer ensuite le raccordement Render/Blueprint des trois phases.
2. Attester la première bascule depuis `1006fe9` et le plancher de retour. Le mode fermé écrit toujours les journaux initiaux : il ne rend pas sûr un retour à l'ancien binaire après le premier journal. Aucun arrêt d'opérations métier ni rollback automatique n'est supposé autorisé.
3. Résoudre ou faire arbitrer explicitement R03 : le transport de réinitialisation de mot de passe reste synchrone. Aucun worker durable ni nouvelle infrastructure payante n'est ajouté ; la réserve temporelle reste ouverte.

Le succès local permet de conserver ce lot dans un commit local précis, **pas de déclarer l'audit clos ni d'effectuer le push conditionnel demandé comme si ces réserves étaient levées**.
