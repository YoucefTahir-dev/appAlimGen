# P1 — Paiements, reprise de dette et compatibilité

Périmètre autorisé : points 2 et 3 de la suite d’audit. État : correctifs en qualification, pas de publication attestée par ce document. Aucun accès aux données de production pour les essais.

## Paiements

Les trois formulaires Web (vente, achat, affectation à une facture client) utilisent désormais le registre transactionnel de rejeu, avec jetons signés de douze heures. Le résultat est rejoué avant de revalider une facture déjà soldée, mais après vérification des droits actuels et du périmètre du document. Un contenu modifié avec la même clé est refusé.

Les anciennes clés de paiement sont admises uniquement sur ces trois routes, pas sur les formulaires de ventes/achats. Un ancien paiement n’est adopté que si l’auteur, le document/client, le montant, le type, la référence, les notes et la date explicite concordent. Une date historique absente ne permet pas de prouver l’intention : refus explicite et invitation à consulter l’historique. Aucun nouveau paiement n’est créé pour résoudre une ambiguïté.

Le rejeu API d’une affectation client recontrôle également les paiements et factures encore accessibles. Il ne revalide pas la dette restant due. Une annulation reste une annulation : un rejeu n’active pas de nouveau le règlement.

Les nouveaux clients peuvent envoyer `Idempotency-Policy: required-v1` sur les neuf mutations déjà protégées : création/modification vente et achat, création paiement, affectation client, création/validation/clôture chargement. L’absence de clé devient alors une erreur avant mutation. Une politique inconnue est refusée.

`API_IDEMPOTENCY_REQUIRED` reste **false par défaut** pour préserver les versions installées. Son activation globale attend la qualification et la décision de transition des clients. Cette compatibilité n’est pas présentée comme une obligation générale déjà active. Les soumissions historiques sans clé/jeton restent non protégées contre un nouvel envoi ; les formulaires nouvellement rendus et le client mis à jour portent leurs clés. La rétention existante du registre API reste de sept jours.

Les intentions Android conservent leur clé pendant la vie de l’écran/du contrôleur et, pour les créances, leur date de première soumission. Les clés sont isolées par action et session. Ce changement ne constitue pas une file de commandes persistante après fermeture ou réinstallation de l’application. Signatures et distribution des binaires sont hors de ce lot.

## Dette antérieure client

`Client.balance` représente le **reste de dette antérieure**, pas le montant initial historique. Les règlements diminuent ce reste ; leur annulation le rétablit. Aucun recalcul de l’historique ni changement de signe n’est effectué.

Les fiches clients ordinaires, Web/admin/API, ne peuvent plus corriger cette valeur. Un ancien PATCH Android qui répète la même valeur est accepté sans l’écrire ; une valeur différente est refusée. La sauvegarde des champs de profil exclut systématiquement le solde, même si l’objet chargé est devenu périmé après un règlement concurrent. À la création, la saisie d’une dette initiale reste possible avec le droit existant d’ajouter un client ; une saisie non nulle est journalisée.

La correction dédiée nécessite `inventory.adjust_client_balance`, un montant attendu, un nouveau montant, un motif non blanc et une clé stable. Elle verrouille le client comme les règlements, compare le solde attendu puis écrit atomiquement le nouveau reste et le journal. Deux corrections fondées sur le même ancien solde ne peuvent pas réussir toutes les deux.

API : `POST /api/v1/clients/{id}/adjust-balance/` avec `Idempotency-Key`, `expected_balance`, `new_balance`, `reason`. Historique paginé en lecture seule : `GET /api/v1/clients/{id}/balance-adjustments/`, avec le même droit financier. Aucun endpoint de modification/suppression du journal.

Le journal conserve avant/après, variation, motif, date et identité figée de l’auteur ; les opérations ORM ordinaires et en masse de modification/suppression sont refusées. L’administration est en lecture seule. Il s’agit d’une protection applicative, pas d’une garantie contre un administrateur SQL. Une erreur se corrige par une nouvelle écriture motivée, pas en effaçant la précédente.

La nouvelle permission n’est accordée automatiquement à aucun rôle/groupe historique, y compris Administrateur et Gestionnaire. Un superutilisateur peut utiliser le circuit ; les autres attributions sont explicites. La matrice des rôles expose ce droit sans modifier les sélections des groupes existants.

Migration `inventory.0024` : table additive et métadonnées de permission, sans reprise de données, sans attribution de droit et sans changement des anciens soldes. Un client ayant une nouvelle écriture de journal ne peut pas être supprimé : la trace financière est protégée.

## Qualification déjà obtenue, avant intégration finale

- Web paiements : SQLite commerce 176 cas (172 réussis, 4 réservés à PostgreSQL) ; PostgreSQL 83/83 réussis, dont concurrence des trois routes sur connexions distinctes.
- Dette Web/admin/services : SQLite inventaire 169 cas (166 réussis, 3 réservés à PostgreSQL) ; PostgreSQL 26/26 réussis, dont correction/correction et correction/règlement.
- API/dette/créances et compatibilité clients : SQLite 240 cas (230 réussis, 10 réservés à PostgreSQL) ; PostgreSQL 51/51 nouveaux cas/dette/conversion, puis les 10 cas historiques réservés, tous réussis sans skip. L’intégration de tous les lots reste qualifiée séparément avant publication. Un résultat de lot ne suffit pas à déclarer le déploiement validé.

Rapports locaux synthétiques : `sqlite_tests-1794505206625560400.json`, `postgresql_tests-1794505223542658000.json`, `sqlite_tests-1794505163488606800.json`, `postgresql_tests-1794505005849707200.json`, sous `tmp/p0c-admin-tests-20261111/`. Ils ne contiennent ni données métier ni secrets de production.

Rapports API combinés : `sqlite_tests-1794509172166333200.json`, `postgresql_tests-1794509219718130900.json` et `postgresql_tests-1794509667491951800.json`, sur le snapshot isolé `83c95609808a490d9eb05b3bded8e656`. Aucun de ces résultats n’atteste à lui seul une mise en production.
