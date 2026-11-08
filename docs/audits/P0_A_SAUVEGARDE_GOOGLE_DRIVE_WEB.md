# P0-A — Option Web de sauvegarde chiffrée vers Google Drive

Date : 8 novembre 2026. État : **341 tests ciblés PASS ; cycle Drive réel fictif PostgreSQL 17 et cycle natif fictif PostgreSQL 18.6 Windows PASS, nettoyages PASS ; préparation CSP/visibilité/préflight Render ajoutée ; production non déployée et désactivée par défaut, Linux/Render non qualifiés**.

## Périmètre autorisé

Choix confirmé : **base de données uniquement — médias exclus**.

L'option exporte la base PostgreSQL utilisée par Django ; elle ne consulte ni ne copie les photos, logos, pièces jointes ou fichiers de stockage objet. Le bucket GCP exclu reste hors périmètre. L'archive peut contenir les références aux médias enregistrées dans les tables, mais pas les fichiers correspondants. Elle n'est donc pas une sauvegarde complète de l'ERP.

Ce lot ne touche ni aux calculs métier, ni aux modèles, ni aux migrations, ni aux workflows GitHub, ni à Android. Aucun export de production, accès Neon/GCP, changement de secret distant, commit, push ou déploiement n'a été effectué pour cette implémentation. L'accès Google autorisé comprend désormais OAuth/identité et les seuls fichiers fictifs d'essai, envoyés, récupérés et supprimés précisément ; aucun autre fichier personnel n'est consulté. Le dépôt indépendant de validation synthétique n'est pas modifié.

## Fonctionnement préparé

Menu **Sauvegardes**, page `/core/backups/`, action POST `/core/backups/create/` :

1. Permission `accounts.view_backups` pour la page ; permissions `accounts.view_backups` **et** `accounts.create_backups` pour la connexion, déconnexion et sauvegarde, via le RBAC existant.
2. CSRF, confirmation du mot de passe actuel et case obligatoire « base uniquement, sans médias ». Aucun champ ne permet de choisir une URL, une base, un dossier distant ou un exécutable.
3. Après cinq confirmations incorrectes sur quinze minutes, refus temporaire selon le mécanisme d'audit existant. Un GET ne lance aucun outil ni appel Drive.
4. Vérification des outils natifs, configuration PostgreSQL/TLS et version majeure ; transaction de contrôle en lecture seule et verrou consultatif transactionnel partagé entre les workers. Le dump utilise une autre connexion, en lecture seule, et son propre instantané cohérent PostgreSQL.
5. Compte Google choisi par l'utilisateur autorisé via OAuth `drive.file`. Identité affichée puis revérifiée avant de créer le dossier/dump ; dossier créé par l'application et vérifié privé (un seul propriétaire, aucun partage). Un dossier déjà partagé ou ambigu est refusé.
6. `pg_dump` au format custom et contrôle du catalogue avec `pg_restore --list`. Ce contrôle **n'est pas une restauration**.
7. Archive TAR contenant exclusivement `database.dump` et `manifest.json`. Le manifeste indique `database_only`, `media_included: false` et `restore_verified: false`.
8. Chiffrement GPG AES-256, dérivation S2K itérée, passphrase sur l'entrée standard, configuration GPG temporaire isolée. Déchiffrement en mémoire et comparaison SHA-256 avant tout envoi.
9. Seul le fichier `.tar.gpg` est envoyé. Métadonnées distantes relues : SHA-256, taille, dossier parent, marqueur applicatif et absence de partage.
10. Résultat minimal dans la session et journal d'événements sans mot de passe, jeton, contenu du dump ou message brut du fournisseur. Suppression des fichiers temporaires aux sorties normales et aux erreurs gérées.

Les domaines HTTPS du connecteur sont fixés dans le code : `oauth2.googleapis.com` et `www.googleapis.com`. Aucune redirection n'est suivie ; aucun endpoint S3/GCP ou paramètre de destination fourni par l'utilisateur n'est accepté.

### Assistant de connexion ajouté localement

Les routes `/core/backups/drive/connect/` et `/core/backups/drive/disconnect/` exigent POST, CSRF, double permission et mot de passe ERP. La première redirige vers la page officielle Google (`accounts.google.com`) pour choisir le compte ; elle ne lance pas d'archive. Le callback GET `/core/backups/drive/callback/` utilise un `state` aléatoire, PKCE S256, une expiration de dix minutes et une liaison au compte ERP/session/hash d'authentification/client OAuth. Le nonce est consommé dans une transaction validée avant l'échange Google ; une transaction externe est refusée.

Les jetons sont chiffrés avec Fernet et une clé serveur dédiée, dans un coffre distinct utilisant la table `django_session` existante. La session de navigation ne contient qu'un identifiant opaque. Les sessions par cookies signés ou cache local sont refusées. La suppression du coffre empêche une ancienne requête de rétablir les jetons après déconnexion/changement de compte. La connexion est propre à ce navigateur et limitée à la session, avec un plafond de huit heures ; aucune connexion globale partagée ou automatisation n'est créée.

Le formulaire d'archive est lié cryptographiquement à la connexion affichée : une page ancienne ouverte avant un changement de compte ne peut pas lancer silencieusement une sauvegarde sur le nouveau compte. L'interface Web ne reprend **jamais** `DRIVE_BACKUP_REFRESH_TOKEN` en l'absence d'une connexion utilisateur. Le helper historique sans configuration explicite reste disponible aux scripts opérateurs, pas comme repli de l'écran.

