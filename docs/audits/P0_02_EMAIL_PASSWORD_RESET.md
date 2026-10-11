# P0-02 — Sécurisation des e-mails et de la réinitialisation

Date de validation locale : 11 octobre 2026

## Statut

- Étape A, protection immédiate du service : **PRÊTE**.
- Étape B, politique applicative et migration : **PRÉPARÉE, NON AUTORISÉE AU DÉPLOIEMENT** tant que l'audit de la base PostgreSQL de production et le test PostgreSQL isolé ne sont pas terminés.
- Déploiement, commit et push : **non effectués**, conformément à la mission.
- P0-03 : **non commencé**.

## Cause racine

`PasswordResetService.request()` utilisait une requête `email__iexact`, puis parcourait tous les utilisateurs actifs correspondants. Une adresse partagée par plusieurs comptes actifs produisait donc un lien distinct par compte. En outre, `iexact` ne neutralisait pas les espaces déjà enregistrés autour d'une adresse.

Le risque était une récupération ambiguë de plusieurs comptes à partir d'une seule adresse. La réponse publique restait neutre, mais plusieurs jetons pouvaient être délivrés au même destinataire.

Fichiers principalement concernés :

- `apps/accounts/services/password_reset.py` ;
- `apps/accounts/models.py` ;
- `apps/accounts/forms.py` et `apps/accounts/admin.py`, qui s'appuient sur la validation du modèle ;
- les commandes de création/récupération administrateur ;
- la migration préparée `0006_unique_normalized_user_email.py`.

## Règles métier constatées et retenues

- La connexion Web et JWT utilise `username`, pas l'e-mail.
- Le champ e-mail du modèle est facultatif. Les formulaires métier de création et modification d'utilisateur l'exigent déjà, tandis qu'un profil ou certains comptes techniques peuvent rester sans e-mail.
- Une adresse enregistrée n'est pas considérée comme vérifiée : aucun champ de vérification n'existe actuellement.
- Un compte sans e-mail ne peut pas utiliser la récupération par e-mail.
- Une adresse non vide est normalisée par suppression des espaces externes et mise en minuscules.
- L'unicité retenue est globale, sur comptes actifs et inactifs. Cela évite qu'une réactivation rende la récupération ambiguë.
- Les chaînes vides restent autorisées pour les comptes sans e-mail.

## Correction immédiate du service

La recherche de récupération compare désormais `Lower(Trim(email))` à la valeur normalisée.

- zéro compte actif : aucun envoi, réponse publique neutre ;
- un compte actif : envoi uniquement si le compte a un mot de passe utilisable et un e-mail ;
- au moins deux comptes actifs : aucun envoi et journal sécurisé `duplicate_active_email` ;
- compte inactif : aucun envoi et aucune révélation publique.

Le journal de conflit contient uniquement les identifiants internes des deux premiers comptes détectés. Il ne contient ni adresse, ni jeton, ni URL de réinitialisation. Le service ne sélectionne jamais arbitrairement le premier compte.

Cette logique est indépendante de la contrainte SQL et a été testée en simulant l'état historique comportant deux comptes actifs en conflit.

## Politique applicative

Le module `apps/accounts/email_policy.py` centralise :

- la normalisation ;
- la validation du format Django ;
- la recherche insensible à la casse et aux espaces ;
- le message administrateur en cas de doublon.

`User.clean()` applique la règle aux ModelForms. Cela couvre les formulaires Web de création, de modification, de profil et les formulaires Django Admin. Le `UserManager` normalise également les adresses lors de `create_user()` et `create_superuser()`.

Les commandes `create_admin` et `reset_admin` vérifient aussi la disponibilité de l'adresse. Aucun endpoint API de création ou de modification d'utilisateur n'a été trouvé ; les endpoints publics de réinitialisation restent neutres et compatibles avec Flutter.

Les opérations ORM qui contournent `full_clean()` ne sont protégées contre la concurrence qu'après application de la contrainte SQL.

## Diagnostic en lecture seule

Commande préparée :

```powershell
python manage.py audit_user_emails
python manage.py audit_user_emails --fail-on-conflict
```

Elle signale : doublons actifs, doublons globaux, variations de casse/espaces, comptes sans e-mail, formats invalides et conflits actifs/inactifs. Les sorties ne contiennent que les identifiants internes et des adresses masquées. Elle ne modifie aucune ligne.

Résultat sur la base SQLite locale uniquement :

- utilisateurs analysés : 3 ;
- doublons actifs : 0 ;
- doublons globaux : 0 ;
- adresses non normalisées : 0 ;
- comptes sans e-mail : 2 ;
- adresses invalides : 0 ;
- conflits actifs/inactifs : 0.

La base PostgreSQL Neon de production n'a pas été interrogée : aucun accès explicitement autorisé et isolé n'était disponible. Les nombres de production restent donc **inconnus**.

## Migration PostgreSQL préparée

La migration `accounts.0006_unique_normalized_user_email` :

1. conserve le manager personnalisé ;
2. vérifie qu'aucun doublon normalisé n'existe ;
3. ajoute `uniq_user_email_ci_trim` sur `Lower(Trim(email))`, pour les valeurs non vides.

Elle ne modifie, ne fusionne et ne supprime aucun compte. Son garde-fou arrête la migration avec un message sans adresse si des conflits existent. Ce garde-fou a été reproduit avec succès sur une base SQLite isolée contenant deux doublons.

La migration directe et la détection de conflit passent sur SQLite. Elles n'ont pas été validées sur PostgreSQL, faute d'instance isolée disponible. En conséquence, **ne pas déployer `0006` maintenant**.

