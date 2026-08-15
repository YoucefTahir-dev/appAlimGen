# Déploiement Render

## Pré-requis

- Un dépôt Git connecté à Render.
- Une base PostgreSQL existante, par exemple Neon ou Render PostgreSQL.
- Un bucket S3 compatible (AWS S3, Cloudflare R2 ou Backblaze B2) pour les médias.
- Les variables d’environnement définies dans `render.yaml`.

## Déploiement Blueprint

1. Pousser le code sur GitHub.
2. Dans Render, choisir **New > Blueprint**.
3. Sélectionner ce dépôt.
4. Render lit `render.yaml` et crée le service web.
5. Renseigner manuellement `DATABASE_URL` avec l’URL PostgreSQL existante.
6. Renseigner les variables du stockage objet demandées par le Blueprint.

## Déploiement manuel

Build command :

```bash
bash build.sh
```

Start command :

```bash
bash start.sh
```

Variables minimales :

- `DJANGO_DEBUG=False`
- `SECRET_KEY=<clé longue et aléatoire>`
- `DATABASE_URL=<connection string PostgreSQL existante>`
- `ALLOWED_HOSTS=<votre-app>.onrender.com`
- `CSRF_TRUSTED_ORIGINS=https://<votre-app>.onrender.com`
- `MEDIA_STORAGE_BACKEND=s3`
- `AWS_ACCESS_KEY_ID=<identifiant du bucket>`
- `AWS_SECRET_ACCESS_KEY=<secret du bucket>`
- `AWS_STORAGE_BUCKET_NAME=<nom du bucket>`
- `AWS_S3_ENDPOINT_URL=<endpoint S3 du fournisseur>`
- `AWS_S3_REGION_NAME=<région, ou auto pour R2>`
- `MEDIA_CSP_ORIGINS=https://<domaine public ou endpoint du bucket>`

Exemple de variable `DATABASE_URL` :

```text
postgresql://user:password@host/database?sslmode=require
```

Avec Neon, copier l’URL complète fournie par Neon, y compris les paramètres de fin
comme `sslmode=require` ou `channel_binding=require`.

Ne jamais placer `DATABASE_URL`, les clés S3 ou `SECRET_KEY` dans GitHub. Les saisir uniquement
dans **Render > Environment**. Les médias locaux ne sont pas acceptés sur une instance Render
Free, car son système de fichiers est éphémère.

### Alternative Render Disk

Sur une instance Render payante, il est possible d’utiliser un disque monté, par exemple sous
`/var/data`. Configurer alors :

```env
MEDIA_STORAGE_BACKEND=filesystem
MEDIA_ROOT=/var/data/media
MEDIA_FILESYSTEM_PERSISTENT=True
```

Le stockage objet reste recommandé pour simplifier les redéploiements et les futures montées en charge.

### Migrer des médias locaux existants

Après configuration du bucket, lancer d’abord une simulation depuis la machine qui possède le
dossier `media` complet :

```powershell
.\.venv\Scripts\python.exe manage.py migrate_media_storage .\media
```

Puis appliquer :

```powershell
.\.venv\Scripts\python.exe manage.py migrate_media_storage .\media --apply
```

La commande ne supprime jamais les fichiers locaux et met à jour les noms en base si le stockage
cible doit les renommer. La simulation ne crée aucun objet. En mode `--apply`, chaque copie est
relue depuis le stockage cible et comparée en SHA-256 avec la source. Un objet déjà présent avec un
contenu différent bloque la migration au lieu d’être écrasé silencieusement.

Par défaut, une référence en base dont le fichier source est absent fait échouer la commande. Après
inventaire manuel uniquement, il est possible de poursuivre les autres fichiers avec :

```powershell
.\.venv\Scripts\python.exe manage.py migrate_media_storage .\media --apply --allow-missing
```

La commande est réexécutable : les objets déjà présents et identiques sont validés puis ignorés.

## Vérification

Après le déploiement :

```bash
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Puis vérifier :

- `/` affiche la page de connexion.
- `/admin/` redirige vers la connexion admin.
- Les fichiers statiques sont servis.
- Une photo ou un justificatif reste disponible après redéploiement.
- Les logs ne contiennent pas d’erreurs de migration.

## Sauvegardes

Voir [BACKUP_RESTORE.md](BACKUP_RESTORE.md). Avant l’ouverture aux utilisateurs, effectuer une
sauvegarde puis une restauration réelle dans une base PostgreSQL temporaire et consigner le résultat.