La destination de cette première version est le dossier privé fixe de l'application dans le compte choisi. **Le choix arbitraire d'un dossier ou d'un Drive partagé n'est pas implémenté.** Déconnecter est une suppression locale des accès futurs : cela ne supprime pas les archives, ne révoque pas le consentement Google et n'annule pas un export déjà autorisé.

## Configuration ultérieure — aucune valeur à communiquer dans le chat

Le bouton reste désactivé sans configuration complète. Variables attendues côté serveur uniquement :

| Variable | Utilisation |
| --- | --- |
| `DRIVE_BACKUP_ENABLED` | `false` par défaut ; activation explicite ultérieure |
| `DRIVE_BACKUP_OAUTH_ENABLED` | `false` par défaut ; assistant Web, obligatoire pour exporter depuis la page |
| `DRIVE_BACKUP_CLIENT_ID` | Client OAuth Google dédié |
| `DRIVE_BACKUP_CLIENT_SECRET` | Secret de ce client, jamais dans Git |
| `DRIVE_BACKUP_OAUTH_REDIRECT_URI` | URI de retour exacte, HTTPS en production, chemin `/core/backups/drive/callback/` ; HTTP loopback admis en DEBUG, ou à l'adresse exacte du banc de connexion seule ci-dessous avec DEBUG désactivé |
| `DRIVE_BACKUP_TOKEN_KEY` | Clé Fernet aléatoire dédiée, distincte de `SECRET_KEY` et de la phrase d'archive, conservée hors de la base |
| `DRIVE_BACKUP_REFRESH_TOKEN` | Ancien mode opérateur seulement ; **ignoré par le parcours Web** qui utilise le compte connecté |
| `DRIVE_BACKUP_PASSPHRASE` | Phrase forte, unique, au moins 32 caractères ; copie de récupération indépendante nécessaire |
| `DRIVE_BACKUP_PG_DUMP_BIN` | Facultatif : chemin administrateur vers `pg_dump` compatible |
| `DRIVE_BACKUP_PG_RESTORE_BIN` | Facultatif : chemin vers `pg_restore` de même version majeure |
| `DRIVE_BACKUP_GPG_BIN` | Facultatif : chemin vers GPG 2 |

Les variables existantes `PG_DUMP_BIN`, `PG_RESTORE_BIN`, `GPG_BIN`, puis le PATH servent de repli pour les exécutables, pas pour les secrets. Ni `TEST_BACKUP_PASSPHRASE`, ni `BACKUP_PASSPHRASE`, ni `BACKUP_DATABASE_URL` ne sont repris automatiquement. Un futur export réel utilisera en revanche les identifiants PostgreSQL déjà configurés dans `settings.DATABASES` de Django ; cette opération nécessite l'autorisation distincte décrite ci-dessous. Django, Python 3.11+ (`hashlib.file_digest`), les clients PostgreSQL compatibles et GPG 2 sont requis.

La protection des jetons ajoute une dépendance **optionnelle et séparée** : `requirements-drive-backup.in` et son verrou avec SHA-256, sans modifier les dépendances ERP existantes. Le verrou contient `cryptography==50.0.2`, `cffi==2.1.1`, `pycparser==3.12`. Les roues Windows ont été installées avec `--require-hashes --only-binary=:all:` dans un répertoire d'essai `tmp/`, sans modifier `.venv`. Leur usage a été testé réellement dans la suite ci-dessous. Sans cette dépendance, l'assistant reste indisponible ; l'ERP peut toujours importer les nouveaux modules. L'installation et la qualification Linux/Render restent à autoriser et effectuer.

Le diagnostic de métadonnées précédemment autorisé a relevé PostgreSQL **18.6** en production. Des clients 17 ne conviennent pas ; le service refuse une version majeure différente. Les outils 18 n'ont pas été installés ou validés sur Render dans ce lot. Le code exige TLS (`require`, `verify-ca` ou `verify-full`) pour le dump ; `verify-full` est préférable pour vérifier également l'identité du serveur. Accepter `require` ne garantit pas cette vérification d'identité.

### Ce que le propriétaire devra faire, après accord distinct

1. Choisir le compte Drive et vérifier l'espace disponible, les quotas et l'absence d'option payante souhaitée. Aucun abonnement, achat ou augmentation payante n'est prévu par ce code.
2. Configurer une fois le client OAuth dédié avec le seul scope `https://www.googleapis.com/auth/drive.file`, l'API Drive et l'URI de retour exacte. Le consentement a désormais réussi dans le banc local de validation, après action manuelle du propriétaire. Cela ne configure ni ne déploie la future instance de production : son client dédié et son callback HTTPS exact restent à préparer et valider séparément.
3. Conserver le secret du client, la clé de protection des jetons et la phrase d'archive dans la configuration sécurisée du serveur. Le parcours Web acquiert lui-même le refresh token et le conserve chiffré dans le coffre de session ; aucune copie manuelle du jeton n'est nécessaire. Ne communiquer aucune de ces valeurs dans le dépôt, les captures d'écran ou le chat. Garder séparément la phrase de récupération : sa perte rend les archives inutilisables.
4. Prendre en compte l'expiration possible à sept jours des refresh tokens d'une application OAuth externe en statut Testing pour ces scopes ; ne pas annoncer une connexion pérenne sans traiter ce point.
5. Autoriser séparément la publication du code, sa configuration et son déploiement. Installer/valider les outils PostgreSQL 18 et GPG, puis tester d'abord avec une base et un Drive de test autorisés.
6. Avant le premier export réel : autoriser explicitement la lecture/export de la base de production et l'écriture sur le Drive choisi. Une restauration devra cibler uniquement une base isolée autorisée, jamais Neon production.