## Sécurité des liens et des sessions

L'audit confirme :

- jeton Django lié à l'utilisateur et au hash du mot de passe ;
- expiration via `PASSWORD_RESET_TIMEOUT` ;
- invalidation après utilisation ou changement indépendant du mot de passe ;
- validation du nouveau mot de passe par les validateurs Django ;
- verrouillage de l'utilisateur dans une transaction lors de la confirmation ;
- rate limiting par adresse normalisée et par IP ;
- réponse neutre pour adresse inconnue/inactive ;
- incrément de `auth_token_version` après changement ;
- rejet des anciens JWT d'accès et de rafraîchissement ;
- aucun jeton en clair dans les journaux.

## Brevo et HTTP 503

Le transport Brevo HTTPS est séparé du problème d'unicité. Les tests unitaires confirment la construction de la requête, le timeout, le traitement des erreurs HTTP et la journalisation sans secret.

La configuration Render à contrôler est :

```text
PASSWORD_RESET_EMAIL_PROVIDER=brevo_api
BREVO_API_KEY=<clé API Brevo, pas clé SMTP>
DEFAULT_FROM_EMAIL=<expéditeur Brevo vérifié>
DEFAULT_FROM_NAME=El Amine
PASSWORD_RESET_PUBLIC_BASE_URL=https://<service>.onrender.com
EMAIL_TIMEOUT=10
```

Avec `brevo_api`, les identifiants SMTP ne sont pas utilisés par ce transport. Aucun secret Render n'a été lu et aucun e-mail réel n'a été envoyé ; la livraison réelle reste donc **À TESTER APRÈS AUTORISATION**.

## Résultats des tests

### Suite exécutée

- tests ciblés P0-02 : **29 réussis, 1 ignoré** sur 30 ;
- suite Django complète : **329 réussis, 9 ignorés** sur 338 ;
- `manage.py makemigrations --check --dry-run` : **PASS**, aucun changement ;
- `manage.py check` : **PASS** ;
- `manage.py check --deploy` : **exécuté avec 1 avertissement** `security.W021` sur `SECURE_HSTS_PRELOAD`, sans rapport avec P0-02 ;
- migration SQLite propre : **PASS** ;
- garde-fou sur doublon SQLite : **PASS** ;
- PostgreSQL isolé : **NON EXÉCUTÉ** ; Docker et `psql` ne sont pas disponibles.

### Matrice demandée

| Cas | Résultat |
|---|---|
| 1. E-mail unique | PASS |
| 2. Aucun utilisateur | PASS |
| 3. Deux utilisateurs même adresse, aucun envoi | PASS, état historique simulé sans dépendre de SQL |
| 4. Variantes majuscules/minuscules | PASS |
| 5. Espaces autour de l'adresse | PASS |
| 6. Compte inactif | PASS |
| 7. Compte sans e-mail | PASS |
| 8. Demandes répétées | PASS |
| 9. Rate limiting e-mail et IP | PASS |
| 10. Lien valide | PASS |
| 11. Lien expiré | PASS |
| 12. Lien réutilisé | PASS |
| 13. Nouveau mot de passe invalide | PASS |
| 14. Nouveau mot de passe valide | PASS |
| 15. Révocation JWT accès/refresh | PASS |
| 16. Création avec e-mail utilisé | PASS |
| 17. Modification créant un doublon | PASS |
| 18. Deux créations concurrentes PostgreSQL | NON EXÉCUTÉ — test écrit, PostgreSQL isolé indisponible |

Un test ignoré n'est pas compté comme réussi.

## Procédure de déploiement proposée

### Étape A — correction immédiate

Déployer séparément la logique de `email_policy.py`, `password_reset.py` et ses tests. Cette correction ne nécessite aucune migration et stoppe immédiatement les envois multiples.

### Étape B — unicité durable

1. Créer une branche ou restauration PostgreSQL isolée de la production.
2. Exécuter `python manage.py audit_user_emails --fail-on-conflict` sur cette copie puis, en lecture seule, sur la production.
3. Faire valider manuellement chaque conflit par le propriétaire métier. Ne modifier ni supprimer automatiquement un compte.
4. Rejouer l'audit jusqu'à `État migration : COMPATIBLE`.
5. Appliquer `python manage.py migrate accounts 0006` sur la copie PostgreSQL.
6. Exécuter le test de concurrence PostgreSQL et toute la suite Django.
7. Sauvegarder/valider le point de restauration Neon.
8. Déployer l'étape B, appliquer la migration, puis tester Web et Android.

## Rollback

Avant tout rollback, arrêter les écritures de comptes. Revenir au code précédent et retirer la contrainte avec :

```powershell
python manage.py migrate accounts 0005
```

La migration ne transforme aucune donnée ; son rollback retire uniquement la contrainte et l'état du manager. Une sauvegarde Neon reste obligatoire avant déploiement.

## Risques résiduels et décision

- Les conflits de production sont inconnus.
- Le test de concurrence PostgreSQL obligatoire n'a pas encore été exécuté.
- La délivrabilité réelle Brevo/Render n'a pas été testée.
- Les comptes sans e-mail restent volontairement exclus de la récupération.
- Une adresse d'un compte inactif reste réservée, choix volontaire pour empêcher les ambiguïtés lors d'une réactivation.

Décision : l'étape A est livrable séparément. L'étape B doit rester bloquée jusqu'à l'audit de production et à la validation PostgreSQL isolée.
