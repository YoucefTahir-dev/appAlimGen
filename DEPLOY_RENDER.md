# Déploiement Render

## Pré-requis

- Un dépôt Git connecté à Render.
- Une base PostgreSQL Render.
- Les variables d’environnement définies dans `render.yaml`.

## Déploiement Blueprint

1. Pousser le code sur GitHub.
2. Dans Render, choisir **New > Blueprint**.
3. Sélectionner ce dépôt.
4. Render lit `render.yaml`, crée la base PostgreSQL et le service web.

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
- `DATABASE_URL=<connection string PostgreSQL Render>`
- `ALLOWED_HOSTS=<votre-app>.onrender.com`
- `CSRF_TRUSTED_ORIGINS=https://<votre-app>.onrender.com`

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
- Les logs ne contiennent pas d’erreurs de migration.
