# Localisation client — phase 1

## Audit et périmètre

Le modèle Client ne possédait que l'adresse texte et la wilaya. Aucun champ GPS,
service de géocodage ou carte client n'était présent dans cette version.
La politique HTTP désactivait la géolocalisation (`geolocation=()`).
Les vues et l'API disposent déjà de permissions métier et d'un journal d'audit.

Développement initial sur `feature/client-geolocation`, sans publication en production.
La publication vers main a ensuite été autorisée sous réserve de tests réussis.
Les bases utilisées par les tests
sont temporaires et isolées. L'adresse existante reste inchangée.

## Modèles et migration

`apps/inventory/models.py` ajoute à Client cinq champs `null=True, blank=True` :

- latitude et longitude : FloatField, bornes -90/90 et -180/180 ;
- location_accuracy : FloatField, mètres, valeur finie >= 0 ;
- formatted_address : CharField de 1000 caractères ;
- place_id : CharField de 255 caractères.

`apps/inventory/migrations/0014_client_location.py` ajoute les colonnes, sans
renommer, supprimer ni remplir les anciennes données. Aucune nouvelle table.
Latitude et longitude doivent être renseignées ensemble. Une précision ne peut
pas exister sans position. Les valeurs NaN et infinies sont rejetées.
`apps/inventory/location.py` centralise validation et audit ; le modèle, le
formulaire et l'API les réutilisent. Les mises à jour SQL directes/bulk ne passent
pas par `Model.save` : elles doivent respecter ces validations si ajoutées plus tard.

## Formulaire et comportement

Fichiers : `apps/inventory/forms.py`, `views.py`,
`templates/inventory/client_form.html`, `_client_location.html`, `client_detail.html`,
`static/js/client-location.js` (les chemins de templates sont sous apps/inventory).

Le clic demande une position avec haute précision, délai de 15 secondes et
position mise en cache par le navigateur au maximum 30 secondes. Aucune demande
GPS ni appel Google à l'ouverture d'une fiche. Le navigateur conserve son propre
consentement et peut ne pas redemander si une autorisation est déjà accordée.

La proposition ne remplit pas les champs enregistrés avant confirmation :

- Confirmer cette adresse : coordonnées et adresse détectée reprises ;
- Conserver le GPS et modifier manuellement : coordonnées reprises, adresse
  manuelle préservée ;
- Annuler : aucune modification de la localisation précédente.

Il faut ensuite enregistrer le formulaire. Une proposition en attente bloque
la soumission pour éviter un enregistrement ambigu. La précision > 100 m affiche
un avertissement non bloquant. Refus, indisponibilité, délai dépassé, HTTP non
sécurisé ou navigateur incompatible permettent toujours une saisie manuelle.
Si l'adresse détectée dépasse les 255 caractères du champ historique, elle reste
dans formatted_address ; l'utilisateur garde le GPS et adapte le texte manuellement.

La fiche et le formulaire proposent un lien Google Maps basé sur les coordonnées,
y compris 0,0. Modifier manuellement les coordonnées dans le formulaire efface
les métadonnées de la précédente détection. L'API efface les métadonnées omises
lorsqu'une position existante change, sans toucher à address.

## Service et endpoints

- `apps/inventory/geocoding.py` : GeocodingService avec fournisseur injecté ;
  GoogleGeocoder est le fournisseur par défaut, remplaçable ultérieurement.
- `POST /inventory/clients/reverse-geocode/` : champs latitude/longitude,
  session authentifiée, CSRF et permission add_client OU change_client.
  Renvoie formatted_address/place_id, mais ne sauvegarde jamais de client.
  HTTP 400 si coordonnées invalides, 429 si limite atteinte, 503 si service absent
  ou indisponible. Aucune erreur fournisseur ou clé privée renvoyée au navigateur.
- API existante `/api/v1/clients/` : champs optionnels ajoutés via ClientSerializer,
  mêmes permissions create/update/partial_update qu'avant. Les anciennes requêtes
  sans champs GPS restent compatibles ; PATCH adresse seule garde la localisation.
- `apps/api/views.py` et `apps/api/serializers.py` : validation et audit dédiés.

Audit dans AuditLog existant : identifiant client, anciennes/nouvelles coordonnées,
utilisateur et date. Les coordonnées sont sensibles : limiter l'accès et définir
la rétention des journaux. Pas de clé API journalisée.

## Configuration

`.env.example` et `gestio_stock/settings.py` documentent :

```dotenv
GOOGLE_MAPS_API_KEY=
PERMISSIONS_POLICY=camera=(), microphone=(), geolocation=(self), payment=()
```

La clé est uniquement côté serveur, jamais dans les templates ou le JavaScript.
La laisser vide désactive les appels Google, pas le GPS ni la saisie manuelle.
Pour staging : activer Geocoding API, limiter la clé à cette API et aux IP de sortie
du serveur si disponibles. Les restrictions par référent HTTP concernent une
future clé navigateur Maps, à séparer de cette clé serveur. Définir quotas et
alertes de facturation dans Google Cloud. Ne jamais transmettre une vraie clé dans Git.

