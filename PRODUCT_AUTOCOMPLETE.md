# Recherche produit AJAX — achats et ventes

## Diagnostic

Avant cette évolution, chaque `ModelChoiceField` produit était rendu comme un
`select` Django. Chaque nouvelle page chargeait donc tous les produits dans le
HTML. Le formulaire de vente chargeait aussi tous les conditionnements actifs.
Les scripts dépendaient des `select` déjà présents et la création rapide d'un
produit ajoutait des options à toutes les lignes.

Le dépôt n'utilisait ni Select2, ni Tom Select, ni Choices.js. Une implémentation
JavaScript légère a donc été retenue, sans nouvelle dépendance ni migration.

## Comportement livré

- Champ texte vide et ID produit caché dans chaque ligne de formset.
- Recherche à partir de deux caractères, temporisée de 300 ms.
- Ancienne requête annulée et réponse tardive ignorée.
- Vingt résultats maximum, recherche ORM avec `Q` sur nom, référence, code-barres
  et marque ; code-barres exact, référence exacte puis début de nom prioritaires.
- Aucun SQL construit manuellement et aucune recherche non bornée.
- Suggestions par ligne, fermeture en cliquant ailleurs, souris, tactile,
  flèches haut/bas, Entrée et Échap ; rôles ARIA pour l'accessibilité.
- Modifier le texte après une sélection efface immédiatement l'ID caché.
  La validation navigateur et Django interdit un nom libre non sélectionné.
- Les nouvelles lignes utilisent la délégation d'événements et fonctionnent sans
  réinitialisation globale. Chaque ID caché reste propre à sa ligne.

### Achats

Le résultat expose uniquement `id`, `name`, `reference`, `stock` et
`purchase_price`. La sélection préremplit le prix d'achat, qui reste modifiable.
Le bouton existant « Nouveau produit » est conservé. Le texte recherché préremplit
le nom de la modale ; après création, le produit est sélectionné uniquement dans
la ligne cible et son prix est appliqué.

### Ventes

Le résultat expose uniquement `id`, `name`, `reference`, `stock` et le tarif
résolu côté serveur pour le client courant. Il ne contient ni coût d'achat, ni
les autres tarifs, ni marge, ni donnée fournisseur. Après sélection, l'endpoint
de prix existant reste la source de vérité et fournit les conditionnements du
produit choisi. Le tarif est recalculé pour le type du client et le
conditionnement. Le changement de client recalcule les lignes actives.

## Sécurité et autorisations

Endpoint : `GET /commerce/products/search/?q=...&context=sale|purchase`.

- Connexion obligatoire.
- `add` ou `change` vente/achat obligatoire selon le contexte.
- 100 caractères maximum et réponse `Cache-Control: private, no-store`.
- Même périmètre appliqué par l'endpoint de prix de vente.
- Un rôle avec modification seule peut rechercher lors d'une édition.

Le dépôt ne contient aucun modèle de stock opérateur, fourgon, tournée active ou
bon de chargement. Le stock est actuellement global. Aucun filtrage fictif n'a
été ajouté : `commercial_products()` est le point central documenté pour intégrer
un futur périmètre de lignes, qui devra aussi être appliqué lors de la validation
et de l'enregistrement des documents pour garantir la sécurité métier.

## Performance

La requête charge uniquement les colonnes nécessaires à la sérialisation et au
calcul du tarif, trie côté base et applique
`LIMIT 20`. Les tests vérifient une seule requête produit après mise en cache des
permissions. Les index uniques existants aident les recherches exactes sur
référence/code-barres. `icontains` sur nom et marque reste acceptable pour un
catalogue raisonnable. Avant une croissance importante, mesurer PostgreSQL avec
`EXPLAIN ANALYZE`, puis envisager `pg_trgm` et des index GIN trigrammes ; aucune
migration non mesurée n'est ajoutée ici.

## Fichiers principaux

- `apps/commerce/product_search.py` : service commun et périmètre d'accès.
- `apps/commerce/views.py`, `urls.py` : endpoint et durcissement du tarif.
- `apps/commerce/widgets.py` et template widget : contrôle composite.
- `apps/commerce/forms.py` : widget et conditionnements bornés au produit.
- `static/js/product-autocomplete.js` : recherche, sélection et clavier.
- `static/js/purchase-form.js`, `sale-formset.js` : intégrations métier.
- Templates achats/ventes, CSS et traductions FR/AR/EN.
- Tests backend et navigateur, changelog et ce rapport.

## Validation

- 7 tests backend ciblés : recherche, casse, champs, classement, limite,
  permissions, données sensibles, tarifs, formulaires et validation d'ID.
- Suite Django complète finale : 241 tests en 301,741 s, aucun échec ; un test réservé
  à PostgreSQL ignoré sous SQLite.
- Deux scénarios navigateur Edge/Chromium isolés en 95,336 s : vente à deux lignes en
  FR/AR/EN, tarifs Détail/Gros/Super Gros, conditionnement, stock, paiement,
  clavier, debounce, invalidation d'ID ; achat dynamique et création rapide.
- Aucun appel externe, aucune base Neon production et aucune donnée métier réelle.
- `manage.py check`, migrations sèches, compilation Python, `collectstatic`,
  validation OpenAPI et `git diff --check` exécutés.

Le navigateur intégré n'était pas disponible ; le pilote Edge local du projet a
été utilisé. Les tests ne remplacent pas une recette staging HTTPS. Aucun staging
indépendant n'a été fourni, donc aucun déploiement ni test de données réelles.

Branche `feature/product-autocomplete`. Ne pas fusionner vers `main` ou déployer
Render avant validation utilisateur explicite.
