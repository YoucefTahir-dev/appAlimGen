# Création rapide de produit depuis un achat

## Périmètre

Branche locale : `feature/purchase-quick-product`. Aucune fusion dans `main`,
aucun déploiement Render et aucune connexion ou migration Neon production.

Le formulaire Achats proposait uniquement un choix de produits existants et
une ligne de formset, sans mécanisme de création AJAX. La page sait maintenant
ajouter plusieurs lignes et ouvrir une modale depuis chacune d'entre elles.

## Architecture

- `QuickProductForm` hérite de `ProductForm` : mêmes validations du nom et des
  prix, même résolution de marque et même génération des identifiants.
- `create_product_from_form` extrait la transaction de la vue produit normale.
  Les deux chemins utilisent ce service, le modèle Product et le journal de
  stock existant. La création rapide n'accepte aucun stock initial.
- `POST /inventory/products/quick-create/` reçoit le formulaire préfixé `quick`.
  Il renvoie HTTP 201 avec `product.id`, `reference`, `name`, `purchase_price`,
  ou HTTP 400 avec les erreurs structurées par champ.
- Authentification de session, CSRF et cumul des permissions
  `inventory.add_product` ET `commerce.add_purchase` sont obligatoires.
  Un succès est enregistré dans le journal d'audit.
- Le JavaScript conserve la ligne qui a ouvert la modale, ajoute le produit
  aux sélecteurs existants et futurs, puis sélectionne uniquement cette ligne
  et remplit son prix d'achat. Le formulaire principal n'est pas envoyé ni rechargé.
- Une soumission en cours désactive le bouton Créer et empêche la fermeture
  de la modale jusqu'au résultat. Annuler avant soumission ne crée rien.

## Choix compatibles avec le catalogue actuel

Le formulaire standard ne permet déjà plus de saisir la référence ou le
code-barres : ils restent automatiques, protégés par les contraintes uniques.
Une collision de code-barres généré est testée et ne crée pas de doublon.
Il n'y a donc pas de champ de code-barres libre ni de bouton « Utiliser ce
produit » pour un code saisi dans cette modale.

La modale demande nom, marque optionnelle, prix d'achat et trois tarifs.
Comme le formulaire produit standard, elle ne demande ni catégorie ni unité :
ces relations acceptent NULL dans le modèle. Photo, description, seuil de stock
et conditionnements optionnels restent accessibles dans la fiche produit complète.
Les règles du catalogue n'imposent pas l'unicité du seul nom : aucune nouvelle
règle d'unicité métier n'est inventée ici.

Le prix catalogue préremplit la ligne ; le prix réel de l'achat reste modifiable.
L'achat ne remplace pas le prix catalogue, conformément au comportement existant.
Un produit créé avec succès reste au catalogue à stock zéro si l'achat est ensuite
abandonné. C'est distinct de l'annulation de la modale avant création.

## Fichiers

- `apps/inventory/forms.py`, `services.py`, `views.py`, `urls.py`.
- `apps/commerce/forms.py`, `views.py`.
- `apps/commerce/templates/commerce/purchase_form.html`, `_purchase_line.html`.
- `static/js/purchase-form.js`.
- `static/css/styles.css` (fermeture de la modale en RTL).
- `locale/{fr,ar,en}/LC_MESSAGES/django.po` et `django.mo`.
- `apps/inventory/tests/test_quick_product.py`.
- `browser_tests/__init__.py`, `browser_tests/purchase_quick_product.py`.
- `CHANGELOG.md` et ce rapport.

## Vérifications

Les tests Django ajoutés vérifient les permissions séparées et cumulées, CSRF,
la méthode HTTP, les erreurs de champs, la génération des identifiants,
la collision de code-barres, l'audit, le stock zéro malgré une requête falsifiée,
puis un achat à deux lignes et un unique mouvement de stock de +100.

Le test navigateur utilise Microsoft Edge via Playwright, un serveur Django local
et une base de tests isolée. Il rejoue le scénario FR/AR/EN : annulation, validation
invalide puis corrigée, création sur la deuxième ligne, conservation de la première
ligne et de l'en-tête, ajout d'une troisième ligne, enregistrement et contrôle du stock.
Il vérifie l'absence d'exception JavaScript non interceptée.
Un passage rapide a révélé un déplacement tardif du focus pendant l'animation
Bootstrap : le code respecte maintenant le champ déjà choisi par l'utilisateur.

Commande spécifique (Playwright installé localement, sans dépendance production) :

```powershell
$env:DATABASE_URL = ''
$env:DATABASE_ENGINE = 'sqlite'
$env:DJANGO_DEBUG = 'True'
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe manage.py test browser_tests.purchase_quick_product --noinput
```

Le navigateur par défaut est `msedge`, configurable via `PLAYWRIGHT_CHANNEL`.
Les captures sont enregistrées dans `tmp/purchase-browser/`, ignoré par Git.
La suite habituelle s'exécute avec `manage.py test --noinput` ; le test navigateur
est exécuté séparément pour ne pas imposer Playwright au serveur de production.

Les traductions sont compilées avec `polib` localement (GNU msgfmt indisponible).
Aucune migration nouvelle n'est nécessaire. PostgreSQL et le staging distant
ne sont pas exercés pendant cette validation locale.

### Résultats locaux — 12 septembre 2026

- Suite Django complète : **214 tests exécutés, 213 réussis, 1 ignoré**,
  en 468 secondes. Le test ignoré exige PostgreSQL pour les verrous de lignes ;
  il ne peut pas être validé avec SQLite.
- Test navigateur séparé : **réussi**, scénarios français, arabe et anglais,
  en 44 secondes, sur écran bureau et viewport mobile.
- `manage.py check` : aucune anomalie.
- `manage.py makemigrations --check --dry-run` : aucun changement détecté.
- Compilation Python, collecte des fichiers statiques et compilation des
  traductions : réussies. `git diff --check` : aucune erreur.
- Démarrage vérifié via le serveur local du test navigateur, sans accès à la
  base de production. Aucune nouvelle table ni migration.
- Captures contrôlées : `tmp/purchase-browser/modal-{fr,ar,en}.png` et
  `tmp/purchase-browser/purchase-{fr,ar,en}.png`. La fermeture de la modale
  arabe est à gauche et les champs sont alignés à droite.

Ces résultats ne remplacent pas une recette PostgreSQL/staging ni un essai
sur téléphone physique. Aucun commit, push, merge ou déploiement n'a été effectué.
