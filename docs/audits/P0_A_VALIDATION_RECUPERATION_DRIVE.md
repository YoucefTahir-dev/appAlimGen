# P0-A — Validation de récupération d'une archive Drive

Date : 8 novembre 2026. **Cycle Drive réel fictif PostgreSQL 17 et cycle natif fictif PostgreSQL 18 Windows validés ; pas encore une sauvegarde de production validée.**

## Résultat actuel

| Étape | État | Preuve et limite |
| --- | --- | --- |
| Formulaires, permissions, service, assistant OAuth et banc isolé | PASS | Dernière relance : 341 tests ciblés en 30,690 s, exit 0, aucun test ignoré ; SQLite jetable, réseau/outils bloqués. Inclut CSP, callback privé, ATOMIC_REQUESTS, visibilité Drive, compteurs 18 et préflight build/démarrage ; anciens chiffres conservés comme historique |
| Serveur local de connexion seule | PASS | Écoute 127.0.0.1:8001, page et health HTTP 200 ; conflit de référent du formulaire corrigé : same-origin sur sa page, no-referrer sur callback/redirection, contrôles Origin/CSRF conservés. Aucun export possible. Port 8000 laissé intact ; consentement Google désormais confirmé |
| Protection des jetons | PASS local | Fernet réel, dépendances optionnelles verrouillées ; tokens dans un coffre chiffré séparé, session de navigation avec identifiant opaque seulement |
| Dépendances optionnelles | PASS sur ce PC | `cryptography 50.0.2`, `cffi 2.1.1`, `pycparser 3.12`, roues installées dans `tmp/` avec vérification obligatoire des SHA-256 ; environnement ERP existant non modifié |
| GPG natif sur fichier inventé | PASS | Chiffrement/déchiffrement identiques et nettoyage ; le fichier n'est pas un vrai dump PostgreSQL |
| Nouveau cycle PostgreSQL natif jetable | PASS 17.11 | Contexte d'exécution autorisé : vrai cluster TLS, dump, GPG et restauration réussis en 18,305 s ; aucune protection Windows/TLS modifiée |
| Six tables fictives, vrai dump, vraie restauration/comparaison | PASS local | Six tables, dix lignes, six séquences, douze index et vingt contraintes identiques ; fournisseur de transfert fictif pour ce premier cycle. Preuve expurgée local17-pass-20261108.json dans tmp/p0a-drive-native-validation |
| Nettoyage des essais natifs | PASS | Anciennes erreurs et nouvelle réussite conservées dans leurs rapports ; cluster arrêté et runtime supprimé après le cycle complet. Aucune protection Windows ou TLS désactivée |
| Consentement Google, choix réel du compte dans le navigateur | PASS local réel | Capture propriétaire « Connexion Google confirmée », corroborée par health HTTP 200 : connections_confirmed=1, callbacks_failed=0. Échange du code, scope exact drive.file, identité about et coffre chiffré réussis ; aucune identité/valeur secrète publiée |
| Renouvellement réel du jeton Google | PASS réel | Dernier rapport : scope exact et identité du compte sélectionné vérifiés avant l'envoi ; aucun jeton dans les preuves |
| Envoi et récupération sur Google Drive réel | PASS réel sur données fictives | Archive de 20 570 octets, métadonnées/visibilité contrôlées, téléchargement effectif et SHA-256 identique avant/après. Un seul fichier d'essai racine My Drive ; pas le dossier fixe du bouton ERP de production |
| Nettoyage du fichier Drive fictif initial | PASS réel confirmé | Capture propriétaire et rapport cleanup-result distinct : identité/provenance/SHA PASS, suppression de l'ID exact puis disparition confirmée sous la même identité. Zéro upload/download. L'ancienne reprise FAIL reste conservée comme historique ; le résidu est résolu |
| Nettoyage du worker du premier essai échoué | PASS historique | Worker interrompu, cluster arrêté, runtime supprimé. Aucune restauration distante n'est revendiquée pour ce premier run ; sa cause initiale écrasée n'est pas reconstruite a posteriori |
| Nouvelle relance du test complet | PASS réel, PostgreSQL 17.11 | Capture et deux rapports concordants : déchiffrement et vraie restauration des octets revenus de Drive ; six tables/dix lignes/six séquences/douze index/vingt contraintes comparés ; mauvaise phrase et altération rejetées. Durée fixture 139,576 s, pas un RTO de production |
| Nettoyage du dernier cycle | PASS réel et local | Suppression du seul nouvel ID et disparition confirmée ; ciphertext téléchargé, runtime/cluster et sessions du broker nettoyés. Worker et broker terminés avec exit 0 ; port 8000 laissé intact |
| Cycle PostgreSQL 18 natif Windows | PASS fictif | 18.6/GPG 2.4.9, 25,752 s ; TLS, dump/chiffrement/restauration réels, six tables/dix lignes/six séquences/douze index/quarante-quatre contraintes identiques, tests cryptographiques négatifs et nettoyage PASS. Transport Drive simulé pour ce cycle |
| Préparation Linux / Render | PARTIELLE, exécution NOT RUN | Roues OAuth CPython 3.12/Linux x86-64 validées par hashes ; runtime/version GPG/PG18 effectifs sur Render non vérifiés. Aucun outil installé sur Render, aucun déploiement. Préflight hors ligne build/démarrage préparé, flags désactivés |
| Export réel et restauration de données de production | NON AUTORISÉS / NOT RUN | Aucun accès Neon production ni au bucket GCP exclu |

