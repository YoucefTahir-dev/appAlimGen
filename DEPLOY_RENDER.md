# Déploiement Render

## Pré-requis

- Un dépôt Git connecté à Render.
- Une base PostgreSQL existante, par exemple Neon ou Render PostgreSQL.
- Les variables d’environnement définies dans `render.yaml`.

## Déploiement Blueprint

1. Pousser le code sur GitHub.
2. Dans Render, choisir **New > Blueprint**.
3. Sélectionner ce dépôt.
4. Render lit `render.yaml` et crée le service web.
5. Renseigner manuellement `DATABASE_URL` avec l’URL PostgreSQL existante.

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

Exemple de variable `DATABASE_URL` :

```text
postgresql://user:password@host/database?sslmode=require
```

Avec Neon, copier l’URL complète fournie par Neon, y compris les paramètres de fin
comme `sslmode=require` ou `channel_binding=require`.

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
