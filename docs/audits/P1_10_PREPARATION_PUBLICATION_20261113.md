# P1 — Suite locale et préparation de publication

Date : 13 novembre 2026. **Aucune publication ni modification de production. L’audit n’est pas clos.** Cette extension est préparée au-dessus du commit backend local `2282d693b4fdfecdca454b998758419a01900413` ; elle ne remplace pas les preuves et limites du rapport `P1_09_INTEGRATION_LOTS_2_5_20261112.md`.

## Mode-pont : désactiver la correction, pas la trace

`CLIENT_BALANCE_CORRECTIONS_ENABLED` est nouveau et vaut `False` par défaut. Le service, le Web et l’API refusent alors la correction de dette, y compris pour un superutilisateur. La garde s’exécute avant le registre d’idempotence : une ancienne réussite n’est pas rejouée comme une opération actuellement disponible. Aucun registre ou historique n’est effacé. Les vérifications de permission existantes restent nécessaires lorsque l’option est activée.

Le bouton de correction est masqué dans le mode fermé ; l’historique reste lisible selon les droits existants. Les créations de clients restent possibles, y compris avec leur dette initiale autorisée. **La journalisation initiale ne dépend jamais de cette option**, même après un retour au mode fermé. Les sauvegardes ordinaires de profil continuent d’exclure le solde.

| Mode sur le même arbre de code candidat | Corrections dédiées | Traces initiales et protections |
| --- | --- | --- |
| A : option explicitement `False` | Refusées, avant toute reprise enregistrée | Maintenues |
| B : option explicitement `True` | Disponibles avec droit dédié, clé, motif et contrôle du solde attendu | Maintenues |
| Retour A : option explicitement `False` | À nouveau refusées, sans supprimer les écritures B | Maintenues, y compris pour les nouveaux clients |

Ce scénario local est un **retour de configuration sur le même contrat de code et de schéma**, pas une attestation de deux versions distinctes déjà déployées. Il couvre la conservation du solde et des journaux après correction et paiement, l’annulation du paiement, la protection des profils périmés, l’historique, l’immutabilité et les suppressions protégées. Aucun downgrade ni migration supplémentaire n’est introduit. Le simple rollback d’un binaire ne prouve pas que la configuration Render reviendra à `False` : ce réglage doit être vérifié explicitement. Le candidat au plancher désigne l’arbre de **cette extension**, pas son parent `2282d69`, qui ne contient pas encore cette garde.

### Première bascule encore ouverte

Le code historique `1006fe9` demeure **interdit comme cible de retour après le premier journal**, initial ou correctif. Le nouveau mode A écrit déjà les traces initiales : le fait de fermer les corrections ne rend donc pas automatiquement sûr le passage progressif `1006fe9 → A`, ni un retour automatique de la plateforme après réception de trafic par A. La coordination de cette première bascule et l’attestation de l’arbre minimum autorisé restent à préparer avant publication. Ne pas prétendre que le mode fermé résout cette phase, ni inventer un arrêt des opérations métier sans accord.

## Qualification de l’extension

Snapshot propre : `tmp/p0c-admin-tests-20261111/snapshot-fdb8f5ca09cb4a9080050dea9c762c8b`. Il contient l’archive Git de `2282d69`, dix fichiers du lot superposés explicitement, et une fixture de diagnostic R03 temporaire. Manifeste : `source-manifest-bridge-fdb8f5ca09cb4a9080050dea9c762c8b.json`, dans le même dossier parent ; SHA-256 final `c9c1b9a164d33318f1dfc757dbfff6528d5b967fadcdae98704fe0c256f9fed6`. Les cinq modifications utilisateur exclues du lot précédent restent exclues et leurs empreintes sont conservées.

| Contrôle | Résultat local |
| --- | --- |
| Régression ciblée SQLite | 317 cas comptés : 294 réussis et 23 réservés à PostgreSQL |
| Même sélection PostgreSQL 18 | 317/317 réussis, aucun skip, échec ou erreur |
| Correspondance des exclusions SQLite | Les 23 noms exacts sont chacun retrouvés en succès PostgreSQL |
| Préflight distinct, sans exécution de tests | Migrations cohérentes, contrôles Django de déploiement, collecte statique, validation OpenAPI et compilation de 278 fichiers Python réussis |
| Intégrité | Dix sources conformes au snapshot ; migration `0024` et `build.sh` inchangés |

