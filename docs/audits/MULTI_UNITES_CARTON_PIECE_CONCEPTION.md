# Gestion multi-unités carton / pièce — audit et conception

Date de l'audit : 14 octobre 2026
Dépôt audité : `gestio_stock`
Révision auditée : `fix/p0-03-historical-invoices` — `706ded3`
Périmètre : audit et conception uniquement. Aucun modèle, service, contrat API, écran Web/Flutter ou fichier de migration n'a été modifié.

## 1. Décision d'architecture et verdict

### Verdict

- **Option A confirmée** : un seul stock canonique par produit, exprimé dans l'unité de base du produit.
- **Réutiliser `ProductPackaging`** : le projet possède déjà une abstraction générique de conditionnement avec facteur de conversion. Il ne faut pas créer un second système spécialisé carton/pièce.
- **Ventes partiellement prêtes** : les ventes Web/API/Android savent déjà sélectionner un conditionnement, convertir la quantité et conserver des snapshots de ligne.
- **Achats et chargements non prêts** : ils ne connaissent que `product + quantity` et interprètent cette quantité directement comme quantité de stock.
- **Tarification incomplète** : les trois tarifs existent uniquement au niveau du produit. Pour un conditionnement, le tarif est aujourd'hui obligatoirement proportionnel au facteur ; le prix de carton indépendant demandé n'est donc pas possible.
- **NO-GO migration de données** : le dépôt ne permet pas d'établir avec certitude si chaque stock de production représente des pièces, des cartons ou une autre unité propre au produit. Aucune multiplication des stocks ou des prix ne doit être lancée avant validation métier produit par produit.
- **NO-GO activation globale** : la fonctionnalité doit rester désactivée pour les produits historiques non classifiés.

### Point bloquant historique

Le code établit une sémantique technique : `Product.quantity` est la quantité dans l'unité de base courante du produit. Il n'établit pas une sémantique métier universelle « pièce » :

- `Product.unit` est nullable et référence une table `Unit` libre (`apps/inventory/models.py:85-102,118-131`) ;
- les quantités sont des entiers, sans code d'unité immuable dans le journal (`apps/inventory/models.py:330-417`) ;
- les achats historiques ne conservent aucun facteur ou libellé d'unité (`apps/commerce/models.py:157-166`) ;
- les chargements ne conservent aucun facteur ou libellé d'unité (`apps/inventory/models.py:483-499`) ;
- la migration des lignes de vente existantes a figé un facteur `1` et le nom de l'unité produit disponible au moment de la migration, sans conversion (`apps/commerce/migrations/0010_saleline_packaging_saleline_packaging_factor_and_more.py:6-14`) ;
- les anciennes migrations montrent qu'un premier modèle de conditionnement a été créé, alimenté puis supprimé avant l'implémentation actuelle (`inventory/0004`, `0005`, `0006` et `commerce/0002`, `0005`). Ces anciennes lignes ne constituent donc pas une preuve de l'unité physique d'origine.

La base SQLite locale a été consultée en lecture seule uniquement. Elle contient surtout des données de test, des unités hétérogènes et nulles, et aucun `ProductPackaging`. Elle n'est pas une copie probante de Neon production. Elle ne permet aucune conclusion sur les stocks réels.

**Décision exigée avant migration** : fournir et valider un inventaire produit par produit indiquant l'unité réelle de la quantité courante, l'unité des tarifs courants et le facteur carton. Jusqu'à cette validation, le système ne doit modifier ni stock, ni prix, ni mouvement historique.

## 2. État du dépôt au début de l'audit

- Branche active : `fix/p0-03-historical-invoices`.
- La branche contient la sécurisation récente des factures historiques (`706ded3`) ; `main` est encore à `697f6d8` lors de l'audit.
- Le suivi Git des fichiers versionnés était propre.
- Éléments non suivis préexistants et laissés intacts : `.pip-tools-cache/`, `android-rpp02n-test/`, `appAlimGen-mobile/`, `docs/user-guides/`.
- L'application Flutter auditée se trouve dans le dossier non suivi `appAlimGen-mobile/`. Toute implémentation future devra confirmer qu'il s'agit bien du dépôt/version Android à livrer.
- Les dernières migrations sont `inventory.0016` et `commerce.0013`. La migration `commerce.0013` appartient au lot de snapshots historiques présent sur la branche active.

