# Audit de parité fonctionnelle Web / Android — ERP El Amine

Date de l'audit : 17 octobre 2026

Référence Web : dépôt Django `gestio_stock`, branche `main`, commit `535441e`

Application Android : dépôt Flutter `appAlimGen-mobile`, branche `main`, commit `9e8c6f6`

## 1. Conclusion exécutive

L'application Android couvre le cœur opérationnel : authentification, permissions, tableau de bord synthétique, produits, clients, fournisseurs, ventes, achats (création et modification), factures, règlements, créances incluant la dette antérieure, stocks et mouvements, bons de chargement et impression Bluetooth.

Elle n'est toutefois pas encore à 100 % du Web. Cette phase de remédiation a livré :

1. la saisie Android du stock initial mixte en cartons et pièces ;
2. la simulation, la confirmation et la correction contrôlée des stocks historiques ;
3. la modification transactionnelle d'un achat avec réajustement du stock ;
4. l'affectation FIFO d'un règlement client sur plusieurs factures ;
5. l'intégration de la créance antérieure et de ses règlements immuables/annulables ;
6. le changement de mot de passe connecté, le journal de stock et la création rapide des catégories de charges.

Les principaux écarts restants sont l'administration mobile des utilisateurs/rôles, certains exports/imports, les historiques partenaires, les profils d'impression avancés et plusieurs fonctions de confort analytique. L'impression Bluetooth demeure couverte par des tests logiciels mais doit encore être validée sur l'imprimante physique.

### Couverture calculée

Le tableau ci-dessous comporte **60 capacités atomiques**. La méthode de calcul est volontairement prudente :

- opérationnelle et vérifiée : 1 point ;
- partielle : 0,5 point ;
- présente mais non testée : 0,5 point ;
- absente ou bloquée par le backend : 0 point.

Résultat après remédiation : **44 points sur 60, soit 73,3 % de couverture fonctionnelle pondérée**.

Répartition Android : **38 opérationnelles**, **8 partielles**, **4 présentes mais non testées**, **10 absentes** et **0 bloquée par le backend**.

La couverture stricte, limitée aux capacités Android classées « opérationnelles et vérifiées », est de **38 sur 60, soit 63,3 %**. Ce second taux ne signifie pas que le reste est défectueux : il distingue simplement le code prouvé par les tests des parcours partiels, non testés ou absents.

## 2. Sources et méthode d'audit

L'audit ne déduit pas une fonctionnalité Android de la seule existence d'une route API. Pour chaque capacité, les éléments suivants ont été rapprochés :

- routes, vues, formulaires et services Django ;
- ViewSets, serializers, permissions et actions DRF ;
- routes Flutter, écrans, formulaires, modèles Dart, repositories et contrôleurs Riverpod ;
- tests Django/API et tests Flutter existants ;
- historique Git récent des deux dépôts.

Principales zones inspectées :

- Web/API : `apps/accounts`, `apps/api`, `apps/commerce`, `apps/core`, `apps/expenses`, `apps/inventory`, `apps/printing` ;
- Android : `lib/app`, `lib/core`, `lib/features` et `test` dans `appAlimGen-mobile`.

Légende Android :

- **Opérationnelle** : parcours UI, modèle/repository et backend cohérents, avec preuve automatisée pertinente ;
- **Partielle** : utilisable, mais fonctionnalité ou couverture de test incomplète ;
- **Absente** : aucun parcours Android utilisable ;
- **Présente non testée** : code UI/repository constaté, sans test ciblé suffisant ;
- **Bloquée backend** : le Web sait le faire, mais l'API mobile requise n'existe pas ou ne l'autorise pas.

## 3. Tableau des écarts

