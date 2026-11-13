# P2-01 — Créances, confidentialité GPS et scripts Web

Date : 13 novembre 2026. État de qualification locale avant autorisation de
publication ; une publication ultérieure est tracée séparément.
Base publiée : `0aed3c1aab21089d91556a6ab960f11eadf6a838`.

## Périmètre et décision du propriétaire

Demande : traiter la matérialisation excessive des créances, les coordonnées GPS
dans les logs et le durcissement Web. **La sauvegarde souhaitée est manuelle** :
aucune automatisation, aucun secret de sauvegarde et aucune configuration cloud
n'ont été ajoutés. Les fichiers de sauvegarde déjà modifiés avant cette demande
ont été laissés intacts ; ceci ne prétend pas désactiver un ancien workflow GitHub
planifié ni changer ses paramètres distants.

Aucun accès à la production, aucune restauration, aucune migration ou écriture
métier de production. Pas de modification du dépôt mobile, des droits existants,
des écritures financières, des verrous, de l'idempotence ou des corrections de
solde. Aucun commit/push effectué à ce stade de qualification.

## PERF-01 — listes de créances

Le compteur de factures restant à régler est calculé en SQL : somme des paiements
actifs par facture, puis nombre de factures dont le total dépasse cette somme.
La requête de clients ne précharge plus toutes les ventes et tous les paiements.
Les totaux, paiements annulés, factures historiques non rapprochées, dettes
initiales et filtres de visibilité restent soumis aux règles préexistantes.
L'arrondi monétaire à deux décimales évite également qu'une somme flottante SQLite
classe une facture intégralement réglée parmi les impayées.

Régression dédiée : 10, 1 000 et 10 000 factures fictives, chacune avec un paiement
actif de 40 sur un total de 100, page API d'un seul client. La construction d'un
objet `Sale` ou `Payment` sur cette page provoque désormais un échec du test.
Même contrôle sur la liste Web. Contrôles complémentaires : annulation, règlement
partiel/complet/fractionnaire, total nul, dette initiale signée et visibilité d'un
opérateur.

Limite explicitement conservée : le détail API historique et le relevé complet
continuent à retourner toutes les lignes, pour ne pas tronquer silencieusement
les réponses des mobiles ni les relevés. Le préchargement en double du détail
API disparaît ; une réponse complète reste proportionnelle à son historique.
Il ne faut pas interpréter le budget nul de la **liste** comme un budget nul
du **détail**, ni comme une absence de travail SQL.

## CONF-01 — minimisation des nouveaux logs GPS

L'événement conserve `client.location.updated`, l'identifiant du client, l'auteur,
la date et le type de changement (`set`, `changed`, `cleared`). Il ne contient
plus ancienne/nouvelle latitude ou longitude, précision, adresse ou identifiant
de lieu. Le point d'audit est partagé par les formulaires Web et l'API.

Les tests vérifient le contenu persistant `AuditLog` **et** la sortie du logger
`security`, pour ajout, modification, changement de métadonnées et effacement.
La géolocalisation de la fiche client reste disponible avec les droits existants.

**Réserve historique :** aucune purge ou réécriture des anciens journaux n'a été
exécutée. Leur assainissement doit être décidé séparément avec le propriétaire,
y compris les copies déjà expédiées à l'hébergeur. Le correctif empêche les
nouveaux événements concernés d'y recopier les coordonnées ; il n'efface pas
rétroactivement les traces existantes.

## SEC-06 — scripts Web et dépendances CDN

- Extraction des scripts exécutables intégrés en six ressources statiques :
  navigation, tableau de bord, aperçu de conditionnement, impression automatique,
  test imprimante et boutons d'impression.
- Suppression des attributs exécutables `onclick`. Le lien QR devient un lien
  normal avec `noopener noreferrer`. Les libellés traduits du graphique restent
  des données HTML, non du code JavaScript interpolé.
- Politique CSP par défaut sans `unsafe-inline` ni `unsafe-eval` pour les scripts ;
  `script-src-attr 'none'`. Les scripts CDN sont limités aux deux chemins/version
  déjà utilisés, pas au domaine jsDelivr entier.