## 3. Architecture actuelle

### 3.1 Produit, unité et conditionnement

`Product` possède :

- une unité libre et nullable via `Product.unit` ;
- un prix d'achat, trois tarifs de vente et un ancien alias `sale_price` aligné sur le détail ;
- `quantity` et `minimum_stock` entiers ;
- aucune déclaration explicite « unité de base confirmée », « vente à la pièce » ou « vente au carton ».

`ProductPackaging` possède :

- `product` ;
- `name` ;
- `conversion_factor >= 1` ;
- `default_sale_price` ;
- code-barres et statut actif (`apps/inventory/models.py:264-308`).

Le conditionnement de base n'est pas une ligne `ProductPackaging` : l'absence de `packaging_id` signifie implicitement facteur `1` et utilise `Product.unit` comme libellé. Cette convention est importante pour la rétrocompatibilité des anciens clients.

Le formulaire produit Web expose un formset de conditionnements, mais son formulaire principal n'expose pas `Product.unit` (`apps/inventory/forms.py:9-44,170-189`). Android expose l'unité du produit, mais ne permet pas de gérer ses conditionnements (`product_summary.dart:37-126`, `product_form_screen.dart:18-121`).

### 3.2 Stock global et journal

`Product.quantity` est le solde courant. Tous les changements passent normalement par `record_stock_movements()` :

- verrouillage des produits dans l'ordre des identifiants ;
- calcul d'un delta ;
- contrôle anti-stock négatif ;
- mise à jour conditionnelle du solde ;
- écriture d'un `StockMovement` immuable dans la même transaction (`apps/inventory/services.py:65-130`).

`StockMovement.quantity`, `applied_delta`, `balance_before` et `balance_after` n'ont aucun snapshot d'unité. Ils sont cohérents seulement si la signification de l'unité de base du produit reste stable dans le temps.

Les ajustements manuels, imports Excel, seuils, listes, alertes et valeur de stock utilisent directement `Product.quantity`. L'import impose aussi une quantité entière et ne transporte pas de facteur de conversion (`apps/inventory/views.py:253-331`).

### 3.3 Ventes

La vente est la partie la plus avancée :

- `SaleLine.quantity` est la quantité déduite du stock ;
- `packaging_quantity` est la quantité saisie/facturée ;
- `packaging_factor` est le facteur historique ;
- `packaging_name` est le libellé historique ;
- `unit_price` est le prix de l'unité choisie ;
- `unit_cost` est le coût historique par unité de base ;
- les snapshots P0-03 conservent produit, unité et total HT de ligne (`apps/commerce/models.py:391-429`).

À la création et à la modification, le serveur recharge et verrouille le conditionnement, refuse un conditionnement inactif ou appartenant à un autre produit, puis exige :

`quantity == packaging_quantity × packaging_factor`

Le stock global ou opérateur est contrôlé et déduit en quantité de base (`apps/commerce/services.py:126-262,265-444`). Les quantités demandées sont agrégées par produit, ce qui protège déjà une vente composée de plusieurs lignes du même produit.

Le Web et Android savent sélectionner un conditionnement dans une vente. L'API accepte déjà `packaging_id` facultatif. Sans ce champ, la quantité reste une quantité de base de facteur `1` (`apps/api/serializers.py:320-342`). Cette convention doit être conservée.

### 3.4 Achats

`PurchaseLine` conserve seulement : produit, quantité et prix d'achat. La quantité saisie devient directement l'entrée de stock et le prix est multiplié directement par cette quantité (`apps/commerce/models.py:157-245`, `apps/commerce/services.py:447-468`).

Conséquences :

- impossible de distinguer 100 cartons de 100 pièces ;
- aucun facteur historique ;
- aucun libellé d'unité historique ;
- aucun prix négocié par conditionnement distinct de son interprétation actuelle ;
- aucune sélection d'unité dans le Web ou Android ;
- Android ne possède actuellement qu'un écran de création d'achat, pas de modification dans le flux audité.

Plusieurs lignes du même produit ne sont pas interdites dans un achat, mais elles seraient aujourd'hui additionnées dans la même unité implicite.

### 3.5 Stock opérateur et bons de chargement

