# Précision de la localisation client

## Diagnostic — 13 septembre 2026

L'ancien code appelait getCurrentPosition une seule fois, avec haute précision,
maximumAge=0 et timeout=15 s. Il envoyait immédiatement toute mesure valide au
géocodeur, même si accuracy dépassait 100 m. L'avertissement était affiché après
le remplacement de l'adresse : il n'empêchait donc pas une adresse éloignée.
Les coordonnées venaient du navigateur, jamais du nom ou d'une ancienne fiche.

Le test utilisateur a été effectué sur ordinateur avec Opera. Le message visible
prouve une précision annoncée supérieure à 100 m, mais sa valeur exacte et les
coordonnées reçues ne sont pas connues. Les valeurs 1332 m / 1800 m sont des
exemples de test, pas des mesures récupérées sur son ordinateur. La cause A
(position navigateur approximative) est étayée ; aucune preuve ne permet
d'affirmer une erreur B de Google sur des coordonnées correctes.

La haute précision est une demande, pas une garantie matérielle. Sur ordinateur,
une position exacte peut rester indisponible. Le correctif refuse les mesures
trop larges ; il ne promet pas une précision à la porte ou au numéro de rue.

## Algorithme

- watchPosition uniquement au clic, enableHighAccuracy=true, maximumAge=0.
- Fenêtre globale de 12 secondes, indépendante des timeouts du fournisseur GPS.
- Conserver la mesure valide avec la plus petite accuracy.
- À 50 m ou moins : arrêter immédiatement et géocoder une seule fois.
- À l'échéance, jusqu'à 100 m inclus : géocoder la meilleure mesure et avertir.
- Au-delà de 100 m : aucun géocodage, adresse et champs cachés inchangés.
- Sans mesure : message d'expiration ; permission refusée : arrêt immédiat.
- Les erreurs transitoires laissent la recherche continuer jusqu'à l'échéance.
- clearWatch sur sélection, refus, expiration, saisie manuelle, soumission et
  sortie de page. Les callbacks tardifs ne peuvent écraser la saisie.
- Une requête Google maximum par recherche acceptée, aucune par mesure reçue.
- Le géocodage garde son timeout réseau distinct de 8 secondes côté navigateur.
- Aucun enregistrement automatique : seules les données choisies sont proposées
  dans les champs cachés, puis enregistrées avec le formulaire normal.

## Géocodage

Google v3 via POST /inventory/clients/reverse-geocode/ est conservé. Les réponses
sont examinées : parmi les résultats de type street_address/premise/subpremise
avec géométrie valide et situés à moins de 150 m du point, le plus proche est
privilégié. Sinon, l'ordre Google est conservé. Ce seuil concerne le résultat
cartographique et ne remplace pas accuracy du navigateur. formatted_address
est conservé sans reconstruire ni inventer un numéro. Pas de préremplissage de
wilaya sans correspondance fiable, pas de stockage supplémentaire du corps Google.

Les diagnostics techniques existants restent expurgés : aucun ajout de logs de
coordonnées ou de réponses brutes en production. Aucune clé ni adresse réelle
de référence n'est codée en dur.

## Vérification manuelle Opera / mobile

Sur Opera, vérifier les permissions de localisation du site et du système.
Si le système propose la localisation précise, l'autoriser volontairement.
Sur Android Chrome, vérifier également la permission précise au niveau Android.
Aucune permission n'est contournée. Le géocodage ne peut améliorer une position
physique erronée fournie par le système.

Après publication autorisée séparément : sur HTTPS, cliquer le bouton, attendre
au plus 12 secondes de recherche puis le géocodage, vérifier l'adresse proposée.
Si la position reste approximative, le formulaire doit conserver l'adresse et
proposer la saisie manuelle. Ne pas utiliser l'adresse réelle comme donnée forcée.
Les tests automatisés simulent GPS et Google ; ils ne prouvent pas la précision
physique sur Opera ni sur Android. Aucun de ces tests physiques n'a été réalisé.

## Fichiers et vérifications

- static/js/client-location.js : acquisition et sélection, nettoyage du suivi.
- apps/inventory/geocoding.py : sélection d'adresse proche dans les résultats.
- apps/inventory/templates/inventory/_client_location.html : messages discrets.
- locale/{fr,ar,en}/LC_MESSAGES/django.{po,mo} : traductions et compilation.
- browser_tests/client_location_accuracy.test.cjs : tests JS de l'algorithme.
- browser_tests/client_location.py : formulaire et erreurs FR/AR/EN.
- apps/inventory/tests/test_geocoding_diagnostics.py : résultats Google multiples.
- CHANGELOG.md et ce rapport.

Aucune migration, aucune modification de base métier ou de configuration secrète.
Branche fix/client-gps-accuracy ; commit local seulement, sans push ni fusion.

### Résultats

- 20 tests Django ciblés clients/géocodage : OK.
- 7 tests JavaScript (mesures successives, seuils, timeout, refus, annulation,
  callbacks tardifs, erreur transitoire) : OK.
- Suite complète : 234 tests en 419,017 s, aucun échec, un test PostgreSQL ignoré
  sous SQLite. Base de test locale indépendante, pas de connexion Neon production.
- 4 tests navigateur Edge/Chromium avec serveur Django local : OK en 236,969 s,
  FR/AR/EN, largeurs mobile et desktop ; fournisseur et positions simulés.
- Premier passage navigateur : un clic intercepté par la sidebar. Relance
  complète réussie sans forcer les clics ni changer le CSS. Cause de cet incident
  intermittent non établie ; dépendances CDN et environnement navigateur restent
  des réserves. Capture arabe mobile inspectée, coordonnées toujours masquées.
- manage.py check : aucun problème ; makemigrations --check --dry-run : aucun
  changement ; collectstatic, compileall, validation OpenAPI et diff --check : OK.

Commandes reproductibles :

```powershell
# Depuis un environnement local isolé SQLite, jamais sur la base de production.
.\.venv\Scripts\python.exe manage.py test --noinput
.\.venv\Scripts\python.exe manage.py test browser_tests.client_location --noinput
.\.venv\Lib\site-packages\playwright\driver\node.exe --test browser_tests/client_location_accuracy.test.cjs
.\.venv\Scripts\python.exe manage.py check
```

Le moteur Node livré avec Playwright est utilisé car node n'est pas dans PATH.
Le navigateur intégré n'étant pas disponible, les tests existants Playwright
Edge ont été utilisés. Le rendu réel sur Opera et la mesure physique restent
à valider après une publication autorisée séparément. Aucun test Android réel.

Sources : [API navigateur](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/watchPosition),
[géocodage inverse Google](https://developers.google.com/maps/documentation/geocoding/guides-v3/requests-reverse-geocoding).
