# Android readiness — remédiation du 13 septembre 2026

## Verdict backend

Le backend est **READY pour démarrer le client Android connecté**, sous réserve du passage de la CI PostgreSQL et de la recette physique RPP02N. Aucun code Android n’a été commencé pendant cette phase.

Les blocages backend identifiés par l’audit précédent sont traités :

- révocation globale des JWT après changement ou réinitialisation du mot de passe ;
- bons de chargement, stock opérateur isolé, validation, vente et clôture transactionnelles ;
- API des paiements ;
- idempotence des ventes, achats, paiements et mutations de chargement ;
- contrat OpenAPI régénéré et validé ;
- couverture de tests SQLite locale et tests de concurrence PostgreSQL dans la CI.

## Contrat Android

Base : `/api/v1/`. Authentification : `Authorization: Bearer <access>`. Les réponses JSON utilisent l’enveloppe `success/data` et les erreurs `success/error` avec `code`, `message` et éventuellement `details`. Les dates sont ISO 8601 et les montants Decimal sont transmis sous forme de chaînes.

Pour toute création commerciale ou mutation de chargement, Android doit envoyer un `Idempotency-Key` stable (UUID recommandé) et le conserver jusqu’à obtention de la réponse. Une reprise avec la même clé et le même corps renvoie la première réponse ; une réutilisation avec un autre corps renvoie `IDEMPOTENCY_KEY_REUSED`. L’absence de clé reste tolérée pour compatibilité avec les clients actuels.

Lors d’un changement/reset de mot de passe, les anciens access et refresh tokens renvoient `TOKEN_REVOKED`. La migration initialise `auth_token_version=1` : tous les jetons émis avant le déploiement, sans ce claim, seront invalidés et les utilisateurs devront se reconnecter une fois.

Le stock de tournée est serveur : Android ne doit ni télécharger le stock global pour le filtrer localement, ni recalculer les soldes. Une vente créée par un utilisateur possédant un chargement actif est automatiquement rattachée à celui-ci et prélevée du stock opérateur, jamais une seconde fois du dépôt.

## Validation

- `manage.py check` : OK.
- migrations : cohérentes, aucune migration manquante.
- OpenAPI : génération et validation sans avertissement.
- suite locale : **281 tests, OK, 5 ignorés** ; les tests ignorés dépendent de PostgreSQL ou d’une infrastructure externe.
- le workflow GitHub exécute PostgreSQL 16, migrations, `check --deploy`, OpenAPI, tests et audit de dépendances.

## Risques résiduels

- La recette physique Bluetooth/RPP02N, y compris arabe raster, doit être faite sur l’appareil.
- Android doit sérialiser le refresh JWT localement ; la rotation/blacklist reste celle de SimpleJWT.
- L’idempotence sans en-tête est volontairement compatible mais n’offre aucune protection au client qui omet la clé.
- Les consoles Render, Neon et GCP ne sont pas certifiées par les tests locaux.

Critical vulnerabilities remaining: **0**.

High vulnerabilities remaining: **0**.

FINAL VERDICT: **READY backend, conditionné au vert de la CI PostgreSQL avant démarrage Android.**
