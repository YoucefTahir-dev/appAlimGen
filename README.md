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