| Fonctionnalité | Web | Android | Écart | Priorité |
| --- | --- | --- | --- | --- |
| Connexion, renouvellement de jeton et déconnexion | Opérationnelle | **Opérationnelle** | Aucun écart fonctionnel constaté ; stockage sécurisé et refresh testés | P0 acquis |
| Restauration de session | Session Web | **Opérationnelle** | Fonction spécifique Android, testée avec expiration et changement d'utilisateur | P0 acquis |
| Biométrie | Non applicable au Web | **Opérationnelle** | Fonction Android conservée et testée | P2 acquis |
| Mot de passe oublié | Opérationnelle | **Opérationnelle** | Android demande le lien, puis la confirmation reste Web conformément à la V1 | P0 acquis |
| Changement du mot de passe connecté | Opérationnelle | **Opérationnelle** | Dialogue Android, validation backend et révocation de la session testés | P1 acquis |
| Permissions, masquage des actions et navigation | Opérationnelle | **Opérationnelle** | Contrôle local et refus backend testés | P0 acquis |
| Administration des utilisateurs | Opérationnelle | **Absente** | Aucun écran, modèle ou repository Android | P1 |
| Administration des rôles et refus explicites | Opérationnelle | **Absente** | Aucun contrat API ni écran Android | P1 |
| Profil utilisateur | Opérationnelle | **Partielle** | Android affiche l'identité, la biométrie, le changement de mot de passe et la déconnexion ; l'édition de l'identité reste Web | P2 |
| Tableau de bord : période et KPI principaux | Opérationnelle | **Opérationnelle** | Périodes, KPI, comparaisons et classements principaux testés | P1 acquis |
| Tableau de bord : graphiques et analyse complète | Opérationnelle | **Partielle** | API riche, mais Android n'affiche qu'un sous-ensemble des séries, classements et analyses | P2 |
| Tableau de bord : filtre par utilisateur | Opérationnelle si autorisé | **Absente** | Donnée API disponible, aucun sélecteur Android | P2 |
| Exports Excel/PDF du tableau de bord | Opérationnelle | **Absente** | Aucun parcours Android | P3 |
| Alertes détaillées | Opérationnelle | **Partielle** | Android affiche le compteur et des indicateurs, pas la liste complète `/alerts/` | P2 |
| Produits CRUD | Opérationnelle | **Opérationnelle** | CRUD et permissions présents ; repository testé | P0 acquis |
| Catégories modifiables avec réaffectation | Opérationnelle | **Présente non testée** | Création, renommage, suppression et réaffectation sont intégrées au formulaire produit, sans test Flutter ciblé | P1 |
| Modes pièce, carton et carton + pièce | Opérationnelle | **Opérationnelle** | Formulaire et contrat de conditionnement présents ; contrat carton testé | P0 acquis |
| Prix d'achat pièce et carton | Opérationnelle | **Opérationnelle** | Champs et sérialisation présents pour les deux conditionnements | P0 acquis |
| Trois niveaux de prix de vente pièce et carton | Opérationnelle | **Opérationnelle** | Super-gros, gros et détail présents dans les modèles et formulaires | P0 acquis |
| Stock initial uniquement en pièces ou uniquement en cartons | Opérationnelle | **Opérationnelle** | Conversion vers l'unité de base effectuée dans le formulaire Android | P0 acquis |
| Stock initial mixte cartons + pièces | Opérationnelle | **Opérationnelle** | Deux champs Android simultanés, conversion canonique contrôlée et contrat produit conservé | P0 acquis |
| Conversion des stocks historiques | Opérationnelle | **Opérationnelle** | Prévisualisation API, confirmation explicite, service atomique et écran Android testés | P0 acquis |
| Correction tracée du facteur après conversion | Opérationnelle | **Opérationnelle** | Prévisualisation, inventaire physique conditionnel, permission dédiée et correction auditée | P1 acquis |
| Codes-barres et QR automatiques | Opérationnelle | **Partielle** | Génération backend commune et affichage du code ; pas de téléchargement, impression d'étiquette ou scan Android | P2 |
| Import/export produits | Opérationnelle | **Absente** | Aucun parcours Android | P3 |
| Recherche intelligente des produits | Opérationnelle | **Opérationnelle** | Recherche API contextualisée par vente, achat et chargement utilisée par Android | P0 acquis |
| Clients CRUD et informations légales | Opérationnelle | **Opérationnelle** | Nom, contact, type, NIF, NIS, RC, AI et solde sont sérialisés et éditables | P0 acquis |
| Recherche intelligente des clients | Opérationnelle | **Opérationnelle** | Recherche paginée et sélection client dans la vente présentes | P0 acquis |
| Géolocalisation client | Opérationnelle | **Présente non testée** | Android utilise le GPS haute précision et le géocodage local ; pas de test Flutter ciblé ni test appareil dans cet audit | P1 |
| Historique des ventes d'un client | Opérationnelle | **Absente** | Endpoint `clients/{id}/history/` disponible, mais jamais consommé par Android | P2 |
| Export clients | Opérationnelle | **Absente** | Aucun parcours Android | P3 |
| Fournisseurs CRUD | Opérationnelle | **Présente non testée** | Écrans et repository CRUD présents, sans test Flutter ciblé | P1 |
| Historique des achats fournisseur | Opérationnelle | **Absente** | Endpoint disponible, mais aucun écran Android | P2 |
| Création d'achat mixte carton/pièce | Opérationnelle | **Opérationnelle** | Recherche, conditionnement, prix et lignes multiples présents | P0 acquis |
| Modification d'un achat | Opérationnelle | **Opérationnelle** | PATCH Android et service backend atomique remplacent les lignes et journalisent le stock | P0 acquis |
| Suppression/annulation d'un achat et restitution du stock | Opérationnelle | **Opérationnelle** | Suppression Android appelle le service backend transactionnel | P0 acquis |
| Vente CRUD mixte carton/pièce | Opérationnelle | **Opérationnelle** | Création, modification et suppression Android présentes ; mutations critiques testées | P0 acquis |
| Prix automatique client + conditionnement | Opérationnelle | **Opérationnelle** | Endpoint de prix consommé et conservation explicite d'un prix manuel | P0 acquis |
| Calcul instantané des ventes | Opérationnelle | **Opérationnelle** | Sous-total, remise, TVA, encaissé et reste calculés en centimes et testés | P0 acquis |
| Factures : liste, détail et statuts | Opérationnelle | **Opérationnelle** | Statuts payée, partielle et impayée testés dans l'UI | P0 acquis |
| Paiement initial non réglé, partiel ou complet | Opérationnelle | **Opérationnelle** | Les trois modes sont proposés à la création de vente | P0 acquis |
| Paiement complémentaire d'une facture | Opérationnelle | **Opérationnelle** | Création de paiement et mise à jour des soldes disponibles | P0 acquis |
| Paiement supérieur au solde d'une facture réparti sur les autres créances | Opérationnelle | **Opérationnelle** | Affectation FIFO transactionnelle sur les factures du client avec clé d'idempotence | P0 acquis |
| Créances clients | Opérationnelle | **Opérationnelle** | Liste, compte client, factures et soldes sont présents ; repository testé | P0 acquis |
| Créance antérieure saisie manuellement | Opérationnelle | **Opérationnelle** | Le solde initial du client est intégré au compte ; ses règlements sont immuables, auditables et annulables | P0 acquis |
| Historique et annulation des règlements clients | Opérationnelle | **Opérationnelle** | Historique du compte et annulation selon permission présents | P1 acquis |
| Relevé client imprimable | Opérationnelle | **Absente** | Aucun rendu/partage du relevé sur Android | P2 |
| Stock global | Opérationnelle | **Partielle** | Android affiche les soldes et le journal des mouvements ; alertes dédiées et régularisations manuelles restent Web | P1 |
| Stock opérateur | Opérationnelle | **Opérationnelle** | Soldes chargés, vendus et restants affichés ; permissions testées | P0 acquis |
| Bons de chargement | Opérationnelle | **Opérationnelle** | Création/édition, validation, annulation et clôture présentes ; actions et idempotence testées | P0 acquis |
| Retours et annulations | Partielle | **Partielle** | Retour du reliquat à la clôture et annulations transactionnelles présents ; aucun module autonome de retour partiel de vente | P1 |
| Impression Bluetooth | Non applicable côté serveur physique | **Présente non testée** | Connexion, erreurs et envoi sont testés avec doubles ; aucune preuve sur imprimante réelle pendant cet audit | **P0 terrain** |
| Tickets thermiques 58 mm et 80 mm | Opérationnelle | **Opérationnelle** | Largeurs, aperçu et construction ESC/POS testés | P0 acquis |
| Paramètre ON/OFF des informations du ticket | Opérationnelle | **Opérationnelle** | Le payload backend et le rendu Android minimal sont testés ; produits + total général uniquement en mode OFF | P0 acquis |
| Facture PDF A4 | Opérationnelle | **Opérationnelle** | Android télécharge et ouvre le PDF officiel du backend ; aperçu et mentions légales testés | P0 acquis |
| Gestion des imprimantes | Opérationnelle | **Opérationnelle** | CRUD, imprimante par défaut et ticket de test présents | P1 acquis |
| Profils d'impression avancés | Opérationnelle via API | **Absente** | Android gère les imprimantes, pas les `print-profiles` | P2 |
| Paramètres entreprise et informations légales | Opérationnelle | **Partielle** | Champs identité/NIF/NIS/RC/AI/TVA présents ; logo et options Web complémentaires absents | P1 |
| Dépenses CRUD | Opérationnelle | **Partielle** | CRUD principal et création rapide de catégorie présents ; justificatif, impression et exports restent absents | P1 |
| Internationalisation FR/AR/EN et RTL | Opérationnelle | **Opérationnelle** | Navigation et principaux écrans traduits ; RTL couvert par tests | P1 acquis |

