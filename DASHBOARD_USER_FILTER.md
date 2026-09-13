# Filtre utilisateur du tableau de bord

## Périmètre de données

Le lien réel entre une vente et un utilisateur est `Sale.created_by`, une clé
étrangère vers `accounts.User`. Les lignes, les coûts historiques, les KPI,
les graphiques, les tops produits et les tops clients utilisent ce même filtre
ORM sur `sale__created_by`.

Les charges sont reliées par `Expense.created_by` et sont donc filtrées par
utilisateur lorsque celui-ci est sélectionné. Les achats restent globaux : le
modèle `Purchase` ne contient actuellement aucune relation vers un utilisateur,
et il serait incorrect d'attribuer ces montants à chaque vendeur.

## Sécurité et URL

Les gestionnaires et administrateurs peuvent sélectionner les utilisateurs
actifs ayant une activité de vente. Un vendeur ne peut pas élargir son
périmètre avec `?user=...` : le backend force automatiquement `user` à son
propre compte. Le paramètre GET est conservé dans les liens d'export PDF/Excel.

## Contrôles réalisés

- filtre global par période et utilisateur ;
- période précédente avec le même utilisateur ;
- CA, nombre de ventes, panier moyen, marge, charges et gain net ;
- graphiques, catégories, tops produits/clients ;
- exports PDF et Excel avec utilisateur analysé ;
- valeurs conservées après actualisation ;
- tests d'accès IDOR vendeur et tests multi-utilisateurs.

Le navigateur intégré n'étant pas disponible dans l'environnement d'exécution,
la vérification interactive reste à effectuer sur l'URL de staging avant toute
publication en production.