`OperatorStock.quantity` et son journal sont des quantités entières canoniques (`apps/inventory/models.py:502-563`). Validation et clôture des chargements transfèrent exactement `LoadingOrderLine.quantity` entre stock global et stock opérateur (`apps/inventory/services.py:263-330`).

`LoadingOrderLine` possède seulement produit, quantité chargée et quantité retournée. La contrainte `uniq_product_per_loading` interdit actuellement deux lignes pour le même produit, donc l'exemple « 10 cartons + 5 pièces » n'est pas représentable (`apps/inventory/models.py:483-499`). Web, API et Flutter reprennent la même structure sans unité.

Les transitions brouillon, validation, clôture et annulation sont transactionnelles. Les verrous existants sur bon, produits et stocks opérateur doivent être préservés. Le contrôle d'un chargement actif par opérateur et l'idempotence API limitent déjà la double validation.

### 3.6 Tarification

Les trois tarifs `super_wholesale_price`, `wholesale_price` et `retail_price` existent sur `Product` et sont sélectionnés selon le type client.

Pour un conditionnement, `get_sale_price()` multiplie toujours le tarif de base par `conversion_factor` (`apps/inventory/pricing.py:14-28`). `ProductPackaging.default_sale_price` est validé et exposé, mais n'est pas utilisé par ce calcul. Ainsi, avec 24 pièces à 220 DA, le serveur proposerait 5 280 DA et ne peut pas proposer automatiquement un carton indépendant à 4 800 DA.

Les calculs commerciaux utilisent `Decimal`, une quantification à `0.01` et `ROUND_HALF_UP` (`apps/commerce/services.py:1-23`). Cette règle doit rester l'autorité serveur.

### 3.7 Factures et impression

Les sorties de vente utilisent déjà la quantité et l'unité choisies :

- aperçu A4 Web : `packaging_name`, `packaging_quantity`, `unit_price` ;
- PDF : mêmes valeurs et snapshots produit ;
- tickets 58/80 : conditionnement et quantité saisie ;
- API d'impression Android : `packaging`, `packaging_factor`, `quantity` saisie et `stock_quantity` convertie (`apps/printing/services.py:114-184`).

Le lot P0-03 ajoute des snapshots d'entreprise, client, produit, unité et montants. Une évolution multi-unités doit s'appuyer sur ces snapshots et ne jamais recalculer une ancienne facture avec le facteur actuel.

Les achats n'ont pas de document historique équivalent dans le périmètre actuel.

### 3.8 Tableau de bord et rapports

Le tableau de bord :

- calcule chiffre d'affaires et marges de vente avec `packaging_quantity × unit_price` et `quantity × unit_cost` ;
- agrège le nombre de produits vendus avec `SaleLine.quantity`, donc en unités de base ;
- agrège les achats avec `PurchaseLine.quantity` et leur valeur avec `purchase_price × quantity` ;
- valorise le stock avec `Product.quantity × Product.purchase_price` (`apps/core/dashboard.py:204-216,309-429`).

Après évolution, les montants restent valides si les prix de ligne sont ceux de l'unité saisie et les quantités stock restent canoniques. En revanche, tout libellé « nombre de produits » devra préciser « unités de base » ; les achats devront utiliser quantité saisie pour le montant et quantité de base pour les statistiques physiques.

### 3.9 API et Android

L'API produit expose déjà `conditionnements`. L'API vente expose les deux quantités et le facteur. L'OpenAPI documente ces propriétés.

Flutter :

- sait lire et envoyer `packaging_id` pour une vente ;
- présente la quantité facturée et conserve aussi `stockQuantity` ;
- n'envoie aucun conditionnement pour un achat ;
- ne gère pas les conditionnements dans la fiche produit ;
- n'envoie aucune unité dans un bon de chargement ;
- affiche tous les stocks comme un entier brut ;
- utilise les snapshots de vente pour facture/aperçu/impression, mais le pilote ESC/POS devra être vérifié sur 58 et 80 mm après ajout de libellés plus longs.

Les erreurs métier Django sont déjà transformées en erreurs API structurées et Flutter mappe les erreurs de champs. Les nouveaux codes devront rester additifs.

## 4. Invariants de conversion

Pour chaque produit activé en multi-unités :

