# El Amine ERP — Gestion commerciale & stock

Application web ERP Django pour la gestion commerciale, les ventes, les achats, les clients, les fournisseurs, les factures PDF et le stock de l’entreprise **El Amine**.

## Stack technique

- Backend : Django 5
- Base de données locale : SQLite
- Base de données production : PostgreSQL compatible Render / Neon
- Frontend : Bootstrap 5, HTML, CSS, JavaScript
- PDF : ReportLab
- Static files : WhiteNoise
- CI : GitHub Actions
- Déploiement : Render

## Architecture

```text
apps/
  accounts/   Authentification, rôles, permissions, récupération admin
  core/       Dashboard, paramètres société, logs d’audit, sécurité
  inventory/  Produits, stock, clients, fournisseurs, imports/exports
  commerce/   Achats, ventes, factures PDF
gestio_stock/ Configuration Django
static/       CSS, logo et assets
templates/    Templates globaux
scripts/      Scripts d’exploitation locale
```

## Installation locale

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Application locale :

```text
http://127.0.0.1:8000
```

Pour un démarrage local simple, utiliser dans `.env` :

```env
DJANGO_DEBUG=True
DATABASE_ENGINE=sqlite
DATABASE_URL=
```

## Tests et validation

Commandes recommandées avant chaque merge :

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

Pour vérifier les paramètres production :

```powershell
.\.venv\Scripts\python.exe manage.py check --deploy
```

## Déploiement Render

Le dépôt contient :

- `render.yaml`
- `build.sh`
- `start.sh`
- `runtime.txt`
- `Procfile`

Variables Render minimales :

```env
DJANGO_DEBUG=False
SECRET_KEY=<clé longue et aléatoire>
ALLOWED_HOSTS=.onrender.com
CSRF_TRUSTED_ORIGINS=https://*.onrender.com
DATABASE_URL=<URL PostgreSQL Render ou Neon>
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
SESSION_IDLE_TIMEOUT_SECONDS=1800
```

Voir aussi [DEPLOY_RENDER.md](DEPLOY_RENDER.md).

## Workflow Git

`main` représente uniquement la version stable de production.

Ne pas développer directement sur `main`.

Branches recommandées :

```text
feature/nom-fonctionnalite
fix/nom-correction
security/nom-durcissement
refactor/nom-refactor
docs/nom-documentation
chore/nom-maintenance
```

Process :

1. Créer une branche depuis `main`.
2. Faire des commits petits et explicites.
3. Exécuter les validations.
4. Fusionner vers `main` uniquement si tout passe.
5. Taguer les versions stables.

Messages de commit recommandés :

```text
feat(products): ajout ...
fix(stock): correction ...
security(csrf): protection ...
refactor(views): simplification ...
docs(render): mise à jour ...
test(stock): ajout tests ...
chore(git): nettoyage ...
```

## Versions

Les versions stables sont taguées :

```text
v1.0.0
v1.1.0
v2.0.0
```

Le détail est documenté dans [CHANGELOG.md](CHANGELOG.md).

## Sécurité

Le projet active notamment :

- validation `SECRET_KEY` forte en production ;
- cookies sécurisés en production ;
- CSRF Django ;
- `X-Frame-Options=DENY` ;
- CSP ;
- HSTS configurable ;
- expiration d’inactivité ;
- journalisation sécurité ;
- validation des uploads ;
- audit dépendances via `pip-audit`.

## Récupération admin

Voir [PASSWORD_RECOVERY.md](PASSWORD_RECOVERY.md).