Le propriétaire conserve le contrôle des secrets et du consentement Google. L'agent peut préparer le code et les tests locaux ; il ne peut pas considérer cette implémentation comme une autorisation implicite d'accéder à la production ou à Drive.

### Banc local de connexion seule — aucun export

Le propriétaire a fourni le chemin d'un unique JSON OAuth conservé **hors dépôt** et autorisé sa lecture programmatique sans divulgation. Vérification locale du 8 novembre : fichier Web valide, projet `el-amine-drive-validation`, client et secret présents ; la dernière lecture du JSON confirme aussi l'URI `http://127.0.0.1:8000/core/backups/drive/callback/`. Le JSON n'a pas été copié et ses valeurs ne sont pas publiées. La présence de l'URI dans le téléchargement ne remplace pas une vérification du paramètre effectivement enregistré chez Google.

`scripts/launch_drive_oauth_local.py` prépare un serveur **distinct de l'ERP** : `settings.configure()` minimal, seulement l'application sessions, SQLite jetable, identité de navigation fictive, clés aléatoires temporaires, environnement purgé, aucun `.env`, compte ERP, base/média existant ou paramétrage Render chargé. Aucun mot de passe ERP n'est demandé dans ce banc. Le consentement Google reste une action manuelle du propriétaire.

Le setting Python booléen strict `DRIVE_BACKUP_LOCAL_CONNECTION_TEST=True` n'est défini que par ce banc. Il autorise uniquement les callbacks HTTP exacts `127.0.0.1:8000` ou `127.0.0.1:8001` avec `DEBUG=False`, rend l'écran Web non exportable et fait refuser le service de sauvegarde **avant** configuration, outils ou accès réseau. Il n'est pas activable par une variable d'environnement héritée. Le lanceur ajoute des interdictions indépendantes : aucun endpoint de création d'archive, aucun sous-processus, dump, dossier ou upload ; seuls `POST /token` en échange de code d'autorisation et `GET /drive/v3/about` pour l'identité sont autorisés sur les deux hôtes Google fixes. Pas de lecture de fichiers Drive.

Écoute exclusive sur `127.0.0.1`, port choisi explicitement parmi 8000/8001, contrôle Host/Origin, CSRF, cookie dédié avec suffixe du port et chemin `/core/backups/`, CSP sans ressources tierces, absence de journalisation des requêtes/queries/erreurs brutes. Les cookies ne sont pas isolés par port : le chemin réduit leur exposition, sans constituer une isolation entre deux services sur le même hôte. Éviter l'autre service local pendant ce test. Arrêt après vingt minutes ou bouton local d'arrêt, fermeture SQLite puis suppression du répertoire temporaire aux sorties normales. Un arrêt brutal reste hors garantie d'effacement ; les jetons résiduels seraient chiffrés avec une clé temporaire non persistée. La déconnexion locale n'est pas une révocation du consentement Google et ne démontre aucune restaurabilité.

Commande de contrôle (aucune requête réseau), puis test navigateur avec `--serve`, depuis la racine du projet ; remplacer le chemin indicatif par le fichier privé choisi, **ne jamais coller son contenu** :

```powershell
.\.venv\Scripts\python.exe -I -B scripts/launch_drive_oauth_local.py --client-file "CHEMIN_HORS_DEPOT\client_secret.json"
.\.venv\Scripts\python.exe -I -B scripts/launch_drive_oauth_local.py --client-file "CHEMIN_HORS_DEPOT\client_secret.json" --serve
# Alternative explicite retenue, sans arrêter le programme existant sur 8000 :
.\.venv\Scripts\python.exe -I -B scripts/launch_drive_oauth_local.py --client-file "CHEMIN_HORS_DEPOT\client_secret.json" --port 8001 --serve
```

Le lanceur charge le répertoire de dépendances optionnelles déjà vérifiées dans `tmp/p0a-drive-oauth-dependencies-20261108`, s'il existe. Il ne les installe pas et ne modifie aucune configuration Google. Un port occupé ou interdit fait échouer le lancement ; aucun processus n'est tué et aucun autre port n'est choisi silencieusement. Pour le port 8001, le propriétaire doit ajouter puis enregistrer exactement `http://127.0.0.1:8001/core/backups/drive/callback/` dans son client OAuth Google de validation ; inutile de communiquer ou recopier son secret.

Le démarrage synthétique réel a atteint la création du serveur, puis Windows a refusé l'écoute avec **WinError 10013**. Aucun serveur de ce banc n'a été ouvert et aucun appel Google n'a eu lieu dans cet essai. Le lancement contrôlé du banc avec le client sélectionné a également échoué proprement, sans diagnostic brut ni secret. Le diagnostic réseau en lecture seule dans un contexte autorisé a ensuite confirmé un **processus Python existant en écoute sur le port 8000** ; ce port ne figure pas dans les plages d'exclusion Windows consultées. Ce processus n'a été ni arrêté ni interrogé en HTTP, et ses arguments n'ont pas été lus. Le propriétaire doit libérer son serveur local ou autoriser une adaptation explicite de port et du callback Google. Le parcours WSGI sans sockets est testé automatiquement ; l'écoute réelle de ce banc et le consentement restent à observer. Ne pas désactiver le pare-feu ou changer une protection Windows pour contourner cet échec.

