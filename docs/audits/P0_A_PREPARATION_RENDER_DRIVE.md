# Préparation Render — sauvegardes manuelles Google Drive

Date : 8 novembre 2026. État consigné avant publication : préparation locale, aucun changement de secret distant, export de production ou service payant activé.

Le propriétaire a ensuite autorisé explicitement la publication des seuls 30 fichiers de ce lot sur `main`, ainsi que son déploiement Render automatique, avec les fonctions de sauvegarde maintenues désactivées. Cette autorisation ne couvre ni l'activation, ni un export réel, ni un changement de forfait. Les autres changements locaux restent exclus.

## Ce qui est établi

- Le cycle réel Drive sur une archive entièrement fictive PostgreSQL 17 a réussi, avec téléchargement, déchiffrement, restauration et nettoyage confirmés.
- Le cycle natif fictif **PostgreSQL 18.6 / GPG 2.4.9 sous Windows** a réussi en 25,752 s : TLS `verify-full`, vrai dump et restauration, six tables, dix lignes, six séquences, douze index et quarante-quatre contraintes identiques. Mauvaise phrase et altération rejetées ; cluster/runtime nettoyés. Drive est simulé dans ce cycle 18.
- Preuve locale ignorée par Git : `tmp/p0a-drive-native-validation/local18-pass-20261108.json`. Le parent distant accepte désormais les comptes exacts propres à chaque majeure : 20 contraintes sur 17, 44 sur 18 ; aucune relaxation des compteurs ou types.
- Le préflight natif des clients 18, GPG 2 et Fernet verrouillé a réussi sur ce PC, sans SQL ni accès Google.
- Les captures fournies par le propriétaire confirment le service Render existant : source `YoucefTahir-dev/appAlimGen`, branche `main`, racine vide, build `bash build.sh`, démarrage `bash start.sh`, pre-deploy vide et Auto-Deploy `On Commit`. Aucun de ces paramètres n'a été modifié.
- La capture du Shell Render confirme `pg_dump` **18.6**, `pg_restore` **18.6** et GPG **2.2.40**. Aucune installation de ces outils ni bascule de runtime n'est nécessaire à ce stade. Ce relevé de versions ne qualifie ni cryptography/Fernet sur Render, ni la configuration effective, ni l'accès PostgreSQL, ni un export/restauration.
- Dernière suite isolée : **341 tests PASS en 30,690 s, exit 0, aucun ignoré**. Contrôle de syntaxe Bash des scripts build/start et `git diff --check` PASS. Scan heuristique du lot de 30 fichiers : zéro motif détecté ; cinq empreintes des changements préexistants inchangées.
- Revue de publication sélective : le même runner, avec `apps.core.backup` et `apps.core.tests.test_backups` chargés en mémoire depuis `git show HEAD` plutôt que depuis leurs versions locales modifiées, donne **339 tests PASS en 22,653 s, exit 0**. Les deux tests en moins appartiennent aux changements historiques exclus. Aucun fichier de travail remplacé, secret lu ou service externe contacté ; aucun couplage runtime au correctif historique détecté. Ce contrôle n'est pas une exécution de la suite ERP complète.
- Contrôle final du **contenu exact sélectionné dans l'index**, extrait avec `git checkout-index` dans une copie propre : **339 tests PASS en 20,483 s, exit 0**. Le premier essai propre avait révélé une dépendance des tests d'interface au manifest `collectstatic` local (101 erreurs, aucun export). Le runner utilise désormais temporairement `StaticFilesStorage` pour ces seuls tests, sans modifier les autres stockages ni le runtime. Le manifest de déploiement reste hors de cette suite ; le build Render conserve son `collectstatic` existant. Ce PASS sur copie propre est la preuve sélective de référence.
- Les trois roues optionnelles pour **CPython 3.12 / Linux x86-64** ont été résolues et téléchargées avec `--require-hashes` depuis PyPI : cffi 2.1.1, cryptography 50.0.2, pycparser 3.12. Ce contrôle de distribution **n'est pas une exécution Linux**.
- WSL Ubuntu ne dispose pas du compilateur/headers requis et démarre sous root. Aucune installation système ni désactivation de protection n'a été entreprise. Docker Desktop n'expose pas de daemon actif. **L'exécution Linux/Render reste non qualifiée.**