Aucune donnée de production n'a été lue ou copiée dans ces essais locaux. La base seule ne couvre pas les photos, logos, pièces jointes, code ERP ou configuration serveur. Les références aux médias présentes en base restent des références, pas les fichiers.

Preuves du dernier cycle : `tmp/p0a-drive-remote-bridge-cf3c4617494f4350bfc65b625c8103e3/remote-result.json` et `native-result.json`, conservées localement et ignorées par Git. SHA-256 concordant : `34067bb376b721a01a39d46552937dc3e1135f8a5bfe1513baabbdca8bdb0f5a`. L'archive fictive elle-même a été supprimée comme autorisé. Cette preuve porte sur le banc séparé, pas sur un clic réel de l'ERP ni sur le déploiement Render/HTTPS/PostgreSQL 18.

## Ce qui a été ajouté localement

La page Sauvegardes prépare désormais ces deux étapes :

1. **Connecter / changer de compte Google Drive**, consentement uniquement chez Google, identité du compte affichée, connexion limitée à cette session Web.
2. **Créer l'archive chiffrée**, confirmation du mot de passe ERP et du périmètre « base uniquement — médias exclus ». Le compte est revérifié avant l'export ; un ancien formulaire est refusé après un changement de compte.

La destination initiale est le dossier privé fixe « El Amine - sauvegardes chiffrees » dans ce compte, pas un sélecteur de dossiers arbitraires ou de Drives partagés. Aucune tâche planifiée ou restauration automatique n'est ajoutée. Sans activation et configuration complètes, les boutons restent indisponibles. Cela n'est pas encore visible en production puisque le code n'y a pas été publié.

## 1. Cycles natifs — PostgreSQL 17 et 18 validés sous Windows, Render non qualifié

Le premier cycle ci-dessous a réussi dans le contexte d'exécution autorisé, après les échecs historiques Windows 87. Il ne faut plus le présenter comme NOT RUN. La comparaison couvre bien la fixture à six tables, et les fichiers en clair ont été nettoyés. La relance distante distincte a ensuite réellement déchiffré/restauré les octets revenus de Drive, sans copie locale de repli.

Depuis une session PowerShell normale, **sans élévation**, dans la racine du dépôt :

```powershell
.\.venv\Scripts\python.exe -I -B scripts/validate_drive_backup_local.py --postgres-bin tmp/p0a-portable-postgres17/runtime/pgsql/bin --gpg-bin "C:/Program Files/Git/usr/bin/gpg.exe" --openssl-bin "C:/Program Files/Git/usr/bin/openssl.exe"
```

Ce script est volontairement limité : il génère son propre cluster lié à `127.0.0.1` et à un port libre, ses identifiants aléatoires, un certificat TLS éphémère (`verify-full`) et ses six tables fictives. Il ne charge pas les paramètres Django de production ni `.env`. Seuls les exécutables natifs explicitement indiqués sont admis ; les appels HTTPS Google sont remplacés par un faux fournisseur local n'acceptant que l'archive chiffrée.

Le cycle utilise le vrai service, `pg_dump`, GPG, un transfert/récupération **simulé**, puis `pg_restore` sur une nouvelle base vide. Il compare toutes les valeurs, index, contraintes et séquences des six tables. Le rejet d'une mauvaise phrase et d'un fichier altéré a également réussi. **PASS local PostgreSQL 17.11 ; pas une preuve de transfert Google réel.**

