# Sauvegarde et restauration

Les commandes prennent en charge SQLite en local et PostgreSQL en production.
Chaque sauvegarde est accompagnée d’une empreinte SHA-256 obligatoire pour la restauration.
Le dump n’est remplacé qu’après validation, via un fichier temporaire ; l’empreinte est elle aussi
écrite par substitution atomique. Un dump incomplet ne remplace donc pas une sauvegarde existante.
Le format est détecté depuis le contenu, pas depuis l’extension.

> Une empreinte SHA-256 détecte une corruption accidentelle, mais ne prouve pas à elle seule
> l’authenticité d’une sauvegarde. Protéger le dump et son empreinte avec les mêmes contrôles d’accès.

## Créer une sauvegarde

```powershell
.\.venv\Scripts\python.exe manage.py backup_database
```

Le dossier par défaut est `backups/` et doit rester hors de Git. Pour choisir un autre emplacement :

```powershell
.\.venv\Scripts\python.exe manage.py backup_database --output D:\ERP-Backups\database.dump
```

PostgreSQL exige `pg_dump` **et** `pg_restore` : le dump custom est contrôlé avec
`pg_restore --list` avant d’être publié. Leurs chemins peuvent être fournis avec
`PG_DUMP_BIN` et `PG_RESTORE_BIN`. Utiliser une version cliente compatible avec la version majeure
du serveur. `sslmode` et `channel_binding` de la configuration Django sont transmis aux outils.

## Copie hors site S3, Cloudflare R2 ou Backblaze B2

Configurer au minimum :

```env
BACKUP_S3_BUCKET=erp-backups
BACKUP_S3_PREFIX=database-backups
BACKUP_S3_ACCESS_KEY_ID=...
BACKUP_S3_SECRET_ACCESS_KEY=...
BACKUP_S3_ENDPOINT_URL=https://...
BACKUP_S3_REGION_NAME=auto
BACKUP_S3_SERVER_SIDE_ENCRYPTION=AES256
```

Les variables `AWS_*` restent acceptées en repli, mais des identifiants `BACKUP_S3_*` séparés et
limités au préfixe de sauvegarde réduisent l’impact d’une fuite. Un endpoint HTTP est refusé ;
l’exception `BACKUP_S3_ALLOW_INSECURE_ENDPOINT=True` est réservée à un émulateur local.
Pour AWS KMS, utiliser `BACKUP_S3_SERVER_SIDE_ENCRYPTION=aws:kms` et éventuellement
`BACKUP_S3_SSE_KMS_KEY_ID=<clé>`.

Puis lancer :

```powershell
.\.venv\Scripts\python.exe manage.py backup_database --upload --delete-local-after-upload
```

Le compte objet doit utiliser des droits limités au bucket et au préfixe de sauvegarde. Activer la
rétention, le versioning et si possible le verrouillage objet chez le fournisseur. Une erreur d’envoi
conserve toujours la copie locale et son empreinte.

## Vérifier une sauvegarde

```powershell
.\.venv\Scripts\python.exe manage.py verify_database_backup backups\database.dump
```

Pour PostgreSQL, cette commande utilise `pg_restore --list`. L’option
`--allow-missing-checksum` ne doit servir qu’à diagnostiquer une ancienne archive et ne permet pas
sa restauration. Un test de restauration dans une base PostgreSQL temporaire reste obligatoire
avant le Go Live.

## Restaurer

1. Mettre l’application en maintenance et arrêter les processus web.
2. Télécharger la sauvegarde et son fichier `.sha256` dans le même dossier.
3. En production, définir temporairement `ALLOW_DATABASE_RESTORE=True`.
4. Lancer :

```powershell
.\.venv\Scripts\python.exe manage.py restore_database backups\database.dump --confirm-restore
```

La commande vérifie d’abord que l’archive correspond au moteur actif, puis crée une sauvegarde
`pre-restore-*`. La substitution SQLite est atomique. PostgreSQL utilise une transaction unique afin
qu’une erreur de `pg_restore` annule la restauration. Supprimer ensuite la variable
`ALLOW_DATABASE_RESTORE` et redémarrer l’application.

Pour SQLite, la commande refuse aussi de remplacer la base si un fichier `-journal`, `-wal` ou
`-shm` est encore présent : cela indique qu’un processus peut toujours utiliser la base. Ne pas
supprimer ces fichiers à l’aveugle ; arrêter proprement tous les processus puis recommencer.

Ne jamais tester une restauration pour la première fois sur la base de production.

## Sauvegarde quotidienne sans shell Render

Le workflow `.github/workflows/database-backup.yml` crée chaque nuit une archive
PostgreSQL au format custom, la valide avec `pg_restore --list`, la chiffre en
AES-256 puis conserve uniquement la copie chiffrée pendant 14 jours.

Configurer dans **GitHub > Settings > Secrets and variables > Actions** :

- `BACKUP_DATABASE_URL` : URL PostgreSQL de production, distincte des fichiers du dépôt ;
- `BACKUP_PASSPHRASE` : phrase secrète unique d’au moins 24 caractères, conservée aussi dans le
  coffre-fort de l’entreprise et jamais dans le dépôt.

Lancer ensuite manuellement le workflow une première fois et télécharger son
artefact. Pour préparer un test de restauration :

```bash
sha256sum --check database-*.dump.gpg.sha256
gpg --batch --decrypt --output database.dump database-*.dump.gpg
pg_restore --list database.dump
```

Le fichier `.sha256` référence uniquement le nom du fichier chiffré : exécuter la vérification dans
le dossier extrait de l’artefact. Le workflow efface le dump en clair même si le chiffrement échoue,
utilise un KDF renforcé et ne publie que le dump chiffré. L’image `postgres:17-alpine` doit être mise
à jour si la base de production change de version majeure.

La restauration doit être essayée sur une base PostgreSQL temporaire, jamais
directement sur la production. Une sauvegarde sans test de restauration ne
valide pas la porte de sortie Go Live.

La rétention GitHub de 14 jours est une protection courte durée. Pour la continuité d’activité,
copier périodiquement les artefacts chiffrés vers un stockage à rétention longue et surveiller les
échecs du workflow planifié.
