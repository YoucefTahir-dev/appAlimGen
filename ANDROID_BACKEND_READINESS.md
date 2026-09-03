# Android Backend Readiness

## État

API Django v1 centralisée, JWT, permissions, pagination, conditionnements partagés,
stock atomique, factures, tickets et contrat d'impression local sont implémentés.
Le schéma versionné est `openapi.yaml`.

## Tests locaux

- suite complète : 186 tests réussis, dont le contrôle Dashboard/Excel et graphiques ;
- tests API et impression ciblés : réussis ;
- conversion pièce/pack/carton et refus sous coût : couverts ;
- stock insuffisant et conditionnement inactif/invalide : couverts ;
- JWT invalide/expiré/rotation/logout : couverts ;
- IDOR/permissions : couverts ;
- test concurrence : présent, exécuté uniquement sur PostgreSQL ;
- bornes de requêtes : produits, dashboard, ventes, factures et stock.
- audit du lock Python : aucune vulnérabilité connue après passage à Django REST Framework 3.17.2.

## Staging et CI

Le workflow déclenche désormais les branches `feature/**`, valide migrations,
paramètres de déploiement, sources, OpenAPI, suite PostgreSQL et dépendances.
Le résultat distant et la recette HTTPS staging doivent être renseignés après push.

## Réserves

- recette réelle Neon staging non exécutée depuis cet environnement ;
- diagnostic physique RPP02N à réaliser depuis Android ;
- aucun déploiement ou test destructif en production n'a été effectué.

## Verdict

🟡 BACKEND VALIDÉ AVEC RÉSERVES
