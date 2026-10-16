# Recherche client, prix et créances — état d'avancement

Date : 16 octobre 2026
Périmètre de cette livraison : **lot 1 uniquement — recherche intelligente des clients dans les ventes Web**.

## Résultat du lot 1

Le sélecteur qui chargeait tout le catalogue clients a été remplacé, sur la création et la modification d'une vente, par une autocomplétion distante :

- le champ est vide sur une nouvelle vente ;
- aucune requête n'est envoyée avant deux caractères ;
- la saisie est temporisée de 300 ms ;
- la recherche utilise l'ORM Django et `icontains` sur le nom, le téléphone et le NIF ;
- les résultats sont triés et limités à 20 ;
- chaque résultat transporte l'identifiant réel du client et permet de distinguer les homonymes par téléphone, NIF et type de client ;
- le client déjà enregistré est restauré lors de la modification d'une vente ;
- une saisie libre non sélectionnée est refusée par le navigateur et par le formulaire Django ;
- le catalogue complet n'est jamais embarqué dans la page ;
- l'endpoint est authentifié, soumis aux permissions de création/modification des ventes, non mis en cache et limité à 60 recherches par minute et par utilisateur par défaut.

Le modèle `Client` ne possède pas de champ séparé « raison sociale » ou « référence client ». Le champ `name` existant porte le nom ou la raison sociale. Le NIF (`tax_number`) et le téléphone ont donc été retenus comme critères complémentaires, sans migration de données.

## Fichiers modifiés

- `apps/commerce/forms.py` : branchement du nouveau widget sur `SaleForm.client`.
- `apps/commerce/widgets.py` : widget composite et restauration sécurisée du libellé du client sélectionné.
- `apps/commerce/templates/commerce/widgets/client_autocomplete.html` : champ accessible, liste de résultats et messages d'état.
- `apps/commerce/templates/commerce/sale_form.html` : utilisation du module d'autocomplétion existant.
- `apps/commerce/views.py` : recherche ORM bornée et limitation de fréquence.
- `apps/commerce/urls.py` : route Web de recherche.
- `static/js/product-autocomplete.js` : comportement client (debounce, annulation des requêtes obsolètes, clavier, clic et validation).
- `static/css/styles.css` : positionnement et défilement de la liste.
- `apps/commerce/tests/test_client_search.py` : tests serveur et formulaire.
- `browser_tests/client_autocomplete.py` : parcours navigateur dédié.
- `browser_tests/product_autocomplete.py` et `browser_tests/sale_unit_form.py` : adaptation des parcours existants au nouveau sélecteur.

## Services et mécanismes réutilisés

- modèle `apps.inventory.models.Client` ;
- permissions granulaires `commerce.add_sale` et `commerce.change_sale` ;
- cache Django pour la limite de fréquence ;
- événement `change` sur l'identifiant `id_client`, déjà consommé par la tarification des lignes de vente ;
- conventions d'autocomplétion produit existantes (requête distante bornée, annulation, navigation clavier).

## Évolution d'interface Web

Nouvelle route authentifiée :

`GET /commerce/clients/search/?q=<texte>`

Elle renvoie uniquement les champs utiles à la sélection : `id`, `name`, `phone`, `tax_number` et `customer_type`. Il s'agit d'un endpoint interne à l'interface Web, et non d'une évolution de l'API mobile versionnée.

## Android et API mobile

Aucune modification Android ou DRF dans ce lot. Les lots 2 et 3 n'ont pas été commencés, conformément à la demande d'arrêt après le lot 1.

## Migrations

Aucune migration de schéma ou de données. Aucune donnée de production n'a été lue ou modifiée.

## Tests exécutés

- `manage.py check` : réussi.
- suite Django complète : **415 tests réussis, 10 ignorés**.
- 53 tests ciblés (`test_client_search`, `test_commerce`, `test_product_search`) : réussis.
- parcours navigateur recherche client (champ vide, seuil de deux caractères, homonymes, sélection par identifiant, restauration en modification, absence du catalogue) : réussi.
- parcours navigateur autocomplétion produit, tarification et ventes en français/arabe/anglais : réussi.
- parcours navigateur vente carton/pièce et calculs instantanés : réussi.

Les réponses 404 sur `/favicon.ico` observées dans les journaux des tests navigateur sont sans rapport avec la fonctionnalité et n'affectent pas les assertions.

## Risques résiduels

- La limite de fréquence s'appuie sur le backend de cache configuré. Avec un cache local et plusieurs instances Render, elle est appliquée séparément par instance. Un cache partagé pourra être utilisé si le service est multiplié horizontalement.
- Une recherche `%texte%` (`icontains`) ne bénéficie pas toujours d'un index B-tree PostgreSQL sur un catalogue très volumineux. La limite de résultats protège le volume retourné ; un index trigramme pourra être évalué sur mesures réelles sans être nécessaire pour ce lot.
- Le nom/raison sociale reste un champ métier unique (`Client.name`). Ajouter ultérieurement une référence client ou une raison sociale distincte nécessiterait une décision métier et une migration additive.

## Plan de déploiement proposé

1. Valider fonctionnellement le lot 1 sur une branche ou un environnement de préproduction.
2. Exécuter les tests Django et les trois parcours navigateur concernés.
3. Déployer normalement via Render avec `collectstatic` ; aucune migration nouvelle n'est attendue.
4. Vérifier la création d'une vente, la recherche avec deux caractères, un homonyme et la modification d'une vente existante.
5. Surveiller les erreurs HTTP 4xx/5xx et le temps de réponse de la route de recherche.

Aucun déploiement n'a été lancé dans cette étape.

## Plan de rollback

Redéployer le commit précédent. Comme il n'existe aucune migration ni transformation de données, le rollback ne requiert aucune opération en base. Les ventes enregistrées restent compatibles : la valeur persistée est toujours la même clé étrangère `Sale.client_id`.

## Décisions métier en attente

- Validation fonctionnelle du lot 1 avant tout commit, push ou déploiement.
- Lot 2 : règle exacte de conservation/avertissement lorsqu'un prix manuel est présent et que le conditionnement change.
- Lot 3 : règle d'affectation explicite des règlements globaux à une ou plusieurs factures, gestion des surpaiements et annulations.
- Décider si une référence client et une raison sociale séparée doivent être ajoutées au modèle.

## Lots non démarrés

- Lot 2 — prix automatique carton/pièce Web et Android : **non démarré dans cette étape**.
- Lot 3 — créances clients et paiements : **non démarré dans cette étape**.
