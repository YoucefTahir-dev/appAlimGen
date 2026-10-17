# Conversion contrôlée des stocks historiques carton → pièce

Date du diagnostic local : 17 octobre 2026

## Décision métier appliquée

- Les quantités des produits `legacy` sont interprétées comme des cartons uniquement pendant la reprise contrôlée.
- Un carton validé existant fournit son facteur.
- En l’absence de facteur validé, le facteur provisoire est `30`.
- Les stocks dépôt et opérateurs sont convertis dans l’unité canonique pièce.
- Aucun prix historique, facture, achat, vente ou ancien mouvement n’est rejoué ou recalculé.
- Un conditionnement provisoire créé par la reprise reste inactif à l’achat et à la vente jusqu’à configuration manuelle de ses prix.

## Diagnostic et simulation locale

La commande exécutée était strictement en lecture seule :

```powershell
python manage.py prepare_historical_stock_conversion --format json
```

Résultat sur la base locale de développement, qui ne doit pas être confondue avec Neon production :

| Contrôle | Résultat |
|---|---:|
| Produits historiques détectés | 11 |
| Facteurs confirmés | 0 |
| Facteurs provisoires à 30 | 11 |
| Produits éligibles | 11 |
| Produits bloqués | 0 |
| Stock dépôt avant, interprété en cartons | 522 |
| Stock dépôt simulé après, en pièces | 15 660 |
| Seuils minimum avant, interprétés en cartons | 30 |
| Seuils minimum simulés après, en pièces | 900 |
| Stock opérateur détecté | 0 |
| Empreinte SHA-256 du rapport | `b5fdf2e96432e515e99d327398c40a884065a70799c6ec53f051b9b09d3c502d` |

Ce résultat ne décrit pas la production. Le même diagnostic devra être généré sur une copie restaurée de Neon avant toute autorisation d’application.

## Garanties techniques

- Une ligne `HistoricalStockConversion` unique par produit empêche une double conversion.
- Les quantités historiques dépôt et opérateurs sont conservées séparément.
- Le seuil de stock minimum est converti et audité dans la même unité canonique.
- Chaque variation produit et opérateur est écrite dans un journal immuable.
- Le facteur, l’utilisateur, la date, la sauvegarde et l’empreinte de simulation sont conservés.
- Toute la conversion d’un lot est atomique.
- Un bon de chargement brouillon, validé ou en cours bloque la conversion du produit.
- Un ancien conditionnement ambigu simplement nommé `Carton` bloque la conversion au lieu d’être deviné.
- Les factures et leurs instantanés de conditionnement ne sont jamais modifiés.

## Correction ultérieure du facteur

La fiche produit expose l’action « Corriger le conditionnement et régulariser le stock » aux utilisateurs autorisés.

Sans mouvement depuis la conversion, le système recalcule les stocks à partir des cartons historiques. Exemple : `100 × 30 = 3 000`, puis correction à `24`, donc `100 × 24 = 2 400` et écart `−600`.

Dès qu’un achat, une vente, un transfert, un chargement ou un retour a eu lieu, le recalcul naïf est définitivement désactivé. Un inventaire physique du dépôt et de chaque stock opérateur devient obligatoire. La correction enregistre l’ancien et le nouveau facteur, les stocks avant/après, l’écart, le motif, l’utilisateur et l’horodatage.

## Procédure de sauvegarde et d’application future

1. Mettre les opérations en pause et fermer ou annuler tous les chargements ouverts.
2. Créer une sauvegarde ou une branche Neon datée.
3. Restaurer cette sauvegarde dans une base isolée et exécuter les contrôles Django et la simulation.
4. Conserver la référence de sauvegarde et le SHA-256 du rapport.
5. Faire valider la liste provisoire/confirmée et les quantités avant/après.
6. Après autorisation explicite seulement, activer temporairement `ALLOW_HISTORICAL_STOCK_CONVERSION=True` et utiliser les trois protections :

```powershell
python manage.py prepare_historical_stock_conversion `
  --output simulation-production.json --format json `
  --apply `
  --confirm APPLY-HISTORICAL-STOCK-CONVERSION `
  --backup-confirmed `
  --backup-reference "<identifiant-sauvegarde-neon>"
```

7. Désactiver immédiatement `ALLOW_HISTORICAL_STOCK_CONVERSION`.
8. Contrôler les totaux, le journal, les stocks opérateurs et un échantillon de factures historiques.

## Rollback

- Avant toute conversion de données, une migration de schéma peut être retirée normalement.
- Après conversion de données, ne pas supprimer ni modifier les mouvements d’audit.
- Si la transaction échoue, tout le lot est annulé automatiquement.
- Si une anomalie métier est constatée après succès, suspendre les écritures et restaurer la sauvegarde Neon vérifiée référencée dans les conversions.
- Si des mouvements ont déjà eu lieu après la conversion, ne jamais tenter une division automatique par 30 : restaurer la sauvegarde ou réaliser un inventaire physique contrôlé.

## Statut

Solution préparée et testée localement. Aucune conversion Neon, aucun déploiement, aucun commit et aucun push n’ont été effectués.
