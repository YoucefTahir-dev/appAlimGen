# Localisation client — champ Adresse simplifié

## Cause et correction

L'implémentation précédente utilisait des widgets numériques visibles dans
ClientForm, un encadré dans _client_location.html et un état de proposition
dans client-location.js. Cet état imposait Confirmer / Conserver / Annuler.

Le formulaire présente désormais uniquement **Adresse [texte][📍]**, avec
un petit message d'état. Le bouton a un titre et un nom accessible traduits.
Le champ reste éditable ; aucun client n'est sauvegardé par la détection.

## Fichiers concernés

- apps/inventory/forms.py : cinq widgets HiddenInput, mêmes validations.
- apps/inventory/templates/inventory/client_form.html : bloc Adresse intégré
  dans la grille existante, sans ligne complète réservée au GPS.
- apps/inventory/templates/inventory/_client_location.html : input-group,
  icône Bootstrap existante, spinner et message discret.
- static/js/client-location.js : remplissage direct, aucune confirmation
  supplémentaire, protection contre les réponses tardives.
- static/css/styles.css : groupe sans retour à la ligne, bouton tactile 44 px
  minimum, coins logiques compatibles LTR/RTL.
- locale/{fr,ar,en}/LC_MESSAGES/django.po et django.mo : nouveaux messages.
- apps/inventory/tests/test_client_location.py : régression des widgets cachés.
- browser_tests/client_location.py : parcours actualisés et réponse tardive.
- CHANGELOG.md et ce rapport.

Aucun changement du modèle, de la migration inventory.0014_client_location,
du service de géocodage, des permissions ou du contrat API. Aucune nouvelle migration.

## Parcours exact

1. À l'ouverture : aucun GPS ni appel fournisseur ; l'adresse existante reste intacte.
2. Clic sur 📍 : spinner et message « Localisation en cours… », bouton désactivé.
3. GPS navigateur : haute précision, timeout 15 s, maximumAge 0 (position fraîche).
4. POST vers l'endpoint interne avec CSRF.
5. Succès : adresse automatiquement placée dans le champ, coordonnées et
   métadonnées dans les champs cachés, spinner arrêté.
6. L'utilisateur peut compléter/corriger le texte ; la position reste attachée.
7. Seul le bouton habituel Enregistrer sauvegarde le client.

Les champs conservés sont latitude, longitude, location_accuracy,
formatted_address et place_id. Aucun label, valeur numérique GPS ni bouton Maps
n'est affiché dans le formulaire. La fiche de consultation et son lien Maps
existants ne sont pas modifiés par cette correction.

## Erreurs et concurrence

- Refus, timeout, navigateur incompatible, position indisponible : petit message,
  pas de remplacement de l'adresse ni des anciennes coordonnées.
- Géocodage absent, clé non configurée, réponse vide ou panne réseau :
  « Adresse automatique indisponible. Saisissez l’adresse manuellement. »
  L'adresse existante est conservée et le GPS obtenu est préparé en champs cachés.
- Une adresse détectée trop longue pour le champ historique n'est ni tronquée
  ni substituée par des coordonnées ; saisie manuelle disponible.
- Précision > 100 m : avertissement textuel sans valeur numérique, non bloquant.
- Si l'utilisateur saisit une adresse ou soumet le formulaire pendant une recherche,
  celle-ci est invalidée ; une réponse tardive ne remplace pas sa saisie.
- Aucune boucle de permission, aucun appel automatique à l'ouverture et aucun
  enregistrement automatique. Aucun appel externe ajouté.

## Backend conservé

POST /inventory/clients/reverse-geocode/ reçoit latitude/longitude et retourne
formatted_address/place_id. Il ne modifie jamais un client. Authentification de
session, CSRF, permission add_client OU change_client et limite d'appels restent
actifs. Les écritures client restent soumises à leurs permissions normales.

GeocodingService réutilise GoogleGeocoder côté serveur. Un fournisseur alternatif
peut toujours être injecté. La clé n'est pas transmise dans le JavaScript.

L'API /api/v1/clients/ conserve les champs facultatifs et les validations :
coordonnées finies, latitude -90/90, longitude -180/180, précision >= 0,
latitude/longitude ensemble et longueurs limitées. HiddenInput ne dispense jamais
de ces contrôles. L'audit de localisation existant est conservé.