## 4. Écarts prioritaires confirmés

### P0 — réalisés dans cette phase

1. **Stock initial mixte Android** : saisie cartons + pièces puis envoi de la quantité canonique.
2. **Conversion des stocks historiques** : simulation, confirmation atomique et correction tracée exposées dans l'API et Android.
3. **Modification d'achat** : remplacement transactionnel des lignes et ajustement journalisé du stock.
4. **Règlement multi-créances** : affectation FIFO jusqu'au montant total dû par le client.
5. **Créance antérieure** : intégration du solde initial et modèle de règlement séparé, immuable et annulable.

Le seul contrôle P0 restant hors automatisation est la **validation physique Bluetooth** : RPP02N en 58/80 mm, accents, arabe raster, reconnexion et coupe papier.

### P1 — parité de gestion

- administration utilisateurs/rôles si la gestion mobile est réellement souhaitée ;
- régularisations manuelles du stock global ;
- logo et paramètres entreprise complets ;
- pièce justificative, impression et exports des dépenses ;
- tests ciblés catégories, fournisseurs, géolocalisation et formulaires multi-unités.

### P2/P3 — confort, historique et export

- graphiques complets, filtre utilisateur et alertes détaillées du tableau de bord ;
- historiques client/fournisseur et relevé client ;
- scan/téléchargement des codes-barres et QR ;
- imports/exports produits, clients, dépenses et tableau de bord ;
- profils d'impression avancés.

