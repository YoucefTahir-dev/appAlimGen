# Audit de sécurité — clôture de remédiation du 13 septembre 2026

## Synthèse

Le périmètre backend requis pour préparer Android ne comporte plus de vulnérabilité Critical ou High connue. L’audit reste une revue du code et des tests, pas une certification des consoles Render/Neon/GCP ni du matériel.

## Authentification

Les access et refresh tokens portent `token_version`. `VersionedJWTAuthentication` et le serializer de refresh comparent ce claim à `User.auth_token_version`. Tout changement de mot de passe Web, reset administrateur, commande `reset_admin` ou endpoint API incrémente la version. Les jetons antérieurs sont rejetés avec le code stable `TOKEN_REVOKED`.

La migration invalide volontairement les anciens jetons ne possédant pas le claim : reconnexion unique attendue lors du déploiement.

## Autorisation et isolation

Le RBAC Django reste commun au Web et à DRF. Les permissions dédiées couvrent création, modification, validation, clôture et vue globale des chargements. Par défaut, un utilisateur ne voit que ses chargements et son stock opérateur. Les ventes sont automatiquement rattachées au chargement actif de leur créateur.

## Intégrité et concurrence

Validation et clôture utilisent `transaction.atomic`, `select_for_update`, ordre stable de verrouillage et mises à jour conditionnelles. Une contrainte conditionnelle interdit deux chargements actifs par opérateur. Le stock dépôt est décrémenté au chargement ; les ventes opérateur ne touchent ensuite que `OperatorStock`. La clôture retourne le reliquat au dépôt. Les deux journaux sont append-only.

L’idempotence persiste `(user, operation, key)`, le hash canonique de la requête et la première réponse. La contrainte unique protège les courses concurrentes. Une commande de purge avec rétention configurable est fournie.

## API et exposition

Les paiements exigent une vente ou un achat exactement, un montant positif, le droit de modifier le document et refusent le surpaiement sous verrou. OpenAPI documente JWT Bearer, erreurs enveloppées, médias PDF/HTML et l’en-tête d’idempotence.

## Résultats

- Critical restant : **0**.
- High restant : **0**.
- Medium opérationnel : idempotence tolérée sans en-tête pour compatibilité ; recette d’infrastructure et matériel à effectuer.
- Tests locaux : **274 OK, 5 ignorés** ; concurrence exécutée par la CI PostgreSQL 16.
