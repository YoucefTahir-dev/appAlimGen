# Readiness backend Web + Android + Windows

## Architecture actuelle et cible

Le Web conserve les sessions et templates Django. Android et le futur client
Windows utilisent `/api/v1/` avec JWT. Les trois canaux convergent vers les mêmes
services métier et une seule base PostgreSQL Neon.

## Impression

Le backend gère profils d'imprimante, formats, préférences utilisateur, données de
ticket et diagnostics. Android/Windows gèrent localement Bluetooth, USB, TCP ou le
spooler. Le serveur cloud n'essaie jamais d'atteindre le matériel du magasin.

RPP02N est traitée initialement comme candidate Generic ESC/POS, avec driver de
diagnostic isolé et recommandation raster pour l'arabe. Le protocole n'est pas
déclaré compatible tant que la procédure réelle n'est pas terminée.

## Données et migrations

- `ProductPackaging` : nom, facteur, prix, code-barres, statut ;
- instantanés de conditionnement sur `SaleLine` ;
- `PrinterProfile`, `PrintProfile`, `UserPrinterPreference` ;
- stock toujours exprimé dans l'unité de base ;
- anciennes ventes migrées comme unité de base, sans réécriture financière.

## Sécurité et qualité

JWT distinct, permissions/refus serveur, IDOR, throttling, validation métier,
transactions et audit sont couverts. CI Linux/PostgreSQL exécute tests, migrations,
OpenAPI, compilation et audit de dépendances.

La validation locale finale couvre 186 tests. Le lock Python haché ne présente
aucune vulnérabilité connue au moment de l'audit.

## Risques restants

1. Une branche Neon et un bucket staging doivent être créés hors production.
2. La recette HTTPS doit être observée sur staging.
3. Le vrai matériel RPP02N doit valider ESC/POS, QR, raster arabe et coupe.

Le workflow GitHub Actions du commit `9d9190b` est vert : installation hachée,
migrations, contrôles de production, OpenAPI, suite PostgreSQL (avec concurrence)
et audit des dépendances ont tous réussi.

## Conclusion

🟡 PRÊT AVEC RÉSERVES