## Configuration inchangée

GOOGLE_MAPS_API_KEY : clé serveur facultative ; pas de clé en dur, aucun appel
Google réel dans les tests. Sans clé, GPS caché et adresse manuelle restent possibles.

PERMISSIONS_POLICY : doit autoriser geolocation=(self). HTTPS requis hors
localhost. La configuration Render n'est pas modifiée par cette branche.
Conserver les restrictions API/IP adaptées à la clé serveur et les quotas Google.
Le cache local de limitation existant n'est pas partagé entre workers.

## Tests

Les tests backend existants couvrent les clients sans GPS, valeurs invalides,
API, permissions, CSRF, migration additive et conservation des données historiques.

Le navigateur teste :
- FR/AR/EN sur largeur mobile 390 px, champ unique et coordonnées cachées ;
- remplissage automatique, absence de POST de sauvegarde avant Enregistrer ;
- correction manuelle, sauvegarde, réouverture et nouvelle détection en modification ;
- refus, indisponibilité, timeout et navigateur non compatible ;
- géocodage échoué avec conservation de l'adresse ;
- saisie et soumission pendant une recherche GPS, réponse tardive ignorée.

Les fournisseurs sont simulés : tests gratuits, déterministes, sans données réelles.
Les captures sont dans tmp/client-location/simple-{fr,ar,en}.png, ignorées par Git.

Résultats locaux du 13 septembre 2026 : suite complète **225 tests exécutés,
224 réussis, un ignoré** (test de concurrence réservé à PostgreSQL), en 355,2 s.
Les contrôles Django, compileall, collectstatic, validation OpenAPI et
makemigrations --check --dry-run passent. Aucune migration nouvelle détectée.
Trois parcours navigateur passent sur Edge/Playwright local (FR/AR/EN, erreurs,
saisie concurrente), sans appel Google réel. Le contrôle visuel confirme le
champ unique et le placement du bouton à gauche en RTL et à droite en LTR.
Le scénario de faible précision vérifie l'absence de valeur numérique affichée.

Le contrôle en lecture seule de l'ancienne version Render confirme HTTPS,
HTTP 200 et geolocation=(self). Il ne constitue pas une validation du nouveau
formulaire en production. Les erreurs HTTP 503 des logs de test correspondent
au fournisseur volontairement simulé en panne ; le favicon 404 préexistant est
sans rapport avec cette correction. La suite émet également un avertissement
JWT sur une clé courte utilisée par les fixtures de test, hors de ce changement.

Commandes locales, en base de tests isolée :

```powershell
$env:DATABASE_URL = ''
$env:DATABASE_ENGINE = 'sqlite'
$env:DJANGO_DEBUG = 'True'
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py test --noinput
.\.venv\Scripts\python.exe manage.py test browser_tests.client_location --noinput
```

## Recette staging Android restant à effectuer

1. Disposer d'un service HTTPS et d'une base staging indépendants.
2. Y publier cette branche après autorisation adaptée, jamais sur Render production.
3. Configurer éventuellement la clé de géocodage et vérifier Permissions-Policy.
4. Sur Android Chrome : ouvrir Nouveau client, vérifier Adresse + 📍 sans champs GPS.
5. Saisir puis effacer une adresse, cliquer 📍 et accepter la permission.
6. Vérifier le remplissage, corriger quelques mots, enregistrer puis rouvrir le client.
7. Refaire en refusant le GPS et sans fournisseur configuré ; la saisie reste possible.
8. Vérifier les logs et la console sans divulguer clé ni coordonnées réelles.

Aucun Android physique ou staging HTTPS n'est accessible dans cette session :
ces tests réels ne sont pas revendiqués comme réalisés. Le navigateur automatisé
local ne les remplace pas. Pas de carte interactive ni d'autocomplete dans cette phase.

## Publication

### Diagnostic complémentaire du géocodage — 13 septembre 2026

Constat réel local : GOOGLE_MAPS_API_KEY absente à la fois du processus et du
fichier .env (contrôle de présence uniquement, aucune valeur affichée). Sans
clé, aucun appel Google n'est effectué : le service échoue avant la requête.
La cause locale est donc missing_key. La configuration Render et les APIs/restrictions
du projet Google ne sont pas accessibles ici : la cause de production reste
non confirmée. Le message générique seul ne permettait pas de la déterminer.