## Corrections préparées

1. La CSP de la seule page Sauvegardes et de sa redirection OAuth autorise l'origine officielle `https://accounts.google.com`, sans assouplissement global. La page garde un référent same-origin pour son POST ; redirection/callback no-referrer.
2. Le callback anonyme/inactif renvoie vers la connexion avec un `next` fixe, sans code/state. Les refus RBAC restent effectifs. Le middleware impose no-referrer/no-store sur ce seul chemin, même en cas de changement de mot de passe obligatoire avant la vue. Il n'exempte aucune authentification.
3. La création d'archive est explicitement non atomique au niveau de la requête : le verrou/contrôle PostgreSQL peut commencer sa transaction read-only après les audits, même avec `ATOMIC_REQUESTS=True`. Le callback garde aussi son exemption transactionnelle.
4. Les créations de dossier et de fichier Drive neutralisent la visibilité par défaut avec `ignoreDefaultVisibility=true`, tout en conservant les contrôles de permissions. Cela ne supprime pas des permissions héritées : un dossier partagé reste refusé.
5. Le build peut installer les dépendances optionnelles verrouillées, en opt-in et roues binaires uniquement. Les gardes build **et démarrage** vérifient outils/configuration avant Django, migrations ou Gunicorn si l'activation est demandée.
6. Le format d'access log Gunicorn exclut la query (`%(U)s`, pas `%(q)s`). Aucun access log supplémentaire n'est activé. Les journaux d'un proxy externe et l'historique navigateur ne sont pas garantis par cette correction.

## Préflight sans sauvegarde

`scripts/check_drive_backup_runtime.py` ne charge ni Django/settings ni `.env`, ne contacte ni base ni Drive, ne suit aucune URL de configuration et ne lit aucun fichier de credentials. Il lance uniquement `--version` sur les trois exécutables avec un environnement sans credentials. Les rapports ne contiennent que des codes fixes et majeures bornées.

```bash
python scripts/check_drive_backup_runtime.py --expected-postgres-major 18
```

Les variables `DRIVE_BACKUP_PG_DUMP_BIN`, `DRIVE_BACKUP_PG_RESTORE_BIN`, `DRIVE_BACKUP_GPG_BIN` prennent priorité sur leurs alias historiques `PG_DUMP_BIN`, `PG_RESTORE_BIN`, `GPG_BIN`, puis le PATH, comme dans le service.

Pour le contrôle de syntaxe d'une configuration fournie au processus :

```bash
python scripts/check_drive_backup_runtime.py --expected-postgres-major 18 --check-configuration --public-origin https://gestio-stock-web.onrender.com
```

Ce PASS ne confirme ni la version du serveur distant, ni les paramètres Django effectifs, ni les droits SQL, l'espace Drive, le quota gratuit, le dossier final ou une restauration de production. Les champs correspondants restent explicitement false. Un outil présent sur PATH n'est pas nécessairement le même qu'une surcharge Python de settings : la configuration effective reste à vérifier.

`--build-guard` laisse les installations désactivées continuer sans outils optionnels : il indique DISABLED et `runtime_checked=false`, jamais « prêt à sauvegarder ». Les flags invalides/partiels et les activations sans outils/configuration valides échouent. `build.sh` passe ce garde avant ses commandes Django ; lors d'un vrai déploiement, il conserve ensuite ses migrations existantes. Le build complet n'a pas été lancé ici.

## Configuration à préparer, sans activation

Le Blueprint local garde explicitement :

```text
DRIVE_BACKUP_BUILD_DEPENDENCIES=false
DRIVE_BACKUP_ENABLED=false
DRIVE_BACKUP_OAUTH_ENABLED=false
```

Pour installer seulement la dépendance OAuth sur une cible autorisée, le premier flag peut être préparé à true en gardant les deux autres false. Cela ne fournit pas automatiquement les outils PostgreSQL 18 ; ceux-ci sont déjà présents dans le service Render observé, d'après la capture du propriétaire.

