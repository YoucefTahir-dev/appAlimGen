# P2-07 — Réinitialisation différée sur l'instance Web existante

14 novembre 2026, Europe/Paris. Préparation et qualification ; **aucune activation
en production**, aucune création de service. Le propriétaire a autorisé un commit
ciblé et une branche de revue, sans fusion ni activation Render.

## But et périmètre

Conserver **Brevo comme fournisseur**. Le processus supplémentaire demande les
envois à Brevo en dehors de la requête Web/API ; ce n'est pas un autre fournisseur
de messagerie. La file SQL durable, les tentatives bornées et le chiffrement sont
ceux du lot backend déjà publié. Aucun compte/privilège SQL, secret, sauvegarde
automatique ou application mobile n'est modifié.

## Faisabilité observée, sans garantie de capacité

Lecture Computer Use de Render → Metrics, fenêtre **Last 12 hours**, le jour de
ce suivi : limite **512 MB / 0.5 CPU**, mémoire visuellement autour de **40–46 %**,
CPU généralement bas avec un pic au déploiement. Les valeurs sont approximatives
et issues des graphes, non d'un export de mesures ni d'un test de charge. Plusieurs
couleurs représentent des instances successives ; ne pas les additionner comme
une seule consommation.

Le propriétaire a ensuite exécuté un chargement des bibliothèques dans le Shell
Linux réel, sans demande de réinitialisation ou appel à Brevo, et transmis :

```json
{"process_peak_mib": 79.0, "crypto_ready": true, "mail_sent": false, "queue_modified": false}
```

Ce pic concerne ce processus de pré-vérification, **pas** une charge complète du
worker et du Web ensemble. Il inclut le chargement Django et un aller-retour
cryptographique avec une valeur fictive. Le superviseur et les allocations lors
d'un envoi ajouteront encore de la mémoire. Les unités MB du graphe et Mio du
processus ne sont pas confondues. La marge observée rend l'option plausible, sans
garantir l'absence de ralentissement/OOM en pointe. Aucun nouvel abonnement ou
changement du plan Render n'est réalisé ou supposé.

## Préparation opt-in

- `start.sh` conserve **par défaut** son démarrage Gunicorn existant et les gardes
  préalables de runtime/schéma. Le nouveau flag est fermé par défaut.
- Seulement avec `PASSWORD_RESET_COLOCATED_WORKER_ENABLED=true`, le superviseur
  Linux `scripts/run_colocated_reset.py` démarre un worker et Gunicorn, dans deux
  groupes de processus. La commande Web conserve les workers/timeouts configurés
  et le format d'accès sans query string.
- Le worker exige aussi le flag asynchrone existant. Son préflight vérifie une
  origine publique HTTPS sans identifiants/query/path, le chiffrement et l'accès
  à un identifiant technique de la file, sans envoi ni lecture de destinataire.
  `--check-only` n'effectue aucune mutation.
- Le Web n'est démarré qu'après le marqueur de disponibilité du worker. Une
  absence de disponibilité après 60 secondes fait échouer le démarrage.
- Lots de 10, pause de 15 secondes ; arrêt sur SIGTERM/SIGINT après l'élément en
  cours. Les signaux sont transmis aux groupes ; les enfants sont attendus, avec
  une limite de grâce de 30 secondes avant arrêt forcé.
- Un décès inattendu de l'un des deux processus ou l'absence de heartbeat pendant
  cinq minutes fait sortir l'unité en erreur. Le répertoire de heartbeat n'est
  supprimé qu'après l'arrêt des enfants.
- Le build opt-in installe le verrou haché optionnel existant pour garantir la
  dépendance cryptographique. Cela **n'active ni Drive ni OAuth**.

**Contrepartie explicite :** sur une instance partagée, un incident grave du
worker peut entraîner le redémarrage du Web avec lui. Ce couplage ne doit pas être
présenté comme une isolation équivalente à un service distinct ou comme une
garantie de zéro interruption. Le heartbeat ne démontre pas la livraison d'un
mail ni l'absence d'échecs Brevo. Les compteurs de file, la réception réelle et
la surveillance opérationnelle restent nécessaires.

## Qualification et limites de preuve

Les tests utilisent des destinataires fictifs, un PostgreSQL 18 neuf en loopback
TLS et un snapshot sans `.env`. Aucun accès à la base réelle, aux secrets, à un
bucket, à Brevo ou à une archive réelle. Les processus de test natifs Linux ont
un environnement synthétique filtré et n'importent pas Django.

Campagnes intermédiaires : 43 puis 45 tests ciblés réussis, puis 65 cas comptés
(61 réussis et 4 réservés au POSIX natif) dans
`postgresql_tests-1794655021878643700.json`. Les quatre scénarios natifs ne sont
**pas** revendiqués comme exécutés sous Windows. Une correction supplémentaire
de l'ordre de nettoyage du répertoire de heartbeat et son test sont ajoutés
après cette campagne. Qualification locale finale : **66 cas comptés, 62 réussis,
4 réservés au POSIX natif, aucun échec/erreur**, rapport
`postgresql_tests-1794655233001817300.json`. Cluster PostgreSQL jetable arrêté
et runtime supprimé ; imports limités au snapshot et intégrité vérifiée.

Les tests couvrent le parcours public sans recherche de compte/envoi en ligne,
la file, ses reprises, le chiffrement, les refus de configuration, le préflight
non mutant, la disponibilité avant Web, les sorties inattendues, le watchdog,
l'arrêt et la confidentialité des erreurs. Les scénarios POSIX ajoutés vérifient
les vrais signaux/groupes et l'attente des enfants ; ils doivent passer en CI
Linux avant toute activation. La syntaxe Bash de `build.sh` et `start.sh` a été
vérifiée avec `bash -n`.

## Conditions avant activation — pas des actions déjà effectuées

1. Réussite de la CI Linux sur le commit exact de revue, dont les scénarios natifs.
2. Autorisation distincte de fusion/publication du code, avec les flags encore
   fermés, puis confirmation du déploiement.
3. Vérification des configurations existantes et de la surveillance de la file,
   sans recopier les secrets ni changer les droits SQL.
4. Activation coordonnée des deux flags **sur le code qualifié déjà publié**,
   jamais le seul flag asynchrone sur une version dépourvue de superviseur.
5. Recette d'un compte/destinataire de test autorisé : réception réelle, lien,
   usage unique et invalidation des sessions ; contrôle Web/API, ressources et
   arrêt/redémarrage. Le propriétaire réalise lui-même la saisie du mot de passe.

Le retour au parcours synchrone demande de fermer l'admission asynchrone puis de
drainer/expirer les demandes actives avant d'arrêter le worker. Ne pas supprimer
la table, changer la clé ou couper brutalement les deux flags avec une file active.
L'envoi reste au moins une fois : un arrêt après acceptation Brevo mais avant
validation locale peut entraîner un doublon de mail. Aucun mot de passe ou token
en clair n'est conservé dans cette file.

**État de la réserve R03 : préparation complémentaire en cours de qualification,
pas corrigée en production tant que le mécanisme reste désactivé et non recetté.**
