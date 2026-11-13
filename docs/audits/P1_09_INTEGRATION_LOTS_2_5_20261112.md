# P1 — Qualification intégrée des lots 2 à 5

Date : 12 novembre 2026. **Qualification backend locale réussie ; aucune publication, activation opérationnelle ni clôture globale de P1.**

L’autorisation porte sur l’anti-doublon restant, les corrections de dette antérieure, les protections d’authentification Web/admin et les garde-fous de migration/concurrence. Ce rapport consolide les preuves d’intégration ; il ne remplace pas les décisions nécessaires à la mise en production.

## Périmètre exact

- Base Git : `1006fe978e86f88bbcbcb3f1db2f9a71209285b1`.
- Snapshot figé : `tmp/p0c-admin-tests-20261111/snapshot-698c89e16ef340b38ec57669386b8833`.
- Archive de base SHA-256 : `aedd4d22041f756d8ddc4d0e66744e2b4e7cbc87d83c45b73ccafe1d0802a708`.
- **42 fichiers backend sélectionnés explicitement**, ajoutés à une archive propre de cette base, soit 484 fichiers dans le snapshot final.
- Manifest local : `tmp/p0c-admin-tests-20261111/source-manifest-698c89e16ef340b38ec57669386b8833.json`. Il contient les SHA-256 des 42 sources, le rattachement à la base et les empreintes des cinq changements utilisateur exclus. Les sources ont été comparées au snapshot avant et après les tests.

| Composant | Fichiers du lot | Contrat couvert |
| --- | ---: | --- |
| Comptes | 7 | Neutralité des erreurs de réinitialisation, consommation concurrente du jeton, délégation de la permission financière |
| API | 7 | Transition compatible des clés, reprises selon les droits actuels, correction dédiée et historique de dette |
| Commerce | 7 | Paiements Web, reprises, ordre de verrouillage ventes/chargements et suppressions ORM |
| Core | 6 | Admission Web/admin persistante, sécurité et commande de migrations revue |
| Inventaire | 14 | Journal de dette, protection du profil/admin, formulaire dédié, migration additive et stock |
| Configuration | 1 | Paramètres compatibles et documentation de leur sémantique |

Les cinq changements utilisateur préexistants sont **exclus des overlays et préservés** : `.env.example`, `.github/workflows/database-backup.yml`, `.github/workflows/django-ci.yml`, `apps/core/backup.py`, `apps/core/tests/test_backups.py`. Leurs versions présentes dans le snapshot sont celles de la base Git, pas les modifications locales de l’utilisateur. Aucun dépôt imbriqué, script ou document non lié au lot n’a été copié comme source supplémentaire. `build.sh` n’est pas modifié.

## Résultats intégrés

Preuves locales non versionnées, sous `tmp/p0c-admin-tests-20261111/` :

| Exécution | Résultat | Rapport et log |
| --- | --- | --- |
| Suite SQLite complète | **PASS : 1097 cas comptés, dont 1061 exécutés et 36 réservés à PostgreSQL** ; aucun échec ni erreur | `sqlite_tests-1794510670802399900.json` et `.log` |
| Régression PostgreSQL intégrée | **PASS : 743/743**, aucun skip, échec ni erreur | `postgresql_tests-1794510820042142900.json` et `.log` |

Les deux nombres ne s’additionnent pas : de nombreux scénarios sont exécutés sur les deux moteurs. Les **36 noms exacts** sautés par SQLite ont chacun été retrouvés dans le log PostgreSQL réussi, sans être sautés dans cette seconde exécution ; aucune omission restante dans cette correspondance.

La suite SQLite sans restriction de labels a également validé :

- cohérence des migrations et absence de migration manquante ;
- contrôles de déploiement Django, collecte des fichiers statiques et validation OpenAPI ;
- compilation des sources, imports exclusivement depuis le snapshot et absence de modification de celui-ci.

