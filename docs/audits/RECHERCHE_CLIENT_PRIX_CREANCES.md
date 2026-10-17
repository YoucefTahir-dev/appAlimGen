# Recherche client, prix carton/pièce et créances clients

Date : 17 octobre 2026

## Résultat livré

Les trois lots sont maintenant couverts sur le Web et, lorsque pertinent, dans l’application Android :

1. recherche distante et sécurisée des clients dans les ventes Web ;
2. tarification automatique par type de client et conditionnement carton/pièce ;
3. compte de créances client, règlements, historique et annulations traçables.

## Lot 1 — recherche client

Le formulaire de vente utilise une autocomplétion distante à partir de deux caractères, temporisée, limitée à 20 résultats et soumise aux permissions. La recherche porte sur le nom, le téléphone et le NIF. Elle restaure le client existant en modification et refuse une saisie libre qui ne correspond à aucun identifiant valide.

## Lot 2 — prix automatique carton/pièce

Le moteur existant `apps.inventory.pricing` reste l’unique source des prix. Les tarifs détail, gros et super-gros sont indépendants pour l’unité de base et chaque conditionnement. Les lignes commerciales conservent leurs instantanés (`packaging_name`, facteur, quantité saisie, coût et prix unitaire) afin qu’une évolution future de la fiche produit ne réécrive pas l’historique.

Évolutions ajoutées :

- recalcul du prix proposé lors de la sélection du produit, du client ou du conditionnement sur le Web et Android ;
- conservation explicite d’un prix saisi manuellement au lieu de l’écraser silencieusement ;
- avertissement visible demandant de vérifier le prix manuel après changement de client ou d’unité ;
- remise à zéro du statut « manuel » quand l’utilisateur choisit un autre produit et accepte donc un nouveau tarif automatique ;
- quantité de stock toujours enregistrée dans l’unité canonique, sans multiplier deux fois le facteur carton.

## Lot 3 — créances clients

La rubrique « Créances clients » est accessible par permission sur le Web et Android. La liste montre le total facturé, le total encaissé, le solde, le nombre de factures impayées, le dernier règlement et les factures historiques qui restent à rapprocher.

Le détail client fournit :

- les factures suivies et leurs statuts impayé, partiel ou soldé ;
- l’historique chronologique des règlements ;
- un relevé imprimable Web ;
- l’enregistrement d’un règlement avec date, mode, référence externe et observation ;
- l’annulation traçable avec auteur, date et motif.

### Source du solde

Le champ historique `Client.balance` n’est pas utilisé comme seconde source comptable. Le solde est calculé à partir des ventes dont `payment_tracking_initialized=True` moins les règlements actifs associés. Les paiements annulés et les factures historiques non rapprochées sont exclus du total ; ces dernières sont signalées séparément.

### Affectation retenue

Chaque encaissement est affecté à une facture précise. Ce choix évite une ventilation implicite et invérifiable sur plusieurs factures. Le surpaiement est refusé sous verrou transactionnel. Une clé d’idempotence protège les doubles soumissions Web et l’API mobile conserve son mécanisme d’idempotence HTTP.

### Immutabilité et concurrence

Un règlement n’est jamais supprimé physiquement. `Payment.delete()` est interdit et l’action d’annulation conserve la ligne. La création et l’annulation verrouillent le document commercial concerné ; le montant actif est contrôlé dans la même transaction. Les totaux des listes et du tableau de bord ignorent les paiements annulés.

## API mobile

- `GET /api/v1/receivables/?status=debtors|settled|all&search=...`
- `GET /api/v1/receivables/{client_id}/`
- `POST /api/v1/payments/` avec facture, montant, mode, date, référence externe et observation
- `POST /api/v1/payments/{payment_id}/cancel/` avec un motif obligatoire

Les permissions dédiées sont :

- `commerce.view_receivables` ;
- `commerce.add_customerpayment` ;
- `commerce.cancel_customerpayment`.

## Migration

La migration additive `commerce.0016` ajoute les métadonnées de règlement et d’annulation ainsi que la clé d’idempotence. Elle ne supprime ni ne transforme aucune donnée existante.

## Validation avant déploiement

- `manage.py check` et `makemigrations --check` ;
- tests Django ciblés puis suite complète ;
- tests API créances et paiements ;
- tests navigateur recherche client et formulaire de vente ;
- `flutter analyze`, tests Flutter et build APK release ;
- contrôle manuel final du diff et des migrations.

Les résultats exacts sont reportés dans le compte rendu du commit. Aucun changement direct des données de production n’est nécessaire.

## Déploiement et rollback

Déploiement : sauvegarder la base, déployer le backend, exécuter les migrations, vérifier `/healthz/` et `/readyz/`, puis publier l’APK Android. Affecter les trois nouvelles permissions aux rôles personnalisés qui doivent utiliser la rubrique.

Rollback applicatif : redéployer les commits précédents. La migration étant additive, les colonnes peuvent rester en place sans risque pour l’ancienne version. Ne pas supprimer les colonnes si des règlements ont déjà été annulés ou enrichis après déploiement.