## 5. Fonctionnalités Web supplémentaires inventoriées

Les fonctions suivantes ne figuraient pas toutes dans la liste initiale, mais existent côté Web :

- gestion complète des utilisateurs, rôles et refus explicites de permissions ;
- exports Excel/PDF du tableau de bord ;
- import/export produits et export clients ;
- téléchargement des QR et codes-barres ;
- historique des ventes par client et des achats par fournisseur ;
- journal des mouvements de stock et régularisation manuelle ;
- relevé client imprimable ;
- catégories de dépenses, justificatifs, impression et exports ;
- profils d'impression avancés ;
- filtre analytique du tableau de bord par utilisateur.

Ces fonctions sont incluses dans le tableau afin d'éviter un pourcentage artificiellement élevé limité au seul périmètre mobile existant.

## 6. Tests réalisés pendant l'audit

| Contrôle | Résultat | Portée réelle |
| --- | --- | --- |
| `python manage.py test --noinput --keepdb` | **PASS — 447 tests, 12 ignorés** | Django, API, services métier, permissions, ventes/achats, stocks, multi-unités, créances, impressions et sécurité avec la base de test configurée |
| Tests Django ciblés de la remédiation | **PASS — 18 tests, 1 ignoré** | Conversion/correction historique, permissions, modification d'achat, allocation FIFO, dette antérieure et annulation |
| `flutter test --suppress-analytics --concurrency=1` | **PASS — 138 tests** | Tests unitaires/widgets des contrats, session, permissions, dashboard, produits, ventes, achats, factures, chargements, créances, mouvements et impression simulée |
| `flutter analyze` | **PASS — aucune anomalie** | Analyse statique Dart/Flutter |
| `flutter build apk --debug` | **PASS** | APK généré dans `build/app/outputs/flutter-apk/app-debug.apk` |
| Émulateur Android | **NON EXÉCUTÉ** | Aucun émulateur/appareil disponible ou piloté pendant cet audit |
| Imprimante Bluetooth physique | **NON EXÉCUTÉ** | Les tests utilisent des transports simulés ; sortie papier non vérifiée |
| Intégration contre Render/production | **NON EXÉCUTÉE** | Audit volontairement local et non mutatif ; aucune donnée de production touchée |
| PostgreSQL 15 via Docker | **PASS — 11 tests ciblés** | PATCH vente avec/sans bon nullable, idempotence concurrente, chargements, conversion historique, paiements et ventes multi-unités |

