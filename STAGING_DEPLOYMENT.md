# Déploiement staging isolé

## Règle

Le staging possède son propre service Django, sa propre branche/base Neon et son
propre bucket média. Ne jamais copier `DATABASE_URL` de production dans ce service.

## Neon

1. Dans le projet Neon, créer une branche `staging-android-api` depuis un snapshot
   contrôlé ou une branche vide selon la politique de données.
2. Créer un rôle dédié au staging et relever uniquement la chaîne de connexion de
   cette branche.
3. Vérifier dans l'URL le nom d'hôte et la base staging avant toute migration.
4. Interdire les données personnelles de production ou les anonymiser.

## Backend staging

1. Déployer la branche Git `feature/android-api` dans un service distinct.
2. Renseigner les variables de `.env.staging.example` dans le coffre du fournisseur.
3. Générer deux secrets indépendants d'au moins 64 caractères pour `SECRET_KEY` et
   `JWT_SIGNING_KEY`.
4. Utiliser un bucket staging, jamais le bucket production.
5. Exécuter `python manage.py migrate --noinput`, puis `collectstatic --noinput`.
6. Vérifier `/healthz/`, `/readyz/`, `/api/schema/` et HTTPS.

## Recette destructive autorisée uniquement en staging

- login, refresh, logout et réutilisation de refresh refusée ;
- création client/produit/conditionnement/vente ;
- contrôle du stock et du mouvement ;
- PDF, ticket 58/80 et `print-data` ;
- permissions vendeur/gestionnaire ;
- AuditLog ;
- deux ventes simultanées du même stock.

Nettoyer les données de test après la recette. Conserver les journaux et le rapport,
mais aucun token, mot de passe ou `DATABASE_URL`.