Il s’agit d’une sélection de régression, **pas d’une nouvelle exécution de la suite backend complète**. Les deux nombres 317 ne s’additionnent pas. Treize nouveaux tests de mode-pont et treize tests du diagnostic d’environnement sont inclus dans cette sélection. Six autres cas sont des diagnostics de la réserve R03, et non des preuves de clôture de sécurité. Le préflight n’exécute aucun test et conserve le seul avertissement préexistant `security.W021` relatif au préchargement HSTS.

Labels de la sélection : tout `apps.api.tests`, inventaire ordinaire (`test_inventory`), corrections de dette, mode-pont, permissions de dette des administrateurs historiques, garde de migrations, créances commerce, diagnostic d’environnement et fixture R03. Les suites complètes du commit précédent et la qualification mobile sont des preuves séparées sur leurs arbres respectifs, pas des exécutions implicitement renouvelées par ce rapport.

Preuves non versionnées sous `tmp/p0c-admin-tests-20261111/` :

- `sqlite_tests-1794558989416784700.json` et `.log` ;
- `postgresql_tests-1794559006169009500.json` et `.log` ;
- `postgres-parent-b5fcfd0a3aae47ea.json` : arrêt du cluster et suppression du runtime synthétique confirmés ;
- `sqlite_preflight_only-1794559458704413000.json` et `.log` : contrôles seuls.

Les tests utilisent uniquement des données synthétiques, des imports du snapshot, un environnement isolé sans dotenv et, pour PostgreSQL, un cluster neuf local avec identité et TLS épinglés. Aucun compte réel, email réel, sauvegarde réelle ou secret de production n’est utilisé.

## Concurrence entre processus de migration

Qualification supplémentaire **PASS, trois scénarios réels**, en 83,382 secondes. La vraie commande `deploy_migrate` est appelée dans les workers ; ni le verrou ni la commande ne sont remplacés par un mock. Chaque scénario commence avec une base synthétique neuve arrêtée à `inventory.0023`, et seule l’expansion `0024` approuvée est en attente. Un verrou DDL externe sur la table client vide retient le premier processus après acquisition de son verrou advisory, pendant que le second tente réellement ce même verrou.

| Scénario | Preuve observée |
| --- | --- |
| Libération normale | Le premier applique une expansion ; le second obtient ensuite le verrou et recalcule un plan vide |
| Concurrent expiré, puis nouvel essai | Le concurrent expire avant `migrate`, sans mutation validée ; le détenteur finit ; le nouvel essai recalcule un plan vide |
| Détenteur interrompu | Seul le processus Python détenu par le parent est terminé ; sa session serveur disparaît et le remplaçant applique l’expansion abandonnée |

Les deux premiers workers de chaque scénario ont des PID de processus distincts et des PID de sessions PostgreSQL distincts, observés dans `pg_stat_activity`. Les empreintes finales du catalogue et de l’historique confirment l’état final avec une seule expansion validée. L’absence de downgrade demandé repose sur l’appel de commande en avant et le contrôle du plan et de sa source, pas sur ces seules empreintes finales. Les onze workers créés au total, incluant les bootstraps et le contrôle d’identité, sont rattachés aux handles détenus et tous réapés. L’arrêt du cluster, l’intégrité des sources et la suppression du runtime synthétique sont confirmés.

Rapport final : `deploy-process-parent-20261113-ec7d76ae78c647e5.json`, SHA-256 `64af0192eeb00457574354aa9f8633e13111f46857733e5f6d4e7e05a5936e6c`. Harnais temporaire : `run_deploy_process_probe_20261113.py` (`647795b37a60b7ba1dc83fdb0a825f107e4fa856ebd1059bd135dc75c8889191`) et `run_deploy_process_worker_20261113.py` (`42e6aff77a8fe8f00ecee10c85f6f8a5cef2bfc6e48dc69e1aa9618776581462`). Il s’agit d’une preuve locale supplémentaire, pas d’un branchement du garde-fou au déploiement réel ou d’une recette Render.