1. Une unité de base explicite et validée existe.
2. `Product.quantity`, `minimum_stock`, `OperatorStock.quantity`, les deltas des journaux et les contrôles de disponibilité sont exprimés dans cette unité de base.
3. Un conditionnement possède un facteur entier strictement positif vers l'unité de base.
4. Pour une ligne de document :

   `base_quantity = entered_quantity × conversion_factor`

5. `entered_quantity` est un entier strictement positif dans la V1. Les fractions de carton ne sont pas acceptées.
6. Le facteur vient exclusivement du serveur. Une valeur fournie par un client est ignorée ou rejetée.
7. Le facteur et le libellé appliqués sont copiés sur la ligne historique avant tout mouvement de stock.
8. Le montant de ligne est :

   `line_total = entered_quantity × unit_price_for_selected_unit`

9. Aucune modification future de `ProductPackaging` ne modifie un document ou mouvement passé.
10. Plusieurs lignes du même produit avec des unités différentes sont autorisées ; la disponibilité est contrôlée sur la somme de leurs quantités de base.
11. Aucun stock carton séparé n'est créé. L'affichage `cartons complets + reste` est une vue calculée : division entière et modulo.

Exemple de référence : 100 cartons × 24 + 30 pièces = 2 430 pièces. Une vente de 2 cartons + 5 pièces déduit 53 pièces et laisse 2 377 pièces, affichables comme 99 cartons + 1 pièce.

## 5. Structure de données proposée

### 5.1 Conserver `Product` comme racine

Conserver les colonnes existantes pour compatibilité. Ajouter, de façon additive :

- un statut de classification, par exemple `unit_semantics_status` : `legacy_unclassified`, `base_confirmed`, `multi_unit_enabled` ;
- éventuellement une date/auteur de validation métier ;
- des drapeaux d'usage seulement si nécessaires à l'UX. La présence de conditionnements actifs peut suffire pour autoriser le carton ; l'unité de base reste autorisée par défaut sauf décision contraire.

`Product.unit` reste l'unité de base. Pour les produits carton/pièce validés, elle doit être « Pièce ». Elle ne doit pas être changée automatiquement pour les produits historiques.

Le stock existant ne doit pas être dupliqué dans un nouveau champ carton. Un champ parallèle temporaire de stock créerait deux sources de vérité et est déconseillé.

### 5.2 Étendre `ProductPackaging`

Utiliser une ligne active « Carton » avec `conversion_factor = pieces_per_carton`. Ajouter :

- un code/type stable optionnel (`CARTON`, `OTHER`) distinct du libellé traduit ;
- `purchase_enabled` et `sale_enabled` si un conditionnement doit être autorisé seulement dans certains flux ;
- `reference_purchase_price`, nullable ;
- `super_wholesale_price`, `wholesale_price`, `retail_price`, nullable ;
- éventuellement un ordre d'affichage.

Les trois prix de conditionnement sont des montants pour **un conditionnement**, non des prix par pièce. Ils peuvent être non proportionnels. Les colonnes sont `DecimalField`, jamais `float`.

`default_sale_price` doit rester lisible pendant la transition. Il ne faut ni l'effacer ni prétendre qu'il est actuellement l'autorité tarifaire. Après inventaire de ses usages et données, il pourra servir de fallback documenté ou être déprécié lors d'une phase contract ultérieure.

### 5.3 Étendre `PurchaseLine`

Conserver `quantity` comme quantité de stock/base afin de ne pas casser les services et rapports. Ajouter l'équivalent des ventes :

- `packaging` nullable ;
- `packaging_name` snapshot ;
- `packaging_factor` snapshot, défaut `1` ;
- `packaging_quantity` ou `entered_quantity` ;
- `line_total_snapshot` recommandé ;
- éventuellement `unit_name_snapshot` et références produit/fournisseur si un vrai document d'achat historique est attendu.

`purchase_price` reste le prix réellement négocié pour l'unité sélectionnée. Le coût de référence du produit ne doit pas écraser ce prix.

### 5.4 Étendre `LoadingOrderLine`

Conserver `quantity` et `returned_quantity` en unité de base. Ajouter :

- `packaging` nullable ;
- nom, facteur et quantité saisie snapshotés ;
- pour les retours, une saisie d'unité peut être offerte mais le résultat est toujours converti en base.