Remarque : une suite verte prouve l'absence de régression couverte par les tests existants. Elle ne prouve pas les parcours sans tests ciblés ni le comportement matériel Bluetooth.

## 7. Risques de déploiement

- **Stock** : toute conversion historique ou modification d'achat mal conçue peut doubler, perdre ou désynchroniser les soldes dépôt/opérateur. Les calculs doivent rester exclusivement backend et atomiques.
- **Compatibilité Android** : tout nouveau champ API doit rester facultatif ou versionné jusqu'à la diffusion du nouvel APK.
- **Créances** : l'affectation d'un surpaiement et les écritures d'ouverture ont un impact comptable ; journal, annulation et idempotence sont obligatoires.
- **Permissions** : ajouter un bouton Android sans reproduire la permission backend créerait une UX incohérente, même si l'API refuse finalement l'action.
- **Impression** : succès de l'appel Bluetooth ne signifie pas sortie papier correcte ; encodage, largeur, raster arabe et firmware dépendent du matériel.
- **Médias** : logo, QR, codes-barres et justificatifs nécessitent une stratégie de stockage persistante et des validations d'upload.
- **Tests d'intégration** : les tests Flutter actuels simulent majoritairement l'API ; un environnement de recette Web/API/Android commun reste nécessaire.

## 8. Avancement par lots

### Lot 1 — Parité stock et produits (P0) — réalisé

- stock initial mixte cartons + pièces sur Android ;
- endpoints de simulation/confirmation de conversion historique ;
- correction tracée du conditionnement ;
- tests Flutter, Django et PostgreSQL 15 de non-régression stock réalisés.

### Lot 2 — Achats et stocks (P0/P1) — réalisé en partie

- service transactionnel de modification d'achat ;
- PATCH API puis formulaire Android ;
- consultation des mouvements de stock réalisée ; régularisation Android restante ;
- tests d'insuffisance, d'annulation et de concurrence PostgreSQL couverts.

### Lot 3 — Créances et paiements (P0) — réalisé

- spécification de l'affectation des surpaiements ;
- écritures de créance antérieure traçables ;
- historique et annulation Android réalisés ; relevé imprimable restant ;
- tests financiers avec arrondis et idempotence.

### Lot 4 — Administration et compléments métier (P1/P2) — en cours

- mot de passe connecté — réalisé ;
- paramètres entreprise complets et logo ;
- catégories de dépenses — réalisé ; justificatifs/impression/exports à faire ;
- historiques client/fournisseur ;
- utilisateurs/rôles si validés pour le mobile.

### Lot 5 — Exports, scan et validation terrain (P2/P3)

- exports et partages mobiles pertinents ;
- scan code-barres/QR ;
- tests d'intégration sur environnement de recette ;
- matrice d'essais sur appareils et imprimantes 58/80 mm.

## 9. Garde-fou pour les prochaines versions APK

Avant chaque nouvelle version Android :

1. comparer les routes Web, API et Flutter modifiées depuis le dernier tag ;
2. mettre à jour ce tableau et les contrats Dart ;
3. ajouter au moins un test API et un test Flutter par règle métier nouvelle ;
4. exécuter Django, Flutter test, Flutter analyze et les tests PostgreSQL ciblés ;
5. réaliser un parcours de recette Web/Android sur les mêmes données ;
6. tester l'impression physique si le code ticket ou Bluetooth a changé ;
7. ne publier l'APK qu'après validation de la matrice de parité.

## 10. État de livraison

Les lots P0 automatisables de stock, achat et créances sont implémentés et couverts par la non-régression locale, y compris les tests ciblés sur PostgreSQL 15. La parité ne peut pas être déclarée à 100 % tant que les écarts P1/P2/P3 du tableau ne sont pas développés et que les contrôles sur appareil Android et imprimante physique ne sont pas exécutés dans leurs environnements réels.

Ce rapport ne transforme pas une fonctionnalité non testée en fonctionnalité validée. Aucun déploiement Render ni aucune modification de données de production n'a été effectué pendant cette phase locale.