Résultat expurgé : `tmp/p0a-drive-native-validation/validation.json`. Ne communiquer que ce rapport, jamais un dump ou un fichier de configuration. En cas de nouvel échec, ne pas retirer TLS, ne pas activer un serveur externe, ne pas utiliser une base existante et ne pas désactiver les protections Windows.

Le jeu de binaires portables EDB 18.6 a ensuite été téléchargé depuis le fournisseur officiel et vérifié par SHA-256 avant extraction ciblée, sans installation système/service. Son cycle synthétique local a réussi : preuve `tmp/p0a-drive-native-validation/local18-pass-20261108.json`. PostgreSQL 18 expose aussi les contraintes NOT NULL dans le catalogue : 44 contraintes comparées, contre 20 sur 17 ; le parent distant vérifie désormais les comptes stricts propres à la majeure. Aucun nouveau transfert Google réel n'est revendiqué pour ce cycle 18. Le banc Windows adapte les chemins MSYS de Git/GPG ; cela ne valide pas l'environnement Linux/Render.

Voir `P0_A_PREPARATION_RENDER_DRIVE.md` pour les gardes de déploiement, tests, paramètres non secrets et la capture Render encore nécessaire. Le menu/les protections sont préparés localement ; aucune publication du worktree mélangé n'est autorisée implicitement.

## 2. Préparer Google — intervention du propriétaire

La première connexion seule a réussi sur le banc local. Elle ne couvrait pas les fichiers Drive ; le cycle ultérieur décrit ci-dessus les a validés séparément sur une archive fictive. Aucun de ces essais ne qualifie la production. Le client JSON réel fourni hors dépôt a été lu de façon ciblée avec autorisation, sans divulgation ni copie dans Git. Aucun secret distant n'a été modifié par l'agent. Les jetons du banc sont temporaires et ne doivent pas être transférés en production.

- Le propriétaire a créé le projet/client Web dédié et activé l'API Drive, d'après ses captures. Le parcours local effectif confirme le callback 8001, le scope exact `drive.file` et l'accès d'identité. Aucun paramètre Google distant n'a été modifié par l'agent ; la future configuration HTTPS de production reste à valider séparément.
- Configurer le client/secret, la clé Fernet dédiée et la phrase de sauvegarde via un stockage sécurisé, sans les copier dans Git ou dans le chat. Le refresh token est obtenu par consentement et conservé chiffré par l'ERP, sans copie manuelle.
- Vérifier le compte destinataire et son espace gratuit disponible ; aucune activation de facturation ou augmentation payante de quota n'est autorisée. Ne pas utiliser de bucket S3/GCP comme repli.
- Installer les dépendances optionnelles verrouillées et les outils natifs compatibles uniquement dans un environnement d'essai autorisé. La connexion OAuth se teste dans un navigateur normal ; les WebViews Windows/Android ne sont pas qualifiées.
- Prévoir les limites du statut OAuth Testing (expiration possible des refresh tokens) et l'assainissement des journaux proxy/access logs du callback. Aucun access/refresh token n'est envoyé dans l'URL, mais un code OAuth court y transite nécessairement.

**Le propriétaire choisit son compte et consent chez Google. Il n'a jamais à communiquer son mot de passe Google, sa phrase de déchiffrement ou un token à l'agent.** La clé Fernet doit être distincte de la phrase d'archive et de la clé Django ; sa perte impose une reconnexion, tandis que la perte de la phrase d'archive empêche de récupérer les sauvegardes.

## 3. Essai Google distant, sur données fictives seulement

Autorisation explicite reçue après la connexion : une petite archive entièrement fictive chiffrée, récupération/restauration dans une base jetable, puis suppression des seuls fichiers de l'essai. Cela ne couvre pas un export réel ou un déploiement.

Le banc `validate_drive_backup_remote.py` est séparé de l'ERP et du worker natif ; ce dernier n'a aucun jeton Google. Le pont ne contient que des octets chiffrés et des preuves à schéma exact. Le test prévoit un seul fichier racine, ID préalloué et marqueur de run, pas de dossier ni réutilisation de fichiers existants. Nouveau consentement obligatoire : aucune copie des jetons du premier banc. Le propriétaire doit utiliser le bouton du nouveau banc, pas l'ERP public.

L'essai distant réel a échoué. Ses preuves d'origine et la première reprise de nettoyage FAIL sont conservées inchangées dans le pont local ignoré par Git. Cette reprise établit un refus local dû au type MIME, pas à l'identité ou au contenu du fichier. La cause précise du tout premier cycle reste non reconstructible, puisque son rapport avait écrasé l'erreur primaire.