- Les cinq inclusions directes CDN disposent de SRI SHA-384 et
  `crossorigin="anonymous"`. Les quatre fichiers/version distincts ont été
  téléchargés en mémoire et leurs empreintes calculées le 13 novembre ; aucune
  mise à niveau Bootstrap/Chart.js n'a été mélangée au correctif.

Les tests couvrent l'absence de scripts exécutables/attributs intégrés dans les
templates du projet, les cinq inclusions SRI, les fichiers statiques, la réponse
de connexion et la conservation d'une politique explicitement définie par
l'opérateur. Trois tests JavaScript isolés couvrent clic d'impression, impression
automatique/différée et données traduites des graphiques.

Réserves : `style-src 'unsafe-inline'` reste nécessaire à la compatibilité actuelle
des styles/impressions et n'a pas été présenté comme supprimé. Une éventuelle
surcharge Render `CONTENT_SECURITY_POLICY` prévaut toujours ; les en-têtes
effectifs devront être contrôlés après publication. Aucun test physique
d'imprimante ou test de navigateur réel n'est revendiqué.

Principes techniques : [CSP MDN](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP)
et [SRI MDN](https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity).

## Qualification et traçabilité

La qualification finale est réussie sur une archive Git propre de la révision
publiée, avec seulement les 24 fichiers explicitement liés à ce correctif.
Snapshot : `tmp/p0c-admin-tests-20261111/snapshot-a6fb62b8a05c46719363ce8d38d18371`.
Les données sont fictives ; les exécuteurs bloquent les accès externes et toute
connexion à une base autre que le runtime SQLite/PostgreSQL 18 local jetable.

Résultats du candidat final :

| Vérification | Résultat |
| --- | --- |
| Suite complète SQLite + scripts Python | 1 145 comptés, 1 108 réussis, 37 ignorés car PostgreSQL requis ; zéro échec/erreur |
| Suite ciblée PostgreSQL 18 | 116/116 réussis, aucun ignoré |
| Scripts JavaScript, DOM fictif | 3/3 réussis ; syntaxe des six scripts vérifiée |
| Prépublication locale | migrations cohérentes, contrôles de déploiement, collecte des statiques, OpenAPI et compilation réussis |
| Intégrité | 24 sources identiques au snapshot ; les cinq fichiers préexistants modifiés restent inchangés ; index Git vide ; dépôt mobile inchangé |
| Nettoyage | cluster PostgreSQL arrêté et runtime supprimé ; aucun runtime synthétique de cette intervention encore présent |

Mesures de liste API PostgreSQL du candidat final :

| Factures fictives du client | Objets Sale/Payment chargés | Requêtes | Pic Python tracemalloc |
| --- | --- | --- | --- |
| 10 | 0 | 3 | 333 481 octets |
| 1 000 | 0 | 3 | 323 452 octets |
| 10 000 | 0 | 3 | 320 864 octets |

Le cas initial à 10 000 factures chargeait 20 000 objets sur la liste. Le budget
de construction d'objets est désormais nul pour ces deux modèles, sans changer
les résultats financiers. Le pic mesuré concerne les allocations Python du test,
pas la RAM totale du serveur ou un benchmark de latence de production.

Preuves locales sous `tmp/p0c-admin-tests-20261111/` :

- `sqlite_tests-1794583477559510400.json` et `.log` ;
- `postgresql_tests-1794583797471146100.json` et `.log` ;
- `postgres-parent-919841e16fb8bca0.json` : arrêt/nettoyage confirmés ;
- `source-manifest-read-privacy-a6fb62b8a05c46719363ce8d38d18371.json` :
  empreintes SHA-256 des sources et des rapports, mesures et limites.

Le premier diagnostic a révélé une collision de noms entre les clients de volume
10/1 000/10 000 dans le jeu de test ; les noms sont maintenant distincts. Un ancien
test d'impression attendait le code `window.print()` directement dans le HTML :
il vérifie maintenant les scripts externes et le bouton non exécutable, avec les
tests JavaScript de comportement en complément. Ces ajustements ne contournent
pas un échec applicatif.

Les rapports antérieurs restent inchangés et ne constituent pas une preuve de
publication de ce nouveau correctif. L'audit global n'est pas déclaré clos.