Les appels utilisent l'[API officielle de géocodage inverse](https://developers.google.com/maps/documentation/geocoding/guides-v3/requests-reverse-geocoding).
Vérifier les conditions Google applicables au stockage et à l'utilisation des
adresses retournées avant activation réelle. Aucun appel Google réel durant les tests.

Limite applicative de 30 appels par utilisateur/minute dans le cache Django,
sans stockage de réponses Google. Le cache local par défaut n'est pas partagé
entre workers : un cache partagé et les quotas Google sont nécessaires pour
un plafond global en production. Timeout serveur 5 s, timeout fetch 8 s.

Si Render/staging définit déjà `PERMISSIONS_POLICY` avec `geolocation=()`, cette
variable reste prioritaire : la mettre à jour avant la recette. Le navigateur
doit utiliser HTTPS (localhost est admis en local). Ne pas activer geolocation=*.

## Tests et reproduction locale

Les catalogues `locale/{fr,ar,en}/LC_MESSAGES/django.po` et `.mo` contiennent
les nouveaux libellés. Le contrôle visuel a également conduit à traduire les
titres Ajouter un client / Modifier le client et Notes, auparavant manquants.
`CHANGELOG.md` résume cette évolution.

`apps/inventory/tests/test_client_location.py` couvre modèle/formulaire/API,
clients sans GPS, bornes, NaN/infini, précision, modification et nettoyage des
métadonnées, audit, RBAC, CSRF, limite d'appels, fournisseur absent/erreur/succès.

`browser_tests/client_location.py` couvre le parcours mobile FR/AR/EN, confirmation,
annulation, sauvegarde et réouverture, lien Maps, refus/timeout/indisponibilité,
navigateur incompatible et géocodage échoué avec conservation de l'adresse.
La localisation navigateur et Google sont simulés. Playwright/Edge est une
dépendance locale de test, non ajoutée aux dépendances de production.

Résultats locaux du 12 septembre 2026 :

- neuf tests backend de géolocalisation réussis ;
- avant publication : test supplémentaire de migration préservant un client
  historique, son adresse, téléphone, solde et tarif ; 12 tests ciblés réussis
  avec la géolocalisation et l'i18n ;
- suite Django complète : **223 tests, 222 réussis et un ignoré** (concurrence
  réservée à PostgreSQL), durée 316,7 secondes ;
- deux tests navigateur réussis, incluant FR/AR/EN et scénarios d'erreur ;
- après les dernières traductions, nouvelle exécution : 11 tests ciblés
  géolocalisation/i18n réussis et deux tests navigateur réussis en 92,2 secondes ;
- `check`, vérification des migrations, compilation Python, collecte des fichiers
  statiques et validation du contrat OpenAPI réussis ;
- migration appliquée automatiquement uniquement aux bases temporaires de tests ;
- captures `tmp/client-location/proposal-{fr,ar,en}.png` ; aucune barre horizontale
  de page dans le scénario mobile à 390 px, boutons accessibles et RTL arabe.

La première exécution navigateur avait échoué dans le test lui-même : lecture ORM
synchrone à l'intérieur du contexte Playwright. Les assertions base sont désormais
faites après sa fermeture ; l'application n'a pas été modifiée pour masquer l'erreur.
Le HTTP 503 est volontaire dans le scénario fournisseur indisponible.

```powershell
$env:DATABASE_URL = ''
$env:DATABASE_ENGINE = 'sqlite'
$env:DJANGO_DEBUG = 'True'
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py test apps.inventory.tests.test_client_location --noinput
.\.venv\Scripts\python.exe manage.py test browser_tests.client_location --noinput
.\.venv\Scripts\python.exe manage.py test --noinput
```

## Recette Android HTTPS à réaliser

1. Préparer une branche/service staging et une base indépendante, jamais Neon production.
2. Sauvegarder la base staging puis appliquer les migrations dans cet environnement.
3. Configurer éventuellement la clé serveur et Permissions-Policy ci-dessus.
4. Sur Android physique, ouvrir l'URL HTTPS staging, se connecter et ouvrir Nouveau client.
5. Saisir une adresse manuelle, demander le GPS et accepter la permission.
6. Vérifier coordonnées, précision et adresse proposée ; confirmer puis enregistrer.
7. Rouvrir le client, vérifier les données, ouvrir Voir sur Maps.
8. Refaire avec refus GPS, localisation désactivée, annulation et géocodage sans clé.
9. Vérifier qu'un utilisateur sans modification client ne peut changer ses coordonnées.
10. Vérifier console navigateur et logs serveur, sans exposer clés ni données clients.

## Réserves et phase suivante

Aucun Android physique, clé Google réelle ou staging HTTPS dédié n'a été utilisé.
La recette terrain et la compatibilité effective PostgreSQL restent à valider.
La recherche Maps, l'autocomplete, la carte interactive et le marqueur déplaçable
sont volontairement réservés à la deuxième phase, après validation de celle-ci.
Pas de tournées, zones ou calcul d'itinéraires ajoutés.