Reprise après accord du propriétaire : le processus du port 8000 n'est pas identifié comme `manage.py runserver` ou comme ce lanceur ; une classification locale a été faite sans afficher ses arguments. Il reste intact. Le banc a été adapté au port 8001, avec deux tests supplémentaires et cookies resserrés. Le contexte de commande classique refuse la lecture des dépendances temporaires (`PermissionError` avec fixtures entièrement fictives) ; le même interpréteur lancé par le canal local déjà utilisé pour les tests y accède normalement. **Démarrage réel PASS sur 127.0.0.1:8001** : message READY, page et `/healthz` HTTP 200, bannière connexion seule, aucun formulaire d'archive, en-têtes no-store/no-referrer et cookie HttpOnly au chemin prévu. À ce contrôle : zéro connexion confirmée, zéro callback en échec, zéro opération d'archive. Consentement Google et configuration du second callback restent manuels et non observés ; aucun accès production, fichier Drive ou upload effectué. Le banc s'arrête automatiquement après vingt minutes, puis doit être relancé si nécessaire.

### Correctif du refus du formulaire local

Le propriétaire a constaté `Request refused.` après soumission du formulaire de connexion. Ce texte provient exclusivement du garde HTTP local avant Django, pas d'une réponse Google. Le code présentait un conflit documenté : `no-referrer` sur une page de formulaire peut provoquer `Origin: null` lors d'un POST de navigation, alors que le garde et Django refusent cette origine. Voir la [règle normative Fetch sur l'en-tête Origin](https://fetch.spec.whatwg.org/#origin-header). Aucun en-tête brut du navigateur du propriétaire n'a été enregistré ; il s'agit donc de la cause identifiée dans le code, pas d'une capture de ses requêtes.

Correctif limité au banc : `Referrer-Policy: same-origin` sur le GET de la page des formulaires uniquement ; `no-referrer` reste appliqué au callback, à la redirection Google et aux autres routes. Les contrôles stricts Host, loopback, Origin exacte, refus des proxys et CSRF restent inchangés. Une origine `null`, manquante ou étrangère reste refusée. Le diagnostic ne conserve que des compteurs de catégories fixes, sans URL, valeur d'origine, cookie, code OAuth ou corps de requête.

L'ancien serveur de ce banc a été arrêté via son formulaire CSRF, sans toucher au programme du port 8000 ; zéro connexion confirmée et zéro archive à l'arrêt, répertoire temporaire supprimé (vérification : zéro répertoire de banc restant). Puis relance sur 8001 : **READY**. Vérification locale réelle : GET formulaire **200**, politique `same-origin`, POST avec session/CSRF/origine valides **302** vers l'endpoint Google attendu et callback 8001 exact ; la redirection n'a **pas** été suivie. La demande de test a ensuite été effacée par déconnexion locale. Aucun appel token/identité Google ni fichier Drive n'a été nécessaire à cette vérification. Le propriétaire doit rouvrir la page principale, pas resoumettre l'ancien onglet d'erreur : les sessions et formulaires précédents ont été invalidés au redémarrage. Consentement réel et éventuels réglages de confidentialité du navigateur restent à observer.

### Connexion Google réelle confirmée — PASS

Après le consentement effectué personnellement par le propriétaire, sa capture montre le retour sur la page locale avec « Connexion Google confirmée ». Contrôle indépendant de la capture par GET local `/healthz` : HTTP **200**, `connections_confirmed=1`, `callbacks_failed=0`, catégories de refus toutes à zéro. Aucune adresse de compte, clé, valeur de cookie, URL d'autorisation complète ni jeton n'est reproduit dans ce rapport.

Le compteur de confirmation est incrémenté uniquement après `oauth.complete()` réussi : retour callback/state/PKCE, échange du code réel, validation du scope exact `drive.file`, lecture d'identité par `about`, puis création du coffre chiffré de session. Cette étape OAuth réelle est donc **PASS**, et le défaut de formulaire n'a pas empêché le consentement après correction. Le compteur est une preuve d'exécution applicative, pas un audit réseau indépendant.

Le banc de connexion seule déclare `archive_operations=0` et `production_access=false`, en cohérence avec ses interdictions de code et de transport ; ces deux champs sont des constantes de mode, pas des capteurs indépendants. Aucun dossier, fichier Drive, export PostgreSQL ou média n'a été utilisé par cette première connexion. L'essai distinct ultérieur de transfert fictif a échoué (voir ci-dessous). La connexion et ses clés temporaires appartiennent au banc : ne transférer aucun de ses jetons ou clés vers la production. La fin du banc impose une nouvelle connexion ultérieure.

**Autorisation distincte désormais reçue :** le propriétaire a répondu « oui » à l'envoi d'une petite archive entièrement fictive chiffrée, sa récupération, sa restauration dans une base jetable et la suppression des seuls fichiers créés par cet essai. Cette autorisation ne concerne ni un export réel, ni un déploiement, ni Neon, ni les médias ou le bucket exclu.

### Préparation du cycle Drive fictif autorisé

Le contexte `exec_command` autorisé permet désormais le cycle natif sans désactiver de protection Windows : **PostgreSQL 17.11/GPG 2.4.9 PASS en 18,305 s**, six tables, dix lignes, six séquences, douze index et vingt contraintes comparés après vraie restauration. Mauvaise phrase et altération refusées, cluster arrêté et répertoire temporaire supprimé. Preuve expurgée conservée dans `tmp/p0a-drive-native-validation/local17-pass-20261108.json`. Cela résout l'ancien blocage de cet essai 17, **pas la qualification PostgreSQL 18/Render**.

Le banc distant sépare deux processus : le worker natif possède uniquement sa base fictive et sa phrase GPG temporaire ; le serveur OAuth possède uniquement les sessions SQLite et les jetons chiffrés. Aucun coffre Google réel n'entre dans le dump. Le pont local n'échange que le `.gpg`, ses empreintes et des JSON à schémas exacts liés à un run ; publication atomique, refus des liens/chemins inattendus et délais bornés. Le worker restaure exclusivement le fichier téléchargé, jamais une copie locale de repli.

`scripts/validate_drive_backup_remote.py` prévoit un seul fichier `.tar.gpg` de test en racine My Drive, sans dossier ni recherche de fichiers existants : ID préalloué, nom et marqueur uniques, visibilité par défaut neutralisée, propriétaire/permissions/SHA-256/tailles contrôlés avant et après téléchargement. Après le résultat natif, suppression de cet ID seulement et contrôle de disparition sous la même identité. Aucun jeton n'est passé au worker, dans ses arguments ou dans le pont. Une interruption ne peut pas produire PASS ; nettoyage ambigu = FAIL, jamais balayage ni nouvel ID automatique.

Les ACL Windows isolent les deux contextes d'exécution : le contexte Node ne peut pas lire le pont privé du worker. Elles n'ont pas été élargies. Le serveur de test est lancé dans le même contexte autorisé que le worker, avec un nouveau répertoire de dépendances temporaires : `tmp/p0a-drive-remote-dependencies-20261108`. Installation des mêmes trois versions depuis PyPI officiel, `--require-hashes`, roues binaires uniquement, configuration pip/environnement hérités neutralisés ; aucune modification de `.venv` ou des verrous ERP. Imports vérifiés : cryptography 50.0.2, cffi 2.1.1, pycparser 3.12.

État observé après lancement manuel : **FAIL**, archive fictive chiffrée de **20 570 octets**, ID préalloué connu, mais transfert complet non confirmé. L'erreur de nettoyage a écrasé la première erreur ; aucune cause initiale n'est établie. Le worker a reçu l'abandon et nettoyé son cluster/runtime ; sa phrase éphémère et l'archive d'origine ont disparu. Le serveur OAuth s'est terminé avec nettoyage local PASS, qui **ne signifie pas suppression Drive PASS**. Les preuves d'origine restent inchangées dans le pont ignoré par Git.

Priorité : `scripts/cleanup_drive_synthetic_local.py`, mode **nettoyage uniquement**, nouveau consentement manuel sur 8001. Il recoupe trois preuves publiques et épingle l'ancien ID/run/nom/taille/empreinte ; il n'ouvre pas le chemin du ciphertext supprimé. Son transport interdit upload, téléchargement, listing, génération d'ID et toute autre cible. Un objet avec provenance contradictoire n'est pas supprimé. Un 404 initial reste ambigu. Suppression réussie puis constat de 404 sous la même identité requis pour PASS. Les métadonnées diagnostiques ne contiennent que des comparaisons booléennes ; les erreurs primaire/nettoyage sont désormais séparées et expurgées. Aucun nouvel envoi avant résolution du résidu ; aucune utilisation de la production comme repli.

La première reprise a échoué avant DELETE : GET HTTP 200 et tous les contrôles de provenance/SHA/confidentialité réussis, sauf `mime_match=false`. Le libellé exact est resté privé ; il n'est pas nécessaire pour constater que la liste de deux MIME était trop restrictive. Correction : type opaque syntaxiquement valide et non Google-native, **avec SHA-256 présent, valide et exact obligatoire** pour ce nettoyage. Les dossiers, raccourcis et documents natifs restent interdits. Le test de transfert complet accepte aussi un libellé opaque différent avec SHA exact, mais impose toujours téléchargement identique puis vraie restauration ; aucune qualification fondée sur le MIME ou le seul octet TAG3. La règle historique de nettoyage sans SHA du broker ne subsiste que pour son MIME par défaut et son objet fraîchement épinglé, pas pour cette reprise du résidu.

Résultat ultérieur observé : nettoyage ciblé **PASS**, corroboré par le rapport expurgé (provenance et SHA exacts, DELETE puis disparition sous la même identité). Aucun envoi/téléchargement n'a été fait par ce nettoyage. Le banc s'est arrêté proprement avec sessions temporaires nettoyées PASS. La nouvelle relance complète a ensuite été préparée avec un worker natif 17, une phrase éphémère, un bridge unique et un broker neuf ; le propriétaire a refait le consentement et lancé le bouton d'essai. Les archives restent entièrement fictives et sont supprimées après vérification, contrairement aux futures vraies sauvegardes à conserver.

**Dernier résultat : PASS réel du cycle distant fictif.** Les rapports `remote-result.json` et `native-result.json` du bridge local `cf3c4617494f4350bfc65b625c8103e3` concordent : archive de 20 570 octets, SHA-256 `34067bb376b721a01a39d46552937dc3e1135f8a5bfe1513baabbdca8bdb0f5a`, upload/download effectifs, compte sélectionné et confidentialité contrôlés, déchiffrement puis restauration des octets téléchargés dans PostgreSQL 17.11. Six tables, dix lignes, six séquences, douze index et vingt contraintes correspondent ; mauvaise phrase et altération sont rejetées. Durée fixture 139,576 s (pas un RTO de production). Le seul nouvel ID a été supprimé et sa disparition confirmée ; ciphertext téléchargé, runtime/cluster et sessions du broker nettoyés. Les deux processus sont terminés avec code 0. La preuve concerne le fichier racine My Drive du banc séparé, **pas** l'exécution réelle du bouton ERP/dossier fixe ni Render/HTTPS/PostgreSQL 18. Aucun jeton du banc n'est transféré à une autre instance.

## Limites à ne pas masquer

La préparation suivante a été ajoutée après le PASS distant : voir `P0_A_PREPARATION_RENDER_DRIVE.md`. Elle corrige la CSP ciblée du parcours Google, protège les callbacks refusés/expirés, traite ATOMIC_REQUESTS, neutralise la visibilité par défaut à la création et ajoute les gardes hors ligne build/démarrage avec logs Gunicorn sans query. Le cycle local PostgreSQL 18.6 a réussi séparément ; les roues OAuth Linux/Python 3.12 sont vérifiées par hashes. Aucun paramètre de service, secret ou forfait Render n'a été modifié ; la configuration effective reste à vérifier à partir d'une capture non sensible du propriétaire.

- **Exécution synchrone et manuelle seulement.** Aucun worker, file de tâches, planification ou service payant n'est ajouté. Un traitement durable en arrière-plan n'est pas implémenté.
- Taille physique de la base limitée à 32 Mio ; dump/TAR limité à 4 Mio ; fichier chiffré limité à 5 Mio. Le contrôle physique peut réussir alors que l'archive dépasse ensuite sa propre limite. L'export est alors refusé, sans envoi en clair.
- Délai applicatif visé de 80 secondes avec sous-opérations bornées. Ce n'est pas une garantie temps réel : DNS, opérations fichiers et arrêt brutal d'un worker ne sont pas entièrement préemptés par ce délai.
- Le nettoyage normal et après erreurs gérées est testé. Une panne, un SIGKILL ou un arrêt brutal peut laisser un répertoire temporaire privé contenant le dump en clair ; aucun balayage des répertoires orphelins ni effacement irréversible sur disque n'est garanti. Cette limite doit être traitée avant une qualification de protection complète.
- L'espace libre de Drive n'est **pas estimé avant l'envoi**. Les refus HTTP de quota/droits sont traités sans exposer la réponse brute. Un bouton actif indique seulement que la configuration est renseignée, pas que Drive est joignable ni suffisamment dimensionné.
- Pas de rétention, rotation, suppression automatique ou relance automatique. Une réponse perdue peut laisser une archive chiffrée sur Drive sans confirmation locale : vérifier Drive avant de recommencer pour éviter les doublons.
- Le SHA-256 distant vérifie les octets transférés, pas la restaurabilité ni le nombre de lignes attendu. **Aucune restauration n'est lancée par le bouton.**
- Passphrase de chiffrement et jeton OAuth résident sur le même serveur : un serveur compromis pourrait déchiffrer ou supprimer ses archives accessibles. Ce n'est pas une sauvegarde immuable ni une séparation complète des pouvoirs.
- Le test GPG natif sous Windows emploie une adaptation des chemins pour l'exécutable MSYS fourni avec Git. Cela ne qualifie pas l'exécution directe du service sur un serveur Windows ; la cible de déploiement reste Linux/Render.
- OAuth, renouvellement et transfert du fichier fictif sont validés réellement dans le banc local ; la configuration et l'exécution de production ne le sont pas. Le client Windows/Tauri et les WebViews Android ne sont pas qualifiés pour ce consentement ; ne pas élargir leur politique de domaines. La connexion réalisée dans un navigateur Web normal n'est pas partagée avec un autre navigateur ou l'application Windows.
- Le callback doit recevoir un code d'autorisation court dans son URL. Les journaux métier n'enregistrent que `request.path`, et la réponse redirige sans query avec `no-store`/`no-referrer`. **Cela ne garantit pas son absence des journaux Gunicorn/proxy/Render ou de l'historique du navigateur** : leur politique de masquage doit être examinée avant activation. Les access/refresh tokens ne sont jamais mis dans l'URL.
- Les nonces/coffres expirés deviennent inutilisables mais nécessitent le nettoyage de sessions expirées standard de Django pour supprimer les lignes. Aucun nouveau job automatique n'est installé. Une restauration de la base rétablit aussi d'anciennes sessions/coffres chiffrés : invalider les sessions restaurées et exiger une nouvelle connexion avant d'exposer l'instance restaurée.
- Le callback est explicitement hors transaction de requête et refuse un échange Google si la consommation du nonce ne peut pas être validée avant l'appel distant. Les tests utilisent SQLite ; les accès concurrents multi-workers sur PostgreSQL 18 restent à qualifier réellement.

## Tests réellement exécutés

Commande locale simple, depuis la racine, sans `.env` ni identifiants hérités, avec les dépendances de test optionnelles installées :

```powershell
.\.venv\Scripts\python.exe scripts/test_drive_backups_local.py
```

Commande exacte utilisée pour la dernière suite, avec les dépendances vérifiées dans un répertoire temporaire séparé plutôt que dans `.venv` :

```powershell
.\.venv\Scripts\python.exe -I -B -c "import sys; sys.path.insert(0,sys.argv[1]); sys.path.insert(0,sys.argv[2]); import scripts.test_drive_backups_local as r; raise SystemExit(r.main())" "$PWD\tmp\p0a-drive-oauth-dependencies-20261108" "$PWD"
```

| Contrôle | Résultat / preuve |
| --- | --- |
| Suite Django ciblée | **PASS — 341 tests, 30,690 s**, exit 0, aucun test ignoré, dans le contexte autorisé ; dépendances verrouillées chargées depuis le répertoire temporaire. Relance après préparation sécurité/runtime ; anciens chiffres conservés comme historique seulement |
| Contrôle Django du runner | **PASS — aucune anomalie détectée** |
| Service Drive | 78 tests inclus, PostgreSQL/GPG/HTTPS simulés : configuration, version, concurrence, compte destinataire, refus, chiffrement, périmètre, intégrité, nettoyage, neutralisation de visibilité par défaut et interdiction d'export en mode connexion seule |
| Assistant OAuth | 52 tests inclus : configuration, absence de dépendance, state/PKCE/TTL, rejeu, coffre chiffré, séparation utilisateurs/navigateurs, session ancienne, changement de compte, formulaire ancien, déconnexion, refus d'une transaction externe, HTTP local strict sans DEBUG et absence de repli vers un jeton global ; Google simulé |
| Écran, formulaires, permissions, CSRF | 54 tests inclus, dont GET sans export, POST seulement, double permission, mot de passe, limitation, connexion/déconnexion/callback, journalisation, mode connexion seule, CSP ciblée, next sans code/state, callback ForcePasswordChange no-referrer/no-store et export hors ATOMIC_REQUESTS. Un parcours TestClient utilise le vrai coffre/PKCE/formulaire avec Google et création d'archive simulés |
| Lanceur OAuth local isolé | 26 tests : JSON publics fictifs, configuration, parcours WSGI/CSRF/Origin/Host, route distante absente par défaut, consentement et sélection exacte, test distant optionnel lié au compte et utilisable une fois, référent par route ; mode nettoyage avec route dédiée, page sans promesse d'upload et même protection de destination ; Google simulé |
| Pont natif, broker et reprise ciblée | 71 tests (21 + 38 + 12) : schémas/atomicité/liens/SHA/délais, téléchargement obligatoire, allowlist Google, diagnostics expurgés, erreurs distinctes, unicité ID, provenance du résidu, MIME opaque avec SHA strict, comptes 17/18 stricts, refus des métadonnées non entières et faux PASS/interruption ; ni réseau ni vrais sous-processus dans ces tests |
| Préflight de déploiement | 15 tests : flags désactivés sans dépendances/outils, échec sûr, syntaxe sans fuite, TLS/URI/clé/timeout, versions incompatibles, alias d'exécutables cohérents et gardes avant Django/Gunicorn. Crypto et outils simulés dans cette suite pour conserver la dépendance optionnelle |
| Régressions ciblées | 45 tests existants inclus : sauvegardes, permissions et RBAC |
| Isolation du runner | Base SQLite et médias fictifs dans un répertoire jetable ; `.env` neutralisé ; environnement purgé ; sockets, DNS et vrais sous-processus bloqués |
| Chiffrement GPG natif supplémentaire | **PASS**, archive fictive de 10 330 octets ; TAR de deux fichiers attendus ; déchiffrement identique et nettoyage vérifié. Dump fabriqué : **aucun export/restauration PostgreSQL réel** |
| Scan ciblé des 16 fichiers | **PASS pour les motifs contrôlés** : clés privées, formats usuels de jetons GitHub/Google/AWS et URI PostgreSQL avec identifiants, aucun résultat ; relecture des fixtures OAuth publiques/fictives. Un scan heuristique n'est jamais une preuve exhaustive d'absence de secrets |
| Relance du scan ciblé avec les bancs OAuth/Drive | **PASS — 30 fichiers**, aucun des motifs contrôlés détecté après préparation Render. Aucun JSON privé ne fait partie du lot ; les cinq empreintes des modifications préexistantes restent inchangées. Scan heuristique non exhaustif |
| Préservation des travaux antérieurs | **PASS** : SHA-256 inchangés pour `.env.example`, les deux workflows déjà modifiés, `apps/core/backup.py` et `apps/core/tests/test_backups.py` |
| Diff Git | **PASS** : `git diff --check`, hors avertissements informatifs de fins de ligne existants |
| Suite complète ERP, Android, émulateur | **NOT RUN** ; aucun résultat global de non-régression revendiqué |
| Nouveau harness natif PostgreSQL/TLS/GPG | **PASS 17.11** dans le contexte autorisé : vrai dump, GPG, restauration, six tables/dix lignes et objets comparés, tests cryptographiques négatifs et nettoyage PASS. Le précédent échec Windows 87 est conservé comme historique, pas comme état actuel |
| Export/restauration PostgreSQL 18 avec cette nouvelle option | **PASS fictif natif Windows 18.6**, 25,752 s : six tables/dix lignes/six séquences/douze index/quarante-quatre contraintes, tests négatifs et nettoyages PASS. Transport simulé pour ce cycle ; Linux/Render restent NOT RUN |
| OAuth Google réel local | **PASS** : consentement manuel, retour affiché au propriétaire, puis GET health HTTP 200 avec une connexion confirmée et zéro callback en échec. Scope exact et coffre chiffré validés par le parcours réel ; aucune identité reproduite dans le rapport |
| Renouvellement de jeton et upload/download Drive réels | **PASS réel** sur la nouvelle archive fictive, SHA identique et confidentialité contrôlée avant/après. Déchiffrement/restauration PostgreSQL 17 et comparaisons PASS, mauvaise phrase/altération rejetées. Premier FAIL conservé comme historique, sans cause initiale inventée |
| Nettoyage du dernier cycle complet | **PASS** : suppression du seul nouvel ID/disparition confirmée, ciphertext téléchargé supprimé, cluster arrêté/runtime supprimé ; broker stoppé par sa route protégée, sessions nettoyées PASS. Worker et broker exit 0 |
| Nettoyage distant de l'essai échoué | **PASS confirmé** par capture et rapport distinct : provenance/SHA exacts puis suppression/disparition. Aucun upload/download/listing. Ancien worker et sessions nettoyés PASS ; les échecs restent conservés comme historique |
| Écoute du banc OAuth réel | **PASS sur 8001** : READY, GET locaux HTTP 200 et POST de formulaire CSRF valide HTTP 302 vers Google, cookie/en-têtes par route vérifiés. Port 8000 laissé intact ; consentement Google désormais **PASS** |
| Sauvegarde réelle et restauration isolée de données réelles | **BLOCKED par autorisation/configuration**, non effectuées |
| Médias et bucket exclu | **HORS PÉRIMÈTRE**, aucun accès dans ces essais |

Le premier passage de la nouvelle suite a exposé une collision entre un helper de test `client()` et le client Django. Le helper a été corrigé, sans affaiblir le service. Le test natif GPG a aussi nécessité l'adaptateur MSYS propre au banc d'essai. Les chiffres ci-dessus proviennent de la relance réussie, pas d'une estimation.

L'ajout du banc OAuth a exposé deux défauts de tests : un appel WSGI réel ferme les connexions de requête et ne convient pas à l'enveloppe transactionnelle de `TestCase` ; ces cas utilisent désormais `TransactionTestCase`. Le résolveur d'URL conservait aussi les closures d'un précédent banc : son cache est vidé à la construction du banc. Ces corrections, puis les tests du port alternatif, précèdent la dernière relance **238 PASS**. La politique CSP autorise uniquement l'origine Google officielle en plus du serveur local pour la redirection du formulaire de consentement ; son comportement dans un vrai navigateur reste à observer.

Les preuves synthétiques locales supplémentaires se trouvent dans `tmp/drive-backup-validation-20261108/` (répertoire ignoré par Git). Elles ne contiennent aucune sauvegarde réelle et ne doivent pas être incluses dans une publication.

## Fichiers de ce changement local — 30

Nouveaux fichiers :

- `apps/core/drive_backup.py`
- `apps/core/drive_oauth.py`
- `apps/core/backup_forms.py`
- `apps/core/backup_views.py`
- `apps/core/templates/core/backup_list.html`
- `apps/core/tests/test_drive_backup_service.py`
- `apps/core/tests/test_drive_backup_views.py`
- `apps/core/tests/test_drive_oauth.py`
- `scripts/test_drive_backups_local.py`
- `scripts/validate_drive_backup_local.py`
- `scripts/launch_drive_oauth_local.py`
- `scripts/validate_drive_backup_remote.py`
- `scripts/cleanup_drive_synthetic_local.py`
- `scripts/check_drive_backup_runtime.py`
- `scripts/tests/test_drive_oauth_launcher.py`
- `scripts/tests/test_drive_native_bridge.py`
- `scripts/tests/test_drive_remote_transfer.py`
- `scripts/tests/test_drive_synthetic_cleanup.py`
- `scripts/tests/test_drive_runtime_preflight.py`
- `requirements-drive-backup.in`
- `requirements-drive-backup.lock`
- `docs/audits/P0_A_SAUVEGARDE_GOOGLE_DRIVE_WEB.md`
- `docs/audits/P0_A_VALIDATION_RECUPERATION_DRIVE.md`
- `docs/audits/P0_A_PREPARATION_RENDER_DRIVE.md`

Adaptations minimales de fichiers existants :

- `apps/core/urls.py` : cinq routes Web.
- `templates/base/base.html` : lien de menu protégé par permission.
- `apps/core/security.py` : en-têtes privés sur le seul callback Drive, y compris les réponses middleware anticipées.
- `build.sh` : dépendance optionnelle en opt-in et garde avant Django/migrations.
- `start.sh` : même garde et access-log sans query, sans activer un nouvel access log.
- `render.yaml` : trois flags Drive explicitement false, aucun secret/forfait modifié.

Les modifications déjà présentes dans `.env.example`, les workflows GitHub, `apps/core/backup.py` et `apps/core/tests/test_backups.py` sont étrangères à ce nouveau diff et ne doivent pas être embarquées implicitement. Les autres travaux locaux non suivis ne doivent pas être ajoutés avec un `git add .`.

## Coût et documentation de référence

Drive ne doit être utilisé que dans l'espace et les quotas gratuits réellement disponibles du compte. Aucune garantie de gratuité illimitée : la documentation API mentionne une facturation prévue pour les dépassements de quotas plus tard en 2026. Ne pas activer la facturation, augmenter les quotas ou souscrire un forfait pour ce lot. L'espace gratuit personnel annoncé est partagé entre Drive, Gmail et Photos ; il faut vérifier le compte réel avant activation.

- [Scopes Drive et accès limité `drive.file`](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).
- [Envoi multipart et limites de taille](https://developers.google.com/workspace/drive/api/guides/manage-uploads).
- [Métadonnées et SHA-256 des fichiers Drive](https://developers.google.com/workspace/drive/api/reference/rest/v3/files).
- [Quotas et tarification de l'API Drive](https://developers.google.com/workspace/drive/api/guides/limits#pricing).
- [Espace du compte Google](https://support.google.com/drive/answer/6374270?hl=fr).
- [Flux OAuth serveur](https://developers.google.com/identity/protocols/oauth2/web-server) et [expiration des refresh tokens](https://developers.google.com/identity/protocols/oauth2/).
- [Compatibilité de version de pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html#APP-PGDUMP-NOTES).

**Conclusion : cycle Drive réel fictif PostgreSQL 17, cycle natif fictif PostgreSQL 18 Windows et nettoyages PASS. Option Web et gardes de déploiement préparés/testés localement, non déployés ; Linux/Render, bouton ERP/dossier fixe et configuration HTTPS restent à qualifier. Voir `P0_A_PREPARATION_RENDER_DRIVE.md` et `P0_A_VALIDATION_RECUPERATION_DRIVE.md` pour les étapes et autorisations restantes. Aucune vraie sauvegarde de production créée : cette preuve ne protège pas encore ses données. P0-A reste ouvert ; aucun passage à P0-B.**