La contrainte `uniq_product_per_loading` doit évoluer afin d'autoriser carton et pièce pour le même produit. Le service doit agréger les quantités de base par produit avant le contrôle et le transfert. Il peut soit autoriser plusieurs lignes de présentation, soit normaliser l'entrée en une structure enfant ; conserver deux lignes historiques est préférable pour relire le document tel que saisi.

### 5.5 Journaux de stock

Le journal continue d'enregistrer un delta canonique. Pour les mouvements saisis directement par un humain, ajouter si nécessaire des métadonnées snapshotées `entered_quantity`, `unit_name`, `conversion_factor`. Pour un mouvement issu d'un document, l'identifiant source et la ligne source font foi ; éviter de dupliquer des données sans besoin d'audit.

Ne jamais changer la signification historique de `StockMovement.quantity`. Un changement d'unité de base d'un produit existant nécessite une opération de cutover métier spécifique, pas un simple `UPDATE` des soldes.

## 6. Règles tarifaires proposées

Ordre d'autorité pour une vente :

1. tarif du conditionnement pour le type client, s'il est explicitement configuré ;
2. fallback temporaire `tarif unité de base × facteur`, afin de préserver le comportement actuel ;
3. absence de prix valide : refuser la ligne et demander une configuration, plutôt que fabriquer un prix.

Pour l'unité de base, les trois champs actuels du produit restent l'autorité. Pour le carton, les trois champs du conditionnement deviennent la matrice indépendante.

Validations :

- prix non négatifs ;
- ordre Super gros ≤ Gros ≤ Détail à décider par conditionnement ;
- interdiction de vente sous coût conservée, mais le coût de comparaison doit être le coût de base historique × facteur ;
- décision métier requise sur l'usage d'un coût négocié récent/moyen pour les contrôles futurs ;
- arrondi monétaire unique `Decimal('0.01')`, `ROUND_HALF_UP`, côté serveur.

Reprise : les tarifs produit existants restent les tarifs de l'unité de base actuelle. Aucun tarif ne doit être déplacé vers « pièce » ou « carton » sans classification validée. Les prix `ProductPackaging.default_sale_price` existants doivent être exportés et vérifiés avant éventuelle copie dans le tarif Détail carton.

## 7. Impacts par flux

### Produits Web et Android

- afficher l'unité de base de façon explicite ;
- section « Unités et conditionnements » commune aux permissions existantes ;
- permettre facteur, usages achat/vente et matrice de prix ;
- afficher `1 carton = X pièces` ;
- afficher le stock brut en base et sa décomposition normalisée ;
- interdire l'activation multi-unités tant que le produit est `legacy_unclassified` ;
- ne pas permettre de modifier librement l'unité de base après mouvements sans workflow de conversion dédié.

### Achats

- sélecteur de conditionnement par ligne sur Web et Android ;
- prix saisi et total calculés dans l'unité choisie ;
- stock crédité avec la quantité convertie ;
- modification/suppression contrepassant exactement `quantity` de base snapshotée ;
- autoriser deux lignes du même produit, carton et pièce ;
- verrouiller les produits dans un ordre stable et laisser le modèle/service de stock écrire une seule fois chaque delta.

### Ventes

- conserver le flux existant, déjà proche de la cible ;
- remplacer le tarif proportionnel obligatoire par la matrice avec fallback ;
- afficher facteur et disponibilité convertie ;
- conserver l'agrégation des besoins par produit et les verrous PostgreSQL ;
- ajouter des tests explicites de vente mixte du même produit ;
- ne pas modifier les snapshots des anciennes lignes.

### Stock global, inventaire et transferts

- stocker seulement la quantité de base ;
- présenter total base, cartons complets et reste lorsque le carton est actif ;
- lors d'un inventaire, accepter éventuellement cartons + pièces puis envoyer au service une quantité physique finale en base ;
- seuil minimum à conserver en base ; l'UI doit afficher son unité ;
- imports/exports Excel à versionner avec colonnes d'unité explicites, tout en continuant d'accepter l'ancien format comme unité de base ;
- les transferts global ↔ opérateur travaillent uniquement en base après conversion serveur.

### Bons de chargement

- ajouter conditionnement et quantité saisie sur chaque ligne ;
- agréger avant validation ;
- transférer une seule quantité de base ;
- préserver brouillon, validation, clôture, retour, contrainte de chargement actif, permissions et idempotence ;
- rendre la validation monotone : un bon déjà validé ne doit jamais recréditer le stock opérateur ;
- conserver les unités saisies pour impression et historique.

