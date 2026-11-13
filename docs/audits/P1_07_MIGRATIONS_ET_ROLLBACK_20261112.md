# P1 — Garde de migration et borne de retour arrière

État : composant en qualification. **Non branché dans build.sh**, aucune migration de production exécutée pour ces essais. L’offre réelle du service Render et sa connexion de migration directe ne sont pas encore confirmées. Ne pas confondre le plan déclaré dans render.yaml avec le plan réellement souscrit.

## Contrat de la commande préparée

`python manage.py deploy_migrate --plan-only` contrôle en lecture seule le graphe et les migrations en attente. Le plan normal ne descend jamais vers une ancienne migration. Une migration inconnue, un conflit, une migration non atomique, une empreinte source modifiée ou un changement des types d’opérations bloque le déploiement. L’empreinte UTF-8 est normalisée LF pour rester identique sous Windows/Linux.

Seule l’expansion `inventory.0024_clientbalanceadjustment_and_client_permission` est approuvée dans ce lot : création d’une nouvelle table et métadonnées de permission. Une base vierge ne doit pas être initialisée silencieusement par cette commande ; son bootstrap et les migrations historiques non appliquées nécessitent une procédure distincte revue.

L’exécution nécessite une connexion PostgreSQL **directe**, vérifiée par l’opérateur, et l’option `--direct-connection-confirmed`. Le nom Neon contenant `-pooler.` est refusé avant toute requête. Ne jamais passer ce drapeau simplement pour contourner un refus. Une URL, un mot de passe ou un jeton ne doit pas apparaître dans la commande ou le rapport.

Un verrou advisory de session commun aux déploiements est acquis avant de reconstruire le plan. Attente maximale par défaut : trente secondes. Attente d’un verrou SQL : cinq secondes. Durée d’un statement : cent vingt secondes. Ces limites sont bornées et le timeout global du build doit rester en place. Les paramètres antérieurs sont restaurés ; le PID propriétaire est contrôlé avant libération. Un échec de nettoyage ferme la connexion. Aucun chemin ne tente une migration descendante de compensation.

La connexion de l’application peut rester poolée ; il faut alors prévoir/configurer un chemin de migration direct avant d’intégrer cette garde au déploiement. Le composant actuel n’invente pas une URL directe et ne change pas DATABASE_URL ou une offre payante à la place du propriétaire.

## Déploiement en deux phases et rollback

Un build échoué laisse l’ancien code disponible, mais **ne signifie pas que la base n’a pas changé**. Les migrations PostgreSQL atomiques sont transactionnelles par migration ; une première migration validée peut rester appliquée si la suivante échoue. Le test de panne partielle vérifie ce cas sur des tables synthétiques.

Avant d’activer les nouvelles écritures de dette, préparer et qualifier une version-pont A : modèle/journal connus du code, protection de suppression conservée, et sauvegardes ordinaires n’écrivant jamais le solde. Déployer et attester A avant la version B activant le circuit de correction. A devient la borne minimale de rollback de B.

Le code historique `1006fe9` est compatible avec l’expansion **tant que le nouveau journal est vide**, mais ne constitue pas une cible de retour arrière après activation : il ignore les nouvelles clés étrangères et pourrait réécrire un solde sans journal. Une suppression de client peut alors échouer, même si la base protège la trace. Le test ancien modèle/nouveau schéma vide ne prouve pas davantage.

Si A n’est pas qualifiée, ne pas activer B et ne pas annoncer le rollback comme résolu. En incident : suspendre les mutations financières concernées, examiner le plan appliqué sans extraire les données et préparer un correctif en avant. Ne pas lancer `migrate ... ancienne_version`, `--fake`, une restauration ou une suppression de journal pour débloquer un build.

## Préconditions avant branchement/publication

1. Confirmer l’offre Render réelle ; aucun déplacement vers un pre-deploy payant sans cette information.
2. Confirmer/configurer la connexion de migration directe sans révéler sa valeur.
3. Qualifier le verrou entre deux vrais processus/connexions, le timeout SQL, la libération après exception, la panne partielle et la compatibilité A/B sur PostgreSQL jetable.
4. Préparer la version-pont et attester son arbre, CI et déploiement ; appliquer les expansions seulement via le chemin qualifié.
5. Après activation, limiter tout retour de code aux versions ayant le contrat de journal et de profil de A.

Sources primaires : [transactions des migrations Django 5.2](https://docs.djangoproject.com/en/5.2/topics/migrations/), [verrous PostgreSQL 18](https://www.postgresql.org/docs/18/explicit-locking.html), [limitations des connexions poolées Neon](https://neon.com/docs/connect/connection-pooling), [phases de déploiement Render](https://render.com/docs/deploys).

## Qualification du composant, hors intégration finale

Snapshot isolé `a8dc0e2f6a444735be1e16e741dff817`, base `1006fe9`, avec cinq fichiers explicitement superposés seulement. SQLite : 13 cas comptés, 8 réussis et 5 réservés à PostgreSQL. PostgreSQL 18 TLS local neuf : 13/13 réussis sans skip ; PID distincts pour la concurrence et pour la preuve de libération du verrou (un verrou de session étant réentrant, le même PID ne suffirait pas). La panne de la seconde migration ne laisse pas sa table créée, mais conserve la première expansion déjà validée.

Rapports sous `tmp/p0c-admin-tests-20261111/` : `sqlite_tests-1794509546296228200.json` et `postgresql_tests-1794509569998674800.json`. Le parent confirme l’arrêt du cluster et la suppression du runtime synthétique. Imports limités au snapshot et sources inchangées : PASS. Aucun accès production, cloud ou archive réelle.

La première qualification a refusé l’empreinte de migration calculée avec un décodage PowerShell inadapté aux accents UTF-8. L’empreinte approuvée est maintenant calculée en UTF-8 explicite, normalisée LF, et validée sur les deux moteurs. Les rapports initiaux échoués restent conservés pour traçabilité ; ils ne sont pas les preuves finales.