Seul le code d’avertissement Django préexistant `security.W021` est observé : le préchargement HSTS n’est pas activé. Ces contrôles ne constituent pas une inspection des paramètres effectivement déployés sur Render.

Le run PostgreSQL inclut `apps.api.tests`, `apps.accounts.tests`, les modules core d’authentification, sécurité Web et migrations, ainsi que **tout commerce et tout inventaire**. L’identité du nouveau cluster PostgreSQL 18 a été contrôlée ; son arrêt et la suppression du runtime synthétique sont confirmés PASS.

### Cas de concurrence et garanties bornées

- Dette antérieure : corrections concurrentes avec comparaison de la valeur attendue, concurrence avec un règlement, rollback du solde et du journal en cas d’erreur, reprise unique et autorisation actuelle.
- Paiements/reprises : pas de nouveau règlement, mouvement de stock ou audit de succès sur une reprise déjà enregistrée ; refus après perte des droits ou du périmètre.
- Authentification : quotas partagés entre connexions, consommation unique d’un même jeton de réinitialisation depuis deux sessions et contrat neutre des réponses d’erreur.
- R02 : **11 cas réussis**, dont 7 sur de vraies connexions PostgreSQL. Ils couvrent clôture/création dans les deux ordres, mise à jour et suppression de lignes, union des verrous d’une suppression multi-ventes, reparentage concurrent et absence de verrou implicite sur Client issu d’un JOIN appelant. Les stocks, refus métier attendus et identités de connexion sont contrôlés.
- Migrations : concurrence du verrou de déploiement entre deux connexions PostgreSQL indépendantes, libération du verrou vérifiée depuis une autre connexion, restauration des timeouts et absence de downgrade automatique après une seconde migration atomique en échec. Le test d’ancien modèle porte explicitement sur une **nouvelle table encore vide**.

Ces résultats traitent les scénarios reproduits et les régressions couvertes ; ils ne prouvent pas l’absence universelle de tout blocage, panne ou différence fonctionnelle sur une infrastructure non inspectée.

## Absence d’opération sur la production

Les runners utilisent un Python dédié avec `-I -B`, une archive indépendante, un environnement synthétique et le chargement dotenv désactivé. Le réseau/natif est interdit dans le processus de tests, sauf la connexion épinglée au cluster PostgreSQL local prévu par le runner. Son parent ne démarre que les composants nécessaires au nouveau cluster synthétique et assure leur nettoyage.

Aucun `.env` réel, secret OAuth, phrase de chiffrement, archive réelle, base de production, compte Google Drive ou service mail de production n’a été utilisé. Aucun serveur ERP réel n’a été démarré, aucune opération métier de recette n’a été lancée sur la production et aucun déploiement n’a été déclenché pour cette qualification. La vérification des cinq fichiers utilisateur exclus se limite à leurs empreintes.

La migration nouvelle `inventory.0024_clientbalanceadjustment_and_client_permission` crée le journal et la permission correspondante ; elle ne contient aucun `RunPython`, aucune attribution de droit à un rôle et aucun recalcul de dette existante. SHA-256 UTF-8 approuvé : `776ce53c77766f25049726995507930d7fc83db91863c96a6392370cec3a22f3`.

## Limites et conditions avant publication