### Factures et impressions

- continuer à lire exclusivement les snapshots pour les documents certifiés ;
- afficher une ligne carton et une ligne pièce séparées ;
- afficher quantité saisie, libellé snapshoté, PU de l'unité choisie et total snapshoté ;
- ne jamais consulter le facteur actuel pour réimprimer une facture ;
- tester PDF A4, aperçu Web, tickets HTML 58/80, `print-data`, aperçu Flutter et ESC/POS Bluetooth en FR/AR/EN et RTL ;
- conserver `stock_quantity` dans les données techniques, sans l'afficher comme quantité facturée.

### Paiements, permissions et sécurité

Les paiements ne changent pas : ils portent sur le total du document. Les permissions de lecture de prix doivent aussi protéger les nouveaux tarifs de conditionnement. Toute conversion, validation et tarification est recalculée côté serveur. Le mobile ne transmet qu'un identifiant de conditionnement, jamais un facteur fiable.

## 8. Contrat API rétrocompatible

### Principe

Conserver `packaging_id` comme identifiant de l'unité secondaire. Ne pas introduire en parallèle une chaîne `unit=CARTON` comme seconde autorité. Le code/type stable peut être retourné en lecture pour l'UX.

### Écritures

Vente, achat et chargement nouveaux :

```json
{
  "product_id": 123,
  "packaging_id": 45,
  "quantity": 2,
  "unit_price": "4800.00"
}
```

- `quantity` reste la quantité saisie dans le conditionnement sélectionné ;
- `packaging_id` absent ou `null` signifie l'unité de base, facteur `1`, comme aujourd'hui pour les ventes ;
- le serveur charge le conditionnement actif du produit et calcule la quantité de base ;
- achats et chargements peuvent utiliser `product` au lieu de `product_id` tant que leur contrat actuel le demande ; documenter précisément chaque schéma sans alias ambigu.

### Lectures additives

Retourner, sans retirer les champs actuels :

- `packaging_id` / `packaging` ;
- `packaging_name` ;
- `packaging_factor` ;
- `packaging_quantity` ou alias clair `entered_quantity` ;
- `quantity` conservé comme quantité de base dans les réponses historiques actuelles ;
- `base_quantity` peut être ajouté comme alias explicite avant une future dépréciation ;
- prix et total historiques.

Pour les produits, étendre `conditionnements` avec usages et trois tarifs selon permission. Mettre à jour `openapi.yaml` et les tests de schéma.

### Anciens clients

Un ancien Android qui n'envoie pas `packaging_id` continue à créer une ligne dans l'unité de base courante, facteur `1`. Cette règle ne signifie pas « pièce » tant que le produit n'est pas classifié. Un produit dont l'unité de base serait convertie ne doit donc pas être exposé à un ancien client pendant le cutover sans version minimale imposée ou feature flag.

## 9. Plan de migration PostgreSQL

### Phase 0 — audit obligatoire, sans écriture

Produire depuis un environnement autorisé un export de production contenant au minimum :

- identifiant, référence, nom, `unit`, stock, stock minimum ;
- trois tarifs et prix d'achat ;
- conditionnements existants, facteurs et prix ;
- volumes de lignes achat/vente/chargement/mouvements ;
- soldes opérateurs et chargements encore actifs ;
- anomalies : unité nulle, facteur invalide, libellés carton/pièce contradictoires, prix incohérents.

Le fichier de décision métier doit classer chaque produit :

- `BASE_ALREADY_PIECE` : quantité et tarifs actuels sont par pièce ;
- `BASE_OTHER_CONFIRMED` : unité autre que pièce à conserver ;
- `BASE_CARTON_REQUIRES_CUTOVER` : stock/tarifs actuels par carton ;
- `UNKNOWN` : aucune activation ni conversion.

### Phase 1 — expand

- ajouter seulement des colonnes nullable/default sûr et index nécessaires ;
- ajouter les snapshots achat/chargement sans backfill sémantique ;
- ajouter les tarifs conditionnement nullable ;
- ajouter le statut de classification produit ;
- ne lancer aucun signal/service de stock ;
- ne pas supprimer ou renommer les colonnes existantes ;
- tester durée et verrous sur une copie PostgreSQL représentative.

### Phase 2 — backend compatible

