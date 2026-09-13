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
3. GPS navigateur : haute précision, timeout 15 s, maximumAge 30 s.
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

Branche fix/client-location-simple-ux. Aucune fusion vers main, aucun push,
déploiement ou migration de production dans cette intervention.
Commit local autorisé après une nouvelle vérification : 13 tests ciblés réussis
et trois tests navigateur réussis en 102,5 secondes. Le test Android physique
sur staging HTTPS reste à réaliser ; il n'est pas remplacé par ces simulations.