Le propriétaire devra conserver et renseigner lui-même, hors Git et sans les communiquer dans une conversation : client OAuth, secret du client, phrase d'archive forte et unique, clé Fernet distincte/persistante. Les jetons et clés éphémères des bancs ne sont jamais réutilisés. L'archive ne sera récupérable qu'avec sa phrase, conservée indépendamment du serveur.

Paramètres non secrets attendus après validation de la cible :

- `DRIVE_BACKUP_PUBLIC_ORIGIN=https://gestio-stock-web.onrender.com` ; adapter si le domaine public réel diffère.
- `DRIVE_BACKUP_OAUTH_REDIRECT_URI=https://gestio-stock-web.onrender.com/core/backups/drive/callback/` ; même URI exacte dans le client Google Web, ajout manuel par le propriétaire. Les callbacks locaux ne sont pas une configuration de production.
- Clients `pg_dump` et `pg_restore` de majeure 18, GPG 2, `WEB_TIMEOUT` d'au moins 120 s, sessions Django en base et cookies Secure/HttpOnly/SameSite Lax. Ne pas raccourcir la limite du worker sous le budget applicatif de l'opération.
- TLS PostgreSQL explicitement requis. Ne retirer ni TLS, ni validation de version, ni limites de taille pour débloquer un test.

Le dépôt déclare actuellement un **Blueprint runtime Python natif**, donc le Dockerfile n'est pas la preuve de son outillage. Le Dockerfile existant n'installe pas explicitement GPG/clients 18/verrou OAuth et son CMD est distinct de start.sh : ne pas le présenter comme qualifié pour cette option. Aucun basculement de runtime/forfait n'a été fait. La présence des clients 18.6 et de GPG 2.2.40 dans le service Render est confirmée par la capture du propriétaire, sans validation d'un cycle complet sur cette cible. Aucune augmentation de quota ou offre payante ne peut être décidée pour résoudre un échec.

## Blocage de la vérification distante

L'accès direct au navigateur n'est pas disponible dans cette session ; la tentative de contrôle du navigateur existant n'a pas permis une saisie vérifiée. Aucun écran de secrets ou paramètre Render n'a été ouvert/modifié. Les captures propriétaire de **Render → gestio-stock-web → Settings → Build & Deploy** et des versions du Shell ont désormais été reçues et sont résumées ci-dessus. Il reste à qualifier le nouveau code et ses dépendances/configuration sur Render après une publication autorisée, sans lancer d'export réel. Il n'est plus demandé de répéter ces captures ni d'installer les outils déjà présents.

Avant une publication, isoler ce lot des modifications préexistantes : ne jamais utiliser `git add .`. Les changements anciens de `.env.example`, des workflows, du module backup historique et de ses tests ne font pas partie de cette préparation. Une publication/déploiement et l'activation d'un export réel nécessitent leur autorisation propre.

## Limites conservées

- Export manuel, base seule ; médias exclus, bucket GCP exclu. Aucune sauvegarde réelle créée.
- Base physique 32 Mio, TAR 4 Mio, ciphertext 5 Mio et budget applicatif 80 s : vérifier l'adéquation sans exporter de données avant une activation autorisée.
- Pas d'estimation d'espace/quota Drive avant l'envoi, pas de garantie de gratuité illimitée.
- Pas de rotation/suppression automatique des vraies archives. Les suppressions des bancs fictifs ne sont pas le comportement des vraies sauvegardes.
- Pas d'idempotence complète : une réponse d'envoi perdue peut laisser une archive distante. Aucun retry automatique ; vérifier le compte avant de relancer.
- Ni le transfert fictif ni ce préflight ne qualifient le bouton ERP réel, son dossier fixe, Render/HTTPS/Linux et la restauration de production.

Références : [outils des runtimes Render](https://render.com/docs/native-runtimes#tools-and-utilities), [binaires Windows PostgreSQL](https://www.postgresql.org/download/windows/), [archives EDB](https://www.enterprisedb.com/download-postgresql-binaries), [métadonnées et création Drive](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create).