- double lecture additive et écriture des nouveaux snapshots ;
- comportement facteur `1` pour requêtes legacy ;
- feature flag global désactivé puis activation par produit classifié ;
- commandes d'audit idempotentes séparées du build Render.

### Phase 3 — classification/reprise contrôlée

- importer uniquement le fichier approuvé, avec checksum, auteur et journal ;
- produits déjà en pièce : ne changer aucun stock, seulement confirmer l'unité et configurer le carton ;
- produits dans une autre unité conservée : garder cette base et n'activer que des multiples entiers compatibles ;
- produits réellement stockés en cartons mais devant passer en pièces : **workflow séparé requis**. Ne pas multiplier silencieusement les anciens mouvements. Définir un événement de conversion auditable, gérer soldes globaux/opérateurs/chargements ouverts et conserver l'ancienne unité des documents. Cette catégorie reste bloquée jusqu'à décision métier et conception détaillée du cutover.

### Phase 4 — interfaces et activation progressive

1. Backend compatible déployé.
2. Web mis à jour mais feature désactivée.
3. Android compatible distribué et version minimale vérifiée.
4. Activation sur quelques produits `BASE_ALREADY_PIECE`.
5. Contrôle stock/mouvements/factures.
6. Extension par lots.

### Phase 5 — contract, ultérieure

Seulement après période d'observation : rendre certains champs obligatoires, retirer les fallbacks devenus inutiles et éventuellement déprécier `default_sale_price`. Aucun retrait dans le premier déploiement.

## 10. Plan de tests

Tous les tests transactionnels et de concurrence doivent être exécutés sur PostgreSQL réel. Les tests SQLite restent utiles mais ne valent pas preuve pour `select_for_update`.

### Modèle et prix

- produit uniquement unité de base ;
- produit pièce + carton facteur 24 ;
- facteur 0/négatif/non entier refusé ;
- conditionnement inactif ou d'un autre produit refusé ;
- trois prix pièce et trois prix carton indépendants ;
- fallback proportionnel legacy ;
- changement de facteur sans modification des documents passés ;
- permissions masquant les prix.

### Achats

- 100 cartons, 30 pièces, puis achat mixte : stock final 2 430 ;
- deux lignes du même produit autorisées ;
- prix négociés différents du théorique ;
- modification de quantité/unité/produit ;
- suppression/annulation avec contrepassation exacte ;
- échec sur seconde ligne : transaction entièrement annulée ;
- aucun mouvement doublé lors d'un retry idempotent.

### Ventes

- 2 cartons + 5 pièces : déduction 53 et montant 10 700 ;
- stock 52 refusé, stock 53 accepté ;
- création, modification et suppression/annulation ;
- stock global et stock opérateur ;
- trois types client, remises, TVA et paiement ;
- même produit sur plusieurs lignes ;
- snapshots inchangés après modification facteur/prix/nom produit.

### Chargements et opérateurs

- 10 cartons + 5 pièces transfèrent 245 ;
- vente opérateur 2 cartons + 3 pièces déduit 51 ;
- retour de 194 ;
- clôture, annulation, double validation, chargements simultanés ;
- cohérence globale + opérateur avant/après chaque transition.

### Stock, dashboard et import

- conversion et affichage division/modulo ;
- seuils et alertes en base ;
- valeur de stock ;
- agrégats physiques en base et montants en quantité saisie ;
- import ancien format facteur `1` ;
- import versionné avec unité ;
- réconciliation du journal et absence de mouvement doublé.

### API et Flutter

- client nouveau avec `packaging_id` ;
- client ancien sans conditionnement ;
- facteur falsifié impossible ;
- OpenAPI et réponses JSON additives ;
- formulaires produit/achat/vente/chargement Flutter ;
- erreurs stock/prix/permission localisées ;
- facture A4 et tickets 58/80 FR/AR/EN, RTL, impression Bluetooth réelle.

### Non-régression

- factures historiques P0-03 et leurs statuts d'intégrité ;
- paiements, marges, dashboard, exports ;
- permissions/RBAC ;
- authentification/JWT et réinitialisation du mot de passe ;
- tests existants de stock, ventes, achats, concurrence, idempotence et chargements.

## 11. Déploiement sécurisé