La reprise corrigée `cleanup_drive_synthetic_local.py` ne lit que ces preuves expurgées ; elle n'ouvre pas l'ancien chemin de l'archive. Elle refait son GET normal juste avant DELETE et exige ID/nom/marqueur/propriétaire/taille et SHA-256 complet exacts. Un SHA absent, mal formé ou contradictoire bloque désormais cette reprise. Le MIME doit être syntaxiquement valide et non Google-native (dossiers/raccourcis/documents natifs exclus), sans exiger un libellé opaque particulier. Le drapeau historique `mime_match=false` reste honnête ; un nouveau drapeau `stored_blob_type` exprime ce critère distinct. Les permissions/état corbeille restent des diagnostics, pas une validation de confidentialité. Un 404 avant DELETE signifie absent ou inaccessible, jamais suppression confirmée. Un rapport distinct est publié pour chaque reprise. Aucune nouvelle archive ne sera envoyée avant clarification du résidu.

Procédure de validation attendue :

1. Utiliser une instance d'essai dont la base est entièrement fictive, sans connexion aux bases, services de messagerie ou intégrations de production. Le bouton sauvegarde toujours la base de **son instance Django** : ne pas cliquer sur le bouton de l'instance de production pour simuler un test fictif.
2. Connecter le compte de test dans l'ERP et vérifier son identité affichée. Préférer un client OAuth/compte de test séparé pour ne pas mélanger essais et futures vraies archives.
3. Créer l'archive de base uniquement. Vérifier que seul un `.tar.gpg` est envoyé dans le dossier privé ; relever nom, date, taille et SHA-256, sans contenu métier ni identifiants d'accès.
4. Récupérer **ce fichier exact** depuis Drive dans un espace privé autorisé. Comparer son SHA-256 à celui de l'envoi, puis le déchiffrer avec la phrase détenue par le propriétaire. Un simple contrôle des métadonnées Drive ne remplace pas ce téléchargement réel.
5. Vérifier les deux membres attendus (`database.dump`, `manifest.json`), le périmètre et le checksum interne avant toute restauration. Refuser des chemins inattendus, liens ou archives altérées.
6. Restaurer dans une **nouvelle base PostgreSQL vide et isolée**, compatible, sans connexion à l'ERP public. Comparer schéma, tables, valeurs attendues, index, contraintes et séquences, puis contrôler les données métier fictives.
7. Produire un rapport PASS/FAIL par étape. La suppression de l'objet distant d'essai ou la révocation Google nécessitent un accord précis ; ne jamais purger arbitrairement un Drive.

Les médias doivent rester marqués **non inclus, zéro média restauré attendu**. Aucun accès au bucket exclu n'est nécessaire.

## 4. Avant de considérer les données de production récupérables

Il faudra encore :

- Une autorisation distincte de publication, déploiement et configuration ; aucun commit, push ou déploiement n'est effectué actuellement.
- Une autorisation explicite d'exporter la base de production et d'envoyer son archive chiffrée sur le Drive désigné, avec règles de conservation et surveillance des échecs.
- Une restauration vérifiée de cette archive dans une base indépendante autorisée, **jamais dans Neon production**. L'archive restaure le point de sauvegarde, pas les opérations ultérieures.
- Le code ERP et la configuration nécessaires pour réinstaller l'application ; conserver les secrets de récupération séparément. Avant d'exposer une instance restaurée, invalider les anciennes sessions/coffres et reconnecter Drive, sans réactiver automatiquement des intégrations de production.
- Un plan distinct pour les médias si une récupération complète de l'ERP devient l'objectif. Leur exclusion actuelle reste respectée.

Un serveur compromis pourrait encore utiliser ses clés et son autorisation pour lire/supprimer ses archives accessibles. Cette option n'offre pas une sauvegarde immuable indépendante. Le cycle distant fictif réussi ne clôt pas P0-A à lui seul : qualification PostgreSQL 18/Linux, publication/configuration autorisées et validation d'une vraie sauvegarde/restauration isolée restent nécessaires avant de revendiquer une protection de la production.

## Références techniques

- [OAuth Google côté serveur](https://developers.google.com/identity/protocols/oauth2/web-server).
- [Accès Drive limité `drive.file`](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).
- [Identité du compte via Drive about.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/about/get).
- [Protection authentifiée Fernet](https://cryptography.io/en/latest/fernet/).
- [Restauration PostgreSQL](https://www.postgresql.org/docs/18/app-pgrestore.html).
- [Création du processus restreint dans pg_ctl 17](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/bin/pg_ctl/pg_ctl.c) ; [code Windows 87](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-).
