# P1 — Admissions Web/admin et réinitialisation publique

Date : 12 novembre 2026. Qualification isolée réussie ; aucun déploiement ni
accès à une base, un compte ou un fournisseur de production dans ce lot.

## Admissions persistantes

Le Web et l'administration Django utilisent désormais le service existant
`core.auth_rate_limit`, et non le comptage préalable de journaux d'échecs.
Chaque POST admis réserve d'abord un budget IP dans une courte transaction,
puis un budget de compte global entre IP. Les identifiants techniques sont
HMACés ; aucun mot de passe, token, nom d'utilisateur ou IP en clair n'est ajouté
à la table technique. Aucun cache local n'est utilisé comme solution de repli.

Les plafonds existants sont conservés sans nouvelle configuration de production :

| Opération | Namespace | Compte | IP | Fenêtre |
| --- | --- | --- | --- | --- |
| Connexion Web | `web.login` | `LOGIN_FAILURE_LIMIT` (5 par défaut) | `LOGIN_FAILURE_IP_LIMIT` (20) | `LOGIN_FAILURE_WINDOW_SECONDS` (900 s) |
| Connexion Django admin | `admin.login` | mêmes paramètres, budget distinct | idem | idem |
| Demande publique Web de reset | `web.password_reset.request` | `PASSWORD_RESET_LIMIT` (5) | `PASSWORD_RESET_IP_LIMIT` (10) | `PASSWORD_RESET_WINDOW_SECONDS` (3 600 s) |
| Demande par administrateur authentifié | `admin.password_reset.request` | mêmes paramètres, budget distinct | idem | idem |
| Confirmation Web avec lien valide | `web.password_reset.confirm` | mêmes paramètres, UID canonique | idem | idem |

Le nom historique `LOGIN_FAILURE_*` reste compatible, mais la sémantique change :
**toutes les tentatives admises, y compris les connexions réussies, comptent**.
Avec les valeurs par défaut, une sixième connexion d'un même compte Web dans
une fenêtre de quinze minutes peut donc être refusée, même après cinq succès.
Les budgets Web, Django admin et API ne se cumulent pas entre canaux. Une IP
manquante ou invalide utilise le budget partagé `unknown` ; elle n'exempte pas
le demandeur. La source IP reste `get_client_ip`, selon la confiance proxy
configurée, sans lecture directe opportuniste des headers transmis.

Un refus produit 429 et le temps restant réel `Retry-After`. Il ne prolonge pas
la fenêtre du compte ; il consomme néanmoins le budget IP préalablement admis.
Une configuration invalide ou une erreur de stockage produit 503 avant la
vérification du mot de passe ou la livraison d'un e-mail, jamais une admission.
Les GET n'utilisent pas de quota. Les confirmations à lien invalide conservent
la réponse Django normale et ne passent pas à la validation du nouveau secret.

Le refus Web rend un formulaire non lié : l'ancien `add_error()` sur un
`AuthenticationForm` lié pouvait déclencher `full_clean()` et donc authentifier
malgré le refus. Le hook admin s'exécute après la vérification CSRF : un POST
CSRF invalide reste 403, sans authentification ni réservation de budget.

Les AuditLog restent un journal pour l'authentification. L'ancien second quota
AuditLog du reset API est supprimé : les quotas atomiques du throttle API déjà
déployé restent la source de vérité. Le filtre AuditLog distinct qui protège
les requêtes Drive rejetées n'est pas modifié dans ce lot.

## Réinitialisation publique et consommation du token

En cas de panne du transport d'e-mail, une demande syntaxiquement valide garde
la même réponse publique qu'un compte absent/inactif : redirection Web vers la
page générique (302), message générique API (200). Cela ne signifie pas qu'un
e-mail a été livré. Le service conserve ses diagnostics assainis et un événement
interne d'échec de livraison avec statut 503 ; ni clé fournisseur, destinataire,
lien de réinitialisation ni texte libre d'exception ne sont exposés.