1. Fusionner et stabiliser d'abord P0-03 afin que l'implémentation multi-unités parte de la protection des factures historiques.
2. Sauvegarder Neon et réaliser un test de restauration mesuré.
3. Auditer/classifier les produits sans écriture.
4. Tester les migrations expand sur un clone PostgreSQL isolé de volume représentatif.
5. Déployer le backend rétrocompatible, feature flag désactivé.
6. Vérifier `/healthz/`, `/readyz/`, schéma, journaux, temps de réponse et verrous.
7. Déployer le Web puis distribuer Android compatible.
8. Activer une liste blanche de produits déjà confirmés en pièces.
9. Surveiller : stock négatif, erreurs de conversion, différences ledger/solde, doublons idempotence, erreurs d'impression et temps de verrouillage.
10. Étendre progressivement ; ne jamais activer les produits `UNKNOWN`.

Le build Render exécute actuellement les migrations. Les reprises de données et classifications potentiellement longues ne doivent pas être placées dans ce build. Elles nécessitent une procédure séparée, bornée, idempotente et explicitement autorisée.

## 12. Rollback

### Avant toute écriture multi-unités

- désactiver le feature flag ;
- revenir au binaire précédent, qui ignore les colonnes additives ;
- conserver les colonnes : ne pas tenter de migration inverse destructive en urgence.

### Après des documents multi-unités

Un rollback de code seul n'est pas sûr : un ancien client pourrait afficher ou tarifer une quantité de base comme quantité commerciale. Procédure :

1. couper les nouvelles écritures commerciales ;
2. conserver le backend compatible multi-unités en lecture ;
3. désactiver la sélection de nouveaux conditionnements ;
4. diagnostiquer les documents/mouvements créés ;
5. corriger uniquement par opérations métier compensatoires traçables ;
6. ne jamais supprimer ou réécrire les documents historiques.

Les migrations de suppression/renommage et toute conversion de produit `BASE_CARTON_REQUIRES_CUTOVER` sont irréversibles au sens métier sans sauvegarde/restauration et procédure dédiée. Elles ne font pas partie du premier lot.

## 13. Décisions métier en attente

Les réponses suivantes sont obligatoires avant l'étape d'implémentation/migration :

1. Pour chaque produit de production, la quantité actuelle est-elle en pièce, carton ou autre unité ?
2. Pour chaque produit, les trois tarifs actuels sont-ils des prix pièce, carton ou autre ?
3. Le `purchase_price` produit représente-t-il le coût d'une pièce, d'un carton ou l'unité de base courante ?
4. Les anciennes lignes d'achat et de chargement ont-elles toujours utilisé cette même unité ?
5. Existe-t-il des conditionnements déjà saisis en production et `default_sale_price` doit-il être repris comme tarif Détail carton ?
6. Un produit peut-il interdire la vente à la pièce tout en conservant la pièce comme unité de stock ?
7. L'ordre Super gros ≤ Gros ≤ Détail doit-il être imposé séparément pour chaque conditionnement ?
8. Le contrôle « pas de vente sous coût » doit-il utiliser le coût de référence courant, un coût moyen pondéré ou le coût historique du lot ?
9. Les retours de chargement doivent-ils conserver la combinaison saisie carton/pièce ou seulement la quantité totale de base ?
10. Faut-il afficher les statistiques physiques en pièces uniquement, ou aussi en décomposition carton + reste ?
11. Quelle version Android minimale pourra être imposée avant d'activer un produit dont l'unité de base change ?
12. Pour les produits actuellement stockés en cartons mais devant devenir stockés en pièces, faut-il réaliser un cutover audité ou créer une nouvelle fiche/SKU ?

## 14. Autorisation requise pour la suite

La conception peut être mise en œuvre sans conversion globale en commençant par les produits `BASE_ALREADY_PIECE`. Toutefois, conformément au point bloquant de la mission :

- aucune migration sémantique de stock n'est autorisée ;
- aucune activation globale n'est sûre ;
- aucun tarif historique ne doit être réinterprété ;
- aucun produit non classifié ne doit passer automatiquement en « pièce ».

L'étape 2 devra être explicitement autorisée après validation de ce rapport et des décisions métier. Elle devra produire `docs/audits/MULTI_UNITES_CARTON_PIECE_IMPLEMENTATION.md`, les migrations additives, les tests PostgreSQL/Flutter et les résultats réels, sans déploiement automatique.
