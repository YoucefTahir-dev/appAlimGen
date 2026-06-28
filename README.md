# El Amine ERP - Gestion Commerciale & Stock

Application web ERP pour la gestion commerciale, achats, ventes et stock.

## Technologie
- Backend: Django
- Base de données: SQLite pour le mode local / PostgreSQL pour production
- Frontend: Bootstrap 5, HTML, CSS, JavaScript
- Génération PDF: ReportLab

## Installation rapide

1. Copier `.env.example` vers `.env`.
2. Démarrer les services Docker:

   ```bash
   docker compose up -d --build
   ```

3. Exécuter les migrations et créer un utilisateur administrateur:

   ```bash
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```

4. Charger les données de démonstration:

   ```bash
   docker compose exec web python scripts/seed_demo.py
   ```

5. Accéder à l'application : http://localhost:8000

## Installation Windows (service)

Si vous ne souhaitez pas utiliser Docker et que vous êtes sur Windows, vous pouvez installer l'application comme un service Windows en mode production avec Waitress.

1. Ouvrir PowerShell en tant qu'administrateur.
2. Créer l'environnement virtuel et installer les dépendances :

   ```cmd
   .\scripts\ensure_venv.bat
   ```

3. Exécuter les migrations :

   ```cmd
   .\.venv\Scripts\python.exe manage.py migrate
   ```

4. Installer et démarrer le service Windows :

   ```cmd
   .\scripts\manage_windows_service.bat install
   ```

5. Gérer le service :

   ```cmd
   .\scripts\manage_windows_service.bat status
   .\scripts\manage_windows_service.bat stop
   .\scripts\manage_windows_service.bat start
   .\scripts\manage_windows_service.bat restart
   .\scripts\manage_windows_service.bat uninstall
   ```

Le service écoute par défaut sur le port `8000` et enregistre les logs dans le dossier `logs/`.

> Assurez-vous d'abord que `.env` est configuré et que `DJANGO_DEBUG=False` en production.

## Fonctionnalités
- Authentification sécurisée
- Gestion des produits et stocks
- Achats, ventes, facturation PDF
- Gestion clients et fournisseurs
- Rapports PDF et Excel
- Administration des utilisateurs
- Paramètres société

## Structure
- `apps/accounts`: gestion des utilisateurs et rôles
- `apps/core`: tableau de bord, paramètres société et audit
- `apps/inventory`: produits, stock, fournisseurs, clients
- `apps/commerce`: achats, ventes, factures, paiements

## GitHub CI
Ce dépôt utilise GitHub Actions pour exécuter les tests automatiquement sur chaque push ou pull request vers `main`.

## Logo
Placez le logo de l'entreprise dans `static/img/logo.png`.

## Notes
- Utilisez le mode production `DJANGO_DEBUG=False`
- Protégez la base de données et effectuez des sauvegardes régulières.