Le frontend n'utilisait déjà pas le nom client. Son POST contient uniquement
latitude/longitude du navigateur. Le changement maximumAge=0 demande une nouvelle
mesure au lieu d'autoriser une position vieille de 30 secondes. La précision
réelle dépend toujours du navigateur et de l'appareil, pas du nom client.

Le fournisseur conserve le même endpoint Google, et l'endpoint Django reste
POST /inventory/clients/reverse-geocode/ avec CSRF et permissions existantes.
Les réponses de succès gardent formatted_address/place_id et ajoutent success
et address. Les erreurs fournisseur ajoutent success=false et code, avec HTTP 503.
Les autres codes HTTP (validation, permissions, quota local) restent inchangés.

Les logs geocoding.failed incluent un code sûr, le statut HTTP et le statut JSON
Google reconnu, sans journaliser la clé, l'URL, les coordonnées ou error_message
brut. Les explications connues de REQUEST_DENIED sont classées par motif sûr.

| Code | Vérification à effectuer |
| --- | --- |
| missing_key | Renseigner GOOGLE_MAPS_API_KEY dans l'environnement serveur |
| api_not_enabled | Vérifier Geocoding API dans le projet associé à la clé |
| invalid_key | Vérifier validité de la clé |
| referrer_restriction / ip_restriction | Vérifier restrictions adaptées à l'appel serveur |
| billing | Vérifier facturation Google du projet |
| request_denied | Refus non catégorisé : consulter la configuration Google |
| over_query_limit / over_daily_limit | Vérifier quotas et facturation |
| zero_results | Google n'a pas trouvé d'adresse pour la position |
| invalid_request | Vérifier la requête fournisseur |
| network_error / timeout | Vérifier connectivité du serveur |
| invalid_json / invalid_response / invalid_results / unexpected_status / empty_address | Réponse fournisseur invalide ou inexploitable |

Pour Google côté serveur, utiliser les restrictions API et IP adaptées, pas une
restriction de référent destinée au navigateur. Sources officielles :
[statuts du géocodage inverse](https://developers.google.com/maps/documentation/geocoding/guides-v3/requests-reverse-geocoding),
[sécurité des clés](https://developers.google.com/maps/api-security-best-practices).
Ne jamais copier une vraie clé dans le chat ou Git. Aucun appel Google réel,
aucune modification de variable Render et aucun accès à Neon production ici.

Fichiers complémentaires modifiés : apps/inventory/geocoding.py, views.py,
static/js/client-location.js, template _client_location.html, traductions FR/AR/EN,
tests/test_client_location.py, nouveau tests/test_geocoding_diagnostics.py,
browser_tests/client_location.py, CHANGELOG.md et ce rapport.

Le test physique Android HTTPS et la vérification du fournisseur avec une clé
réelle restent nécessaires. Un commit local du correctif ne configure pas Google
et ne résout pas à lui seul une variable absente sur Render.

Branche fix/gps-geocoding-diagnostics. Aucune fusion vers main, aucun push,
déploiement ou migration de production dans cette intervention.

Vérifications du correctif : 18 tests ciblés réussis ; suite complète de 232 tests
en 193,413 secondes, sans échec (un test réservé à PostgreSQL ignoré sous SQLite) ;
trois tests navigateur réussis en 101,307 secondes avec serveur Django local.
FR/AR/EN, nom vide ou arbitraire, coordonnées seules envoyées, adresse modifiable,
enregistrement et erreurs simulées sont couverts. Capture mobile française
inspectée dans tmp/client-location/simple-fr.png : adresse et bouton alignés,
coordonnées masquées et formulaire lisible.

manage.py check, compilation Python, collectstatic, validation OpenAPI et
git diff --check réussis ; makemigrations --check --dry-run ne détecte aucun
changement de modèle. Aucune migration nécessaire. Les logs de géocodage des
tests ne contiennent ni clé ni réponse fournisseur brute.
Le test Android physique sur staging HTTPS reste à réaliser ; il n'est pas
remplacé par ces simulations. La configuration Google réelle reste à valider.