1. **INF02 n’est pas branché au déploiement.** `build.sh` reste inchangé. La connexion de migration doit être directe, non transaction-poolée ; la commande exige une confirmation explicite et refuse notamment un hôte Neon `-pooler`. Les possibilités de la formule Render et l’emplacement du contrôle de pré-déploiement restent à confirmer. Aucun secret ou réglage externe n’a été modifié pour résoudre ces choix. Neon documente que les migrations et les opérations dépendant d’une session nécessitent une connexion directe : [documentation de connexion/pooling](https://neon.com/docs/connect/connection-pooling).
2. **Plancher de retour de code à définir avant activation.** Après création d’écritures de dette, l’ancien code `1006fe9` ne connaît pas ce journal : une suppression peut échouer sur sa clé étrangère et un ancien CRUD peut modifier le solde sans journal. La compatibilité avec une table vide ne valide donc pas un retour à cette version après activation. Préparer/valider un pont A connaissant le journal, protégeant le solde et les suppressions, puis retenir A comme plancher avant B. Une migration déjà validée n’est pas annulée automatiquement quand une migration suivante ou un build échoue.
3. **R03 temporel reste ouvert.** La neutralité du contenu et des erreurs de réinitialisation est qualifiée ; l’envoi SMTP reste synchrone. Le canal temporel d’énumération n’est pas considéré clos. Aucun worker durable n’a été configuré ou autorisé dans ce lot.
4. **Clés API : transition compatible, pas bascule générale.** `API_IDEMPOTENCY_REQUIRED` demeure `False` par défaut pour les clients déjà installés ; l’option `required-v1` et le mécanisme de bascule sont testés. Cela ne garantit pas l’anti-doublon d’une ancienne intention cliente sans clé. La nouvelle correction financière dédiée impose sa clé dès le départ. Toute bascule globale attend la qualification des clients remplacés et une décision explicite.
5. **Dette : correction distincte, pas refonte comptable.** La sémantique du solde antérieur restant et les valeurs signées sont conservées. La saisie initiale autorisée reste possible ; le journal n’est pas ajouté une seconde fois au calcul des créances. Aucun historique ancien n’est reconstruit ni rôle automatiquement enrichi ; toute délégation du nouveau droit financier reste explicite.
6. **Clients mobiles séparés.** Les modifications Android/Flutter et leur recette constituent une qualification distincte : elles ne font pas partie des 42 sources de ce snapshot. Aucun APK signé, poste Windows ou périphérique d’impression réel n’est certifié par les suites backend.

La sauvegarde automatique, les autres réserves P1/P2 et la qualification de production ne sont pas clôturées par ce rapport. Le backend décrit est prêt pour une revue de commit local sur le périmètre qualifié ; ce verdict n’autorise ni ne constate un push, une fusion, une activation INF02 ou un déploiement.

## Liste des 42 overlays

```text
apps/accounts/forms.py
apps/accounts/permissions.py
apps/accounts/services/password_reset.py
apps/accounts/tests/test_password_reset.py
apps/accounts/tests/test_legacy_debt_permissions.py
apps/accounts/tests/test_password_reset_privacy.py
apps/accounts/views.py
apps/api/authentication.py
apps/api/idempotency.py
apps/api/serializers.py
apps/api/tests/test_shared_auth_throttling.py
apps/api/tests/test_client_balance_adjustments.py
apps/api/tests/test_idempotency_rollout.py
apps/api/views.py
apps/commerce/forms.py
apps/commerce/models.py
apps/commerce/services.py
apps/commerce/views.py
apps/commerce/web_idempotency.py
apps/commerce/tests/test_loading_lock_order.py
apps/commerce/tests/test_web_payment_idempotency.py
apps/core/security.py
apps/core/deployment_migrations.py
apps/core/management/commands/deploy_migrate.py
apps/core/tests/test_deployment_migrations.py
apps/core/tests/test_web_auth_rate_limit.py
apps/core/web_auth_rate_limit.py
apps/inventory/admin.py
apps/inventory/forms.py
apps/inventory/models.py
apps/inventory/services.py
apps/inventory/views.py
apps/inventory/urls.py
apps/inventory/tests/test_inventory.py
apps/inventory/tests/test_client_balance_adjustments.py
apps/inventory/balance_adjustments.py
apps/inventory/migrations/0024_clientbalanceadjustment_and_client_permission.py
apps/inventory/templates/inventory/client_detail.html
apps/inventory/templates/inventory/client_form.html
apps/inventory/templates/inventory/_client_balance_history.html
apps/inventory/templates/inventory/client_balance_adjustment_form.html
gestio_stock/settings.py
```