Trois essais préparatoires refusés restent conservés (`71d1aac5021b5aa8`, `2522b33432f4ed62`, `99f2b5f6a7011c5e`) : validation ACL ne reconnaissant pas `OWNER RIGHTS`, représentation `str`/`Path` du certificat, puis PID d’un redirecteur venv différent du vrai worker. Ils ne sont pas les résultats finaux. Les PID observés au troisième essai ont été vérifiés sortis, sans orphelin restant. Le harnais final lit la DACL protégée et son propriétaire, sans changer les droits Windows ; [CPython 3.13.14 utilise bien OWNER RIGHTS pour le mode 0700](https://github.com/python/cpython/blob/v3.13.14/Modules/posixmodule.c#L5277-L5284). Les workers sont lancés par le Python réel avec `-I -B -S`, sans site global ni exécution de fichiers `.pth`, en chargeant explicitement les dépendances du seul venv approuvé.

Avant les scénarios, un smoke réel sans Django ni PostgreSQL vérifie le PID exact du worker, ses imports et son nettoyage : `deploy-process-parent-20261113-10a57fb804fa1eb1.json`, PASS. Le harnais final répète ce smoke avant tout `initdb`. Si un worker vivant ou son identité ne peut être attesté, il interdit le nettoyage destructif et conserve le runtime privé pour récupération ciblée.

## R03 : réserve confirmée, pas masquée

La fixture temporaire `test_reset_delivery_boundary_20261113.py` contrôle le transport par des barrières, sans attente artificielle destinée à uniformiser les temps. Dans chacun des six cas Web/API × succès/exception/timeout simulés, une demande pour un compte éligible entre dans le transport puis reste suspendue. Les demandes absentes et inactives terminent pendant cette suspension. Après libération, le statut, le corps et la destination publics restent identiques.

**Verdict : canal temporel structurel toujours présent.** Ces résultats ne sont pas des mesures de distribution des temps en production. L’envoi peut utiliser Django/SMTP ou Brevo HTTPS : dans les deux cas, l’appel actuel est synchrone. Aucun worker durable ni nouvelle infrastructure n’est configuré. Un délai fixe, un thread détaché, `on_commit` ou un changement de fournisseur ne sont pas présentés comme une résolution durable. Le choix d’exploitation de la livraison hors requête reste nécessaire.

## Diagnostic de configuration sans divulgation

Le nouveau script `scripts/check_migration_environment.py` inspecte uniquement l’environnement du processus. Il n’importe pas Django ou dotenv, ne lit aucun fichier de configuration et n’ouvre aucune connexion. Sa sortie contient seulement des classifications, booléens et codes fixes : aucun hôte, nom de base, identifiant, mot de passe, chemin de certificat ou paramètre brut.

- `REVIEW_REQUIRED`, code de sortie 0 : diagnostic produit ; **jamais une autorisation de déployer**.
- `BLOCKED`, code de sortie 2 : forme non prise en charge, pooler connu, option de routage connue ou argument refusé.
- `deployment_ready`, `direct_connection_verified`, `connection_options_verified`, `render_compute_plan_verified` et `rollback_floor_attested` restent toujours `False`.

La présence de `-pooler` signale une incompatibilité connue avec le verrou de session ; son absence ne prouve pas que la connexion est directe. Le diagnostic ne vérifie ni DNS, TLS, paramètres libpq, variables `PG*`, existence/accessibilité de la base ou formule Render. Il ne permet pas de simplement retirer `-pooler` d’une URL, d’inventer une connexion directe ou de passer le drapeau de confirmation du garde-fou sans vérification.

## Conditions toujours nécessaires avant publication

1. Formule **Compute Plan** réellement souscrite sur le service Render, non déduite de `render.yaml` ou d’une capture qui n’affiche pas sa valeur. Le pre-deploy est documenté pour les services payants ; aucun coût ni changement de formule n’est engagé ici : [phases de déploiement Render](https://render.com/docs/deploys).
2. Connexion de migration directe vérifiée sans divulgation de son URL, et raccordement explicite du garde-fou. `build.sh` reste inchangé : les preuves locales ne signifient pas que la commande protégée est utilisée en production.
3. Procédure de première bascule et plancher de retour de code attestés, avec coordination des mutations pendant la transition si nécessaire. Aucun retour automatique à `1006fe9` après journalisation.
4. Choix d’exploitation pour résoudre R03. Le déploiement de ces garde-fous ne clôt pas cette réserve ni les autres points hors lot de l’audit.

Le commit mobile local `53c7adb2dbf969e3ffa5dbd6a0ad30f4209a2879` reste distinct et inchangé. Aucun APK/AAB ni signature ou recette matérielle supplémentaire n’est produit dans cette reprise.