L'administrateur authentifié conserve un message d'échec utile pour son action.
Une panne de la réservation d'authentification demeure 503 pour toute identité,
avant recherche du compte : elle n'est pas assimilée à une panne fournisseur.

Le formulaire Web utilise maintenant `PasswordResetService.confirm()` lors de
la sauvegarde. Il recontrôle le token sous verrou utilisateur et révoque les JWT
une seule fois. Une autre requête peut avoir consommé le lien après le contrôle
initial de Django : ce cas rend alors la page habituelle de lien invalide, sans
remplacer le mot de passe choisi par la requête gagnante. Le service recontrôle
également `is_active` sous le même verrou.

## Qualification synthétique

Base archivée : `1006fe978e86f88bbcbcb3f1db2f9a71209285b1`, avec les onze fichiers
d'authentification explicitement superposés. Snapshot qualifié :
`tmp/p0c-admin-tests-20261111/snapshot-41f2514558af48f2b7be8abfe51b8758`.
Les modifications parallèles BUS-03 des permissions et de la transition API ne
sont pas présentes dans cette qualification auth seule : une qualification
combinée doit précéder toute publication du lot global.

| Vérification | Résultat | Rapport local sous `tmp/p0c-admin-tests-20261111` |
| --- | --- | --- |
| Six modules auth/core/API, SQLite | PASS : 105 cas comptés, 103 réussis et 2 skips PostgreSQL | `sqlite_tests-1794508593781543000.json` |
| Mêmes modules sur un PostgreSQL 18 TLS neuf et local | PASS : 105/105, aucun skip | `postgresql_tests-1794508637509069900.json` |
| Quota global simultané, 8 connexions/PID distincts et 8 IP synthétiques | PASS : exactement 3 admissions | Même rapport PostgreSQL |
| Deux POST Web, même token, deux sessions et connexions PostgreSQL | PASS : une seule consommation et révocation JWT | Même rapport PostgreSQL |
| Imports limités au snapshot et sources inchangées | PASS sur les deux moteurs | Mêmes rapports |
| Cluster et runtime synthétiques arrêtés/supprimés | PASS | Rapport du parent PostgreSQL |

La première qualification a révélé un défaut de rendu de refus dans la nouvelle
implémentation : `add_error()` requiert `cleaned_data` même sur un formulaire non
lié. Le correctif initialise un dictionnaire vide avant cet appel, sans lier les
identifiants ou déclencher une authentification. Les tests négatifs en 429 et
503 vérifient explicitement l'absence d'appel à `authenticate()`.

Cette qualification ne lance aucune commande Django sur le worktree. Les
runners désactivent dotenv, limitent les imports au snapshot, remplacent mail et
stockages par des implémentations synthétiques et refusent les accès externes.

## Limites explicites

R-03 n'est pas totalement clôturé : **la livraison reste synchrone**, donc le
temps de réponse peut encore différer entre un compte existant et absent. Il
n'est affirmé ni uniformité temporelle, ni envoi asynchrone, ni mesure d'attaque
en production. Aucune file, thread détaché ou worker supplémentaire n'est créé.
Un transport réellement asynchrone requiert une orchestration durable, des
choix d'exploitation et une qualification distincte ; une simple attente fixe
ne prouverait pas à elle seule l'absence de canal temporel.

L'autorisation de ce lot ne change aucun plan Render, compte réel, secret,
paramètre de production, seuil de quota ou donnée métier. Aucun nouveau modèle
ou migration de schéma n'est nécessaire : la table technique existante est
réutilisée. Les collisions de connexion et token sont à vérifier sur PostgreSQL
isolé ; des tests SQLite seuls ne constituent pas une preuve de verrouillage.

Références primaires : [transactions Django 5.2](https://docs.djangoproject.com/en/5.2/topics/db/transactions/),
[verrous PostgreSQL 18](https://www.postgresql.org/docs/18/explicit-locking.html),
[réinitialisation OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
