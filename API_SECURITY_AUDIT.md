# Audit sécurité API v1

## Contrôles validés par le code et les tests

- authentification JWT, expiration, rotation, blacklist et logout ;
- permissions Django identiques au Web, refus individuels et HTTP 403 ;
- accès direct aux factures protégé (IDOR) ;
- rate limits anonymes, authentifiés et authentification ;
- ORM Django et serializers typés contre injections courantes ;
- champs générés/audit en lecture seule contre mass assignment ;
- uploads réutilisant les validateurs d'image et de document ;
- erreurs JSON structurées sans traceback ni secret ;
- prix, stock, numérotation et mouvements dans des transactions serveur ;
- aucune chaîne Neon dans le contrat OpenAPI ou les réponses.

## Risques et mesures

| Niveau | Risque | Mesure |
|---|---|---|
| Moyen | vol d'un access token sur terminal compromis | durée 10 min, stockage Keystore/Credential Manager, révocation refresh |
| Moyen | abus distribué contournant une limite par IP | supervision, limites par utilisateur et reverse proxy de confiance |
| Moyen | permissions objets futures plus fines | ajouter une règle objet si les données deviennent limitées par magasin |
| Faible | payload imprimante mal interprété par un firmware | transport local, profil inactif, diagnostic avant activation |

## Portes de sortie

Le pipeline Linux doit exécuter tests PostgreSQL, validation OpenAPI et `pip-audit`.
Une recette HTTPS sur le staging isolé et un test matériel RPP02N restent obligatoires
avant de qualifier l'impression réelle.
