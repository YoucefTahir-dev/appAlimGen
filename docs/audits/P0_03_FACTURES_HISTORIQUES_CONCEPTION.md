# P0-03 — Sécurisation des factures historiques : audit et conception

Date de l'audit : 12 octobre 2026
Dépôt audité : `gestio_stock`
Révision auditée : `main` — `697f6d8` (alignée sur `origin/main` au début de l'audit)
Périmètre : audit et conception uniquement ; aucune modification du modèle, des migrations, de l'API, des impressions ou de Flutter.

## Légende des constats

- **[CONFIRMÉ CODE]** : directement vérifié dans la révision auditée.
- **[À VÉRIFIER]** : exige une décision métier, un contrôle de données ou une preuve externe au dépôt.
- **[PROPOSITION]** : architecture recommandée ; elle n'est pas encore implémentée.

## 1. Diagnostic précis avec fichiers et lignes

### Conclusion principale

**[CONFIRMÉ CODE]** Le risque P0-03 est réel. Une facture ancienne est aujourd'hui reconstruite à partir d'un mélange de données déjà conservées lors de la vente et de données relationnelles actuelles. Après modification d'un client, d'un produit ou des paramètres de l'entreprise, le PDF, l'aperçu A4, les tickets et les réponses utilisées par Android peuvent présenter ces nouvelles valeurs sur une ancienne facture.

### Preuves principales

- `apps/commerce/models.py:251-307` : `Sale` conserve le numéro, le client lié, le total TTC, la remise, le taux de TVA, le mode de paiement et la date de création. Aucun état brouillon/émise/annulée et aucun snapshot entreprise/client n'existent.
- `apps/commerce/models.py:345-406` : `SaleLine` conserve quantité de stock, conditionnement (`packaging_name`, `packaging_factor`, `packaging_quantity`), prix unitaire et coût historique. Elle ne conserve ni désignation/référence/unité produit, ni total HT de ligne matérialisé.
- `apps/commerce/services.py:28-33` et `apps/commerce/services.py:175-199` : le numéro de facture est attribué pendant `create_sale()`, dans la même transaction que la vente et ses lignes.
- `apps/commerce/services.py:205-374` : une vente existante peut être modifiée ; client, lignes, remise, TVA, paiement et total peuvent changer. Les lignes sont remplacées et les mouvements de stock sont ajustés.
- `apps/commerce/views.py:222-251` : le Web possède aussi une modification de vente ; `apps/commerce/views.py:254-269` permet sa suppression lorsque les protections le permettent.
- `apps/commerce/admin.py:27-37` : seuls les numéros sont en lecture seule dans l'administration ; les autres données de vente ne sont pas figées.
- `apps/commerce/utils.py:139-171` : `build_invoice_context()` relit `CompanySettings.objects.first()`, `sale.client` et les produits des lignes au moment du rendu.
- `apps/commerce/utils.py:224-355` : le PDF relit l'entreprise, le client, les identifiants légaux et le nom produit actuels. La mention « Validée » est seulement un libellé de rendu, pas un état persistant.
- `apps/commerce/templates/commerce/sale_invoice_preview.html:46-132` : l'aperçu A4 utilise les mêmes objets actuels.
- `apps/commerce/templates/commerce/sale_ticket.html:164-226` : les tickets 58/80 mm utilisent les mêmes objets actuels.
- `apps/printing/services.py:114-178` : `invoice_print_data()` fournit à Android l'entreprise, le client et les produits actuels ; les paiements restent, à juste titre, dynamiques.
- `apps/api/serializers.py:282-304` : les lignes API imbriquent le produit actuel.
- `apps/api/serializers.py:364-424` : `client_details` et `company_details` sont construits depuis les fiches actuelles ; sous-totaux et TVA sont recalculés depuis les lignes ; les capacités exposent modification/suppression et déclarent `can_cancel=False`.
- `apps/api/views.py:340-381` : `/api/v1/sales/` accepte création, mise à jour et suppression.
- `apps/api/views.py:541-603` : le ViewSet facture est en lecture seule, avec PDF, ticket et données d'impression.
- `apps/commerce/migrations/0007_payment_tracking_and_sale_cost_snapshot.py:11-62` : une migration antérieure a figé le meilleur coût historique disponible, tout en reconnaissant qu'il ne s'agissait pas nécessairement du coût d'origine.
- `apps/commerce/migrations/0010_saleline_packaging_saleline_packaging_factor_and_more.py:7-45` : les conditionnements historiques existants ont déjà été snapshotés à partir de la meilleure donnée alors disponible.

### Réponses aux dix questions de l'audit

1. **Quand une vente devient-elle une facture émise ?**
   **[CONFIRMÉ CODE]** Il n'existe pas d'événement distinct d'émission. La vente est créée avec un numéro de facture et est immédiatement utilisée comme facture (`create_sale()`).

2. **Existe-t-il un statut brouillon ou finalisé ?**
   **[CONFIRMÉ CODE]** Non. Aucun champ d'état de facture n'existe. « Validée » est un texte d'affichage.

3. **Quand le numéro est-il attribué ?**
   **[CONFIRMÉ CODE]** Pendant la création, via une séquence annuelle verrouillée dans la transaction (`apps/commerce/services.py:28-33, 175-186`).

4. **Une facture émise peut-elle être modifiée ?**
   **[CONFIRMÉ CODE]** Oui, par le Web et l'API, sous réserve des permissions. Les changements peuvent affecter client, lignes, quantités, prix, remise, TVA, total et stock.

5. **Une facture peut-elle être annulée ?**
   **[CONFIRMÉ CODE]** Il n'existe pas d'annulation métier. Elle peut être supprimée ; la suppression restaure le stock et peut être bloquée par des paiements protégés. L'API annonce `can_cancel=False`.

6. **Quelles informations sont figées ?**
   **[CONFIRMÉ CODE]** Numéro facture, numéro ticket, date de création, remise globale, taux de TVA global, TTC, mode de paiement, quantités, prix unitaire facturé, coût unitaire, nom/facteur/quantité du conditionnement. Les paiements sont enregistrés séparément et ne constituent pas un snapshot de facture.

7. **Quelles informations sont dynamiques ?**
   **[CONFIRMÉ CODE]** Raison sociale, adresse, téléphone, e-mail, logo et identifiants légaux de l'entreprise ; nom, adresse, téléphone, type et identifiants légaux du client ; désignation, référence et unité du produit. Le HT et le montant de TVA sont recalculés à partir des lignes, de la remise et du taux.

8. **Quels formats utilisent ces informations ?**
   **[CONFIRMÉ CODE]** PDF serveur, aperçu/impression A4 Web, ticket HTML 58 mm, ticket HTML 80 mm, détail JSON et `print-data` utilisé pour l'impression Bluetooth Android. L'aperçu Android A4/58/80 utilise aussi le détail JSON.

9. **Quels endpoints Android sont concernés ?**
   **[CONFIRMÉ CODE]** `GET /api/v1/invoices/`, `GET /api/v1/invoices/{id}/`, `GET /api/v1/invoices/{id}/pdf/`, `GET /api/v1/invoices/{id}/ticket/?width=58|80`, `GET /api/v1/invoices/{id}/print-data/?paper_width=58|80&language=...`, ainsi que `GET/PATCH/PUT/DELETE /api/v1/sales/{id}/`. Les fiches `/clients/`, `/products/` et `/company-settings/` sont les sources actuellement relues.

10. **Quelles fonctionnalités risquent d'être affectées ?**
    **[CONFIRMÉ CODE]** Création/modification/suppression de vente, ajustement du stock, vente opérateur et bon de chargement, permissions/capacités, paiements, liste et détail factures, PDF, A4, tickets 58/80, QR, impression Bluetooth, traductions FR/AR/RTL, administration Django et tests de concurrence. **[À VÉRIFIER]** La politique comptable attendue pour corrections, avoirs et annulations déterminera l'impact UX final.

### Champs : source actuelle et future source historique

| Bloc / champ affiché | Source actuelle | Conservation actuelle | Future source recommandée |
|---|---|---:|---|
| Numéro facture / ticket | `Sale` | Oui | `Sale`, immuable après émission |
| Date d'émission | `Sale.created_at` | Oui, mais nommée création | `Sale.issued_at`, explicite et immuable |
| État « Validée » | Texte statique | Non | `Sale.invoice_status` explicite |
| Entreprise : nom/adresse/téléphone/e-mail | `CompanySettings` actuel | Non | Snapshot sur la facture |
| Entreprise : NIF/NIS/RC/AI | `CompanySettings` actuel | Non | Snapshot sur la facture |
| Logo | Fichier actuel de `CompanySettings` | Non | Référence d'archive immuable ou règle de marque courante à décider |
| Client : nom/adresse/téléphone | `Client` actuel | Non | Snapshot sur la facture |
| Client : NIF/NIS/RC/AI | `Client` actuel | Non | Snapshot sur la facture |
| Type client | `Client` actuel, affiché Android | Non | Snapshot seulement s'il doit rester sur le document final |
| Produit : désignation/référence/unité | `Product`/`Unit` actuels | Non | Snapshot par ligne |
| Conditionnement | `SaleLine.packaging_name/factor` | Oui | Conserver les champs existants |
| Quantité vendue | `SaleLine.packaging_quantity` | Oui | Conserver ; immuable après émission |
| Quantité de stock | `SaleLine.quantity` | Oui | Conserver pour audit stock, distincte de la quantité facturée |
| Prix unitaire facturé | `SaleLine.unit_price` | Oui, décimal | Conserver ; immuable après émission |
| Remise de ligne | Absente du modèle | Sans objet | Ne pas ajouter tant qu'aucune remise par ligne n'existe |
| Remise totale | `Sale.discount` | Oui, décimal | Conserver ; immuable après émission |
| Taux de TVA | `Sale.tax_rate` global | Oui, décimal | Conserver ; immuable après émission |
| Total HT de ligne | Calcul quantité × prix | Déterministe mais non matérialisé | Snapshot décimal par ligne recommandé |
| HT facture / montant TVA | Recalculés | Non | Snapshots décimaux sur la facture |
| TTC facture | `Sale.total` | Oui, décimal | Conserver ; immuable après émission |
| Devise | Chaîne d'affichage `DZD` | Non | Code ISO `DZD` snapshoté |
| Paiements / payé / restant / statut paiement | `Payment` actuel | Historique séparé | Rester dynamiques et séparés du snapshot fiscal |

## 2. Workflow actuel de facturation

1. **[CONFIRMÉ CODE]** Le Web ou Android envoie client, lignes, remise, TVA, mode de paiement et éventuellement paiement intégral.
2. **[CONFIRMÉ CODE]** `create_sale()` ouvre une transaction atomique, verrouille les ressources de stock nécessaires, valide les quantités et calcule avec `Decimal`.
3. **[CONFIRMÉ CODE]** La séquence annuelle est verrouillée et fournit immédiatement le numéro de facture ; une séquence distincte fournit le ticket.
4. **[CONFIRMÉ CODE]** `Sale` et ses `SaleLine` sont créées, le stock est décrémenté et un paiement peut être créé.
5. **[CONFIRMÉ CODE]** Aucun passage brouillon → émission n'a lieu : le document est immédiatement imprimable et affiché comme validé.
6. **[CONFIRMÉ CODE]** Plus tard, la vente peut être éditée. Les lignes sont supprimées/recréées et les stocks sont compensés, puis les impressions reflètent le nouvel état et les référentiels actuels.
7. **[CONFIRMÉ CODE]** Une suppression peut annuler physiquement la vente et restaurer le stock ; ce n'est pas une annulation comptable traçable.

Ce workflow rend impossible la promesse « une facture émise ne change jamais » sans décider ce qui remplace l'édition et la suppression actuelles.

## 3. Liste des champs historiques à figer

### Facture

**[PROPOSITION]** Conserver ou ajouter :

- `invoice_number` et `ticket_number` existants ;
- `issued_at` (`DateTimeField`) ;
- `invoice_status` (`issued`, puis éventuellement `cancelled`; ne pas introduire un brouillon sans besoin validé) ;
- `snapshot_version` (`PositiveSmallIntegerField`) ;
- `snapshot_integrity_status` (`complete`, `legacy_partial`, `legacy_unknown`, `manually_verified`) ;
- `subtotal_ht_snapshot`, `tax_amount_snapshot`, `discount` existant, `tax_rate` existant, `total` existant, tous en `DecimalField` ;
- `currency_snapshot`, valeur `DZD` ;
- provenance/horodatage de validation manuelle, si une reprise manuelle est autorisée.

### Entreprise

**[PROPOSITION]** Nom, adresse, téléphone, e-mail, NIF, NIS, RC et AI sous forme de champs texte nullable sur `Sale`. L'e-mail est inclus car il est réellement affiché sur l'A4 Android et le PDF/Web peut l'utiliser. Le nom arabe actuellement constant doit soit devenir un champ officiel snapshoté, soit rester une traduction de présentation ; décision requise.

**[À VÉRIFIER]** Le logo demande une règle explicite : conserver l'image de marque actuelle à la réimpression, ou archiver une copie immuable du logo utilisé. Pour une fidélité documentaire complète, une copie/clé d'objet immuable est préférable à une simple référence vers le logo modifiable.

### Client

**[PROPOSITION]** Nom/raison sociale, adresse, téléphone, NIF, NIS, RC et AI sur `Sale`. Garder le FK `client` pour navigation et recherche, mais ne plus l'utiliser comme source documentaire après émission. L'e-mail client n'est actuellement pas affiché et n'a donc pas à être ajouté sans nouveau besoin.

### Lignes produit

**[PROPOSITION]** Ajouter à `SaleLine` : désignation, référence et unité snapshotées, ainsi que `line_total_ht_snapshot` en `DecimalField`. Réutiliser les snapshots de conditionnement et les quantités/prix existants. Ne pas ajouter de remise ni TVA par ligne tant que le modèle métier reste global à la facture.

### Paiements

**[CONFIRMÉ CODE]** Ils sont séparés de `Sale` et peuvent survenir après émission.
**[PROPOSITION]** Ne jamais les copier dans le snapshot fiscal. `amount_paid`, `balance_due` et `payment_status` doivent continuer à refléter le suivi de paiement réel, avec une mention claire qu'ils ne font pas partie des données figées à l'émission.

## 4. Architecture de snapshots proposée

### Choix recommandé

**[PROPOSITION]** Utiliser des colonnes typées, explicites et additives sur `Sale` et `SaleLine`, plutôt qu'un unique `JSONField` opaque.

Avantages : validations Django/PostgreSQL, types décimaux sûrs, requêtes/audits simples, évolution contrôlée de l'API et absence de dépendance au schéma d'un JSON historique. Un `snapshot_version` permet d'évoluer sans réinterpréter les anciennes données.

### Service unique d'émission

**[PROPOSITION]** Créer ultérieurement un service d'émission atomique qui :

1. verrouille la vente concernée ;
2. charge et, si nécessaire, verrouille explicitement entreprise, client, produits et conditionnements sans jointure nullable verrouillée ;
3. calcule une seule fois les montants avec `Decimal` et la règle d'arrondi existante ;
4. copie toutes les valeurs documentaires ;
5. attribue le numéro et `issued_at` ;
6. marque le snapshot `complete` et la facture `issued` ;
7. interdit toute seconde émission ou réécriture des snapshots.

L'opération doit rester dans la transaction de création actuelle afin de ne pas créer de facture numérotée sans lignes, ou de vente/stock sans facture cohérente.

### Immutabilité

**[PROPOSITION]** Après émission :

- interdire la modification du client, des lignes, des quantités, prix, TVA, remise, totaux, devise et snapshots ;
- autoriser uniquement les opérations non fiscales prévues explicitement, notamment l'ajout de paiements ;
- remplacer la suppression par un futur processus d'annulation/avoir traçable ;
- protéger au niveau service/API/admin, puis ajouter des contraintes PostgreSQL adaptées lorsque le workflow aura été validé.

Un simple `save()` sur `Sale` ne suffit pas comme frontière : les mutations actuelles passent aussi par lignes, ViewSets, formulaires et administration.

### Lecture documentaire

**[PROPOSITION]** Introduire un `InvoiceDocumentBuilder` unique. PDF, A4, tickets Web, API facture, `print-data` et aperçu Android doivent consommer le même objet historique. Pour une facture `complete`, aucune valeur documentaire ne doit provenir du `Client`, `Product`, `Unit` ou `CompanySettings` actuels.

Pour une ancienne facture incomplète, le builder doit renvoyer un statut de provenance et des champs absents explicites ; il ne doit jamais substituer silencieusement la valeur actuelle.

## 5. Stratégie pour les anciennes factures

### Classification des données

| Catégorie | Données | Traitement proposé |
|---|---|---|
| Historiques déjà conservées | numéros, date de création, remise, taux TVA, TTC, mode de paiement, quantités, prix, coût, conditionnement | Copier/laisser telles quelles ; ne jamais recalculer ni renuméroter |
| Déductibles de façon déterministe | HT de ligne depuis quantité facturée et prix figés ; HT/TVA seulement si la règle d'arrondi historique est prouvée | Calcul contrôlé hors migration de schéma, avec preuve et journal ; sinon laisser inconnu |
| Récupérables depuis document archivé | identité entreprise/client, légaux, produit/unité si un PDF/ticket original fiable existe | Import dédié, checksum/source/document, revue manuelle |
| Inconnues | anciennes adresses, anciens identifiants légaux, ancien nom produit/entreprise sans archive | `NULL` + `legacy_unknown`; ne pas utiliser la valeur courante comme vérité historique |
| À validation manuelle | valeurs proposées par archives, sauvegardes ou opérateur | Interface/admin restreint, justification, auteur, date et journal d'audit |

**[CONFIRMÉ CODE]** Le dépôt ne contient pas de modèle d'archive immuable des PDF émis.
**[À VÉRIFIER]** Il faut inventorier hors code les PDF papier/électroniques, sauvegardes média et exports comptables réellement conservés.

### Présentation des documents anciens

**[PROPOSITION]** Une facture legacy partielle doit porter un avertissement non ambigu, par exemple « Données historiques d'identité non certifiées / indisponibles », visible selon le contexte administratif retenu. Les champs manquants restent vides ou marqués indisponibles. Un écran peut proposer séparément les données actuelles à titre informatif, mais elles ne doivent pas être incorporées au document historique officiel sans libellé explicite.

### Interdictions

- Ne pas copier automatiquement `Client`, `Product` ou `CompanySettings` actuels dans des snapshots prétendument historiques.
- Ne pas modifier numéros, dates, montants, stocks ou paiements pendant la reprise.
- Ne pas traiter le coût copié par la migration `0007` comme une preuve absolue du coût d'origine : le commentaire de migration documente sa limite.

## 6. Stratégie de migration PostgreSQL

### État du mécanisme Render

**[CONFIRMÉ CODE]** `build.sh:4-20` installe, collecte les statiques, effectue l'audit e-mail P0-02 puis lance `python manage.py migrate --noinput` pendant le build, sous timeout. Render gratuit ne fournit pas ici de commande pre-deploy (`build.sh:7-11`). Une migration longue ou ambiguë peut donc faire échouer le build ; le service précédent reste normalement servi, mais le schéma peut déjà avoir été partiellement avancé par des migrations atomiques distinctes.

### Phases recommandées

1. **Migration de schéma additive** : colonnes nullable, sans défaut calculé, index minimaux, aucune lecture de référentiel, aucun mouvement métier. Sur PostgreSQL, éviter les défauts réécrivant toute la table et vérifier le coût de chaque contrainte.
2. **Déploiement compatible en double lecture/écriture** : nouveau code écrit des snapshots complets pour toute nouvelle émission ; anciennes réponses API conservent leurs clés. Les documents choisissent le snapshot uniquement lorsqu'il est déclaré complet.
3. **Audit legacy en lecture seule** : commande produisant compteurs/CSV anonymisable par statut et champ manquant. Aucun changement de données.
4. **Reprise séparée et idempotente** : uniquement après validation, par lots bornés, journalisée, reprise possible, jamais implicitement dans `build.sh`. Elle copie seulement les données dont la provenance est démontrée.
5. **Contraintes finales** : après contrôle de complétude des nouvelles factures, renforcer les contraintes applicatives puis PostgreSQL sans bloquer les lignes legacy explicitement classées.

### Compatibilité des migrations existantes

**[CONFIRMÉ CODE]** La migration `0006` citée dans la mission appartient à l'application `accounts`; côté `commerce`, la branche auditée va jusqu'à `0012_sale_loading_order.py`. La nouvelle migration devra dépendre du dernier nœud `commerce` réel, pas de `accounts.0006`, tout en restant compatible avec celui-ci au niveau déploiement global.

### Rollback de schéma

**[PROPOSITION]** Le rollback applicatif doit précéder toute suppression de colonnes : l'ancienne version ignore les nouvelles colonnes, ce qui rend le retour de code possible tant que l'API garde ses champs historiques. Ne pas inverser une migration de données legacy en production. Conserver les snapshots créés ; une suppression physique ne serait envisagée qu'après sauvegarde, preuve de non-utilisation et nouvelle autorisation.

## 7. Impact sur les endpoints API

### Évolution additive proposée

**[PROPOSITION]** Garder les routes et clés existantes, puis ajouter au détail vente/facture :

- `invoice_status`, `issued_at`, `currency` ;
- `snapshot_version`, `snapshot_integrity_status` ;
- un objet `historical_document` structuré (`company`, `client`, `totals`, provenance) ;
- par ligne : `product_name_snapshot`, `product_reference_snapshot`, `unit_name_snapshot`, `line_total_ht_snapshot`.

Dans une phase transitoire, `client_details`, `company_details` et `lines[].product` restent présents. Pour une facture complète, leurs valeurs d'affichage peuvent être alimentées par le snapshot tout en conservant IDs et forme JSON ; ceci protège les anciennes applications Android. Le nouvel objet explicite permet aux versions récentes de distinguer données historiques et référentiels.

### Mutations

**[PROPOSITION]** Après décision métier, `PATCH/PUT/DELETE /api/v1/sales/{id}/` doit refuser une facture émise avec un code métier stable (par exemple HTTP 409), sans transformer une erreur de règle en 500. Les capacités doivent alors annoncer `can_update=false`, `can_delete=false`; `can_cancel` ne devient vrai que lorsqu'un vrai processus d'annulation est implémenté.

**[À VÉRIFIER]** Il faut décider si une vente peut rester brouillon et si l'API actuelle de création doit continuer à émettre immédiatement. Le choix recommandé pour compatibilité V1 est : création actuelle = émission immédiate ; une éventuelle API de brouillon serait additive plus tard.

## 8. Impact sur Flutter

**[CONFIRMÉ CODE]** Flutter utilise :

- `InvoicesRepository` pour `invoices/`, `invoices/{id}/`, `pdf/` et `print-data/` (`business_repositories.dart:26-59`) ;
- `SalesRepository` pour création, `PATCH` et suppression (`business_repositories.dart:80-117`) ;
- `SaleDetails` pour `client_details`, `company_details`, lignes, montants, paiements et capacités (`business_entities.dart:286-360`) ;
- `InvoiceDocumentData` pour l'aperçu 58/80/A4 (`invoice_document.dart:60-79`) ;
- l'impression locale Bluetooth par `print-data`, ou par raster en arabe/RTL (`invoice_preview_screen.dart:99-129, 186-214`).

**[PROPOSITION]** Une première version serveur peut rester compatible sans mise à jour Flutter si les clés existantes demeurent et deviennent historiquement correctes. Une version Flutter ultérieure doit :

- lire les nouveaux champs de snapshot de manière optionnelle ;
- afficher l'intégrité/provenance des factures legacy ;
- masquer édition/suppression selon les capacités serveur ;
- conserver les fallbacks de parsing pour les serveurs plus anciens ;
- ne jamais recalculer les montants en `double` lorsqu'une valeur décimale officielle est fournie. Le fallback actuel de total de ligne utilise `double` (`business_entities.dart:232-234`) et devra être supprimé au profit du total serveur snapshoté.

**[CONFIRMÉ CODE]** Les traductions FR/AR et le RTL existent dans les écrans facture.
**[PROPOSITION]** Toute nouvelle mention d'intégrité, d'annulation ou d'indisponibilité doit recevoir simultanément ses traductions française, arabe et anglaise, avec tests RTL.

## 9. Impact sur les impressions

| Sortie | État actuel confirmé | Changement futur |
|---|---|---|
| PDF serveur | relit référentiels actuels | consommer uniquement `InvoiceDocumentBuilder` historique |
| A4 Web | relit contexte courant | même source historique que le PDF |
| Ticket Web 58/80 | relit contexte courant | même source historique ; préserver largeur/encodage/RTL |
| API `print-data` | entreprise/client/produit courants | mêmes clés, valeurs issues du snapshot |
| Aperçu Android 58/80/A4 | détail API courant | objet historique commun ; aucune divergence avec PDF |
| Bluetooth latin | `print-data` serveur | conserver contrat et largeurs 58/80 |
| Bluetooth arabe | rasterise l'aperçu Flutter | s'assurer que l'aperçu est alimenté par snapshots |
| QR PDF | nom client et total reconstruits | nom client et total historiques ; définir/versionner le payload |

**[PROPOSITION]** Ajouter des tests « golden »/extraction de texte comparant PDF, A4, 58 et 80 pour la même facture. La source unique est essentielle : corriger uniquement le PDF laisserait Android et les tickets incohérents.

## 10. Plan de tests

Tous les tests de transaction/concurrence/migration devront être exécutés sur PostgreSQL isolé, jamais sur Neon production. SQLite peut compléter les tests rapides, mais ne valide ni verrous ni comportement de migration PostgreSQL.

1. Création de vente : numéros, stock et snapshots complets sont atomiques.
2. Émission : `issued_at`, état, version et intégrité sont fixés une seule fois.
3. Changement de nom client après émission : toutes les sorties gardent l'ancien nom.
4. Changement d'adresse client : toutes les sorties gardent l'ancienne adresse.
5. Changement NIF/NIS/RC/AI entreprise et client : ancienne facture inchangée.
6. Changement de désignation/référence/unité produit : lignes historiques inchangées.
7. Changement de prix catalogue : prix facturé et total inchangés.
8. Réimpression après tous les changements : PDF/A4/58/80/API identiques sur les champs figés.
9. Cohérence décimale : somme HT, remise, TVA et TTC suivent la règle d'arrondi définie, sans float.
10. TVA/remise : taux zéro/non zéro, remise zéro/non zéro, limites et arrondis.
11. Impression Web : aperçu et PDF lisibles, mêmes données et statut legacy visible.
12. Android : détail, raster arabe, Bluetooth latin, 58/80/A4 et erreurs imprimante.
13. API : ancien contrat accepté, nouveaux champs additifs, permissions et refus de mutation stable.
14. Migration legacy : numéros/dates/montants inchangés, classification correcte et reprise idempotente.
15. Informations inconnues : aucune substitution par valeur actuelle ; avertissement correct FR/AR/EN.
16. Paiements partiels postérieurs : snapshot inchangé, payé/reste/statut évoluent correctement.
17. Annulation : à écrire après décision ; traçabilité, absence de suppression et règle stock validée.
18. Migration sans stock : comparer agrégats/lignes de stock avant/après, aucune écriture stock.
19. Migration sans paiement : checksum/compteurs/montants/références identiques avant/après.
20. Concurrence PostgreSQL : émission simultanée, séquence unique, un seul snapshot, ordre de verrouillage sans deadlock.

Tests complémentaires indispensables : administration Django, vente opérateur avec bon de chargement, rollback sur snapshot incomplet, logo absent, champs légaux vides, caractères arabes, timezone, gros volume de lignes et génération répétée byte/logiquement stable.

## 11. Plan de déploiement et rollback

### Déploiement progressif

1. Valider les décisions de la section 13 et le dictionnaire exact des champs.
2. Sauvegarde logique vérifiée et restauration testée hors production.
3. Exécuter la suite complète sur PostgreSQL isolé, dont migrations avant/arrière de schéma et tests de concurrence.
4. Déployer d'abord la migration additive nullable, courte et sans données métier.
5. Déployer le code dual-compatible qui écrit les snapshots des nouvelles factures, avec métriques de complétude.
6. Vérifier `/healthz/`, `/readyz/`, création contrôlée en environnement de préproduction, PDF/A4/58/80 et Android ancien/récent.
7. Activer la lecture par snapshots complets via un drapeau serveur réversible.
8. Lancer seulement ensuite l'audit legacy en lecture seule.
9. Soumettre un plan de reprise chiffré avant toute écriture legacy.
10. Renforcer l'immutabilité après observation et validation métier.

### Rollback

- Désactiver le drapeau de lecture snapshot si un problème de rendu survient, sans effacer les snapshots.
- Revenir au code précédent, compatible avec les colonnes additives.
- Ne jamais restaurer la base ou inverser une reprise legacy sans procédure dédiée et rapprochement.
- En cas d'échec de build Render, vérifier l'état exact des migrations avant une nouvelle tentative ; ne pas supposer qu'aucune migration n'a été appliquée.

**[À VÉRIFIER]** La disponibilité d'un environnement de préproduction représentatif, d'une sauvegarde restaurable et du mécanisme opérationnel de lancement d'une reprise par lots doit être confirmée avant implémentation.

## 12. Risques de régression

| Risque | Niveau | Réduction proposée |
|---|---:|---|
| Bloquer l'édition actuelle sans solution de correction | Élevé | décision métier préalable ; avoir/annulation ou brouillon |
| Divergence PDF/Web/Android | Élevé | builder documentaire unique + tests croisés |
| Ancien Android cassé par contrat API | Élevé | évolution additive, clés/types/routes conservés |
| Fausse donnée historique par backfill courant | Critique | statut de provenance, `NULL`, import d'archives vérifiées uniquement |
| Recalcul/arrondi changeant les totaux anciens | Critique | ne pas recalculer ; conserver les décimaux existants |
| Mouvement de stock lors de migration | Critique | migration de schéma pure ; assertions avant/après |
| Paiements modifiés ou confondus avec snapshot | Critique | modèles/services séparés ; tests checksum |
| Migration longue pendant build Render | Élevé | colonnes nullable sans data migration ; reprise hors build |
| Course lors de l'émission | Élevé | transaction atomique, verrous explicites et test PostgreSQL |
| Logo historique perdu | Moyen/élevé | règle d'archivage immuable à décider |
| Données legacy incomplètes mal comprises | Élevé | avertissement et statut d'intégrité visibles |
| Suppression d'une facture émise | Critique | remplacer par processus traçable après décision |
| Usage de `double` dans Flutter | Moyen | montant de ligne officiel fourni en chaîne décimale |
| Administration contourne les services | Élevé | champs readonly, actions dédiées, tests admin et garde DB |

## 13. Décisions métier nécessitant accord

1. **Point d'émission.** Recommandation : conserver pour la V1 « création = émission immédiate » afin de ne pas casser Web/Android. Souhaitez-vous plus tard un vrai brouillon ?
2. **Immutabilité.** Confirmez-vous qu'après émission client, lignes, prix, remise, TVA et totaux deviennent non modifiables, y compris pour un administrateur ?
3. **Correction d'erreur.** Faut-il mettre en place un avoir/facture corrective, une annulation avec motif, ou les deux ?
4. **Suppression.** Confirmez-vous l'interdiction de supprimer une facture émise ?
5. **Stock lors d'une annulation.** L'annulation doit-elle restituer automatiquement le stock, ou le mouvement physique doit-il être géré séparément selon retour réel ?
6. **Numérotation annulée.** Recommandation : conserver définitivement le numéro et marquer le document annulé, sans réutilisation. Accord requis.
7. **Factures legacy.** Quel libellé officiel doit apparaître quand les identités historiques sont inconnues ?
8. **Archives.** Existe-t-il des PDF, copies papier numérisées, exports comptables ou sauvegardes permettant une reprise certifiée ?
9. **Validation manuelle.** Qui est autorisé à compléter une ancienne facture, avec quelle preuve et quel journal d'audit ?
10. **Logo.** Une réimpression doit-elle conserver le logo d'origine ou utiliser le logo actuel ?
11. **Nom arabe de l'entreprise.** Doit-il devenir une donnée d'entreprise configurable et snapshotée, ou rester un libellé de présentation ?
12. **Type client.** Doit-il être considéré comme une mention historique officielle puisqu'Android l'affiche actuellement ?
13. **Affichage des paiements.** Confirmez-vous que payé/reste/statut doivent rester actuels sur l'écran, tout en étant clairement séparés du contenu fiscal émis ?
14. **Migration opérationnelle.** Quel mécanisme autorisé remplacera un shell pre-deploy indisponible sur Render gratuit pour une future reprise par lots ?

## Conclusion et arrêt P0-03 conception

**[CONFIRMÉ CODE] Cause racine : OUI.** La facture n'est pas un document autonome : elle relit plusieurs référentiels modifiables.
**[PROPOSITION]** La correction sûre consiste à figer, dans la transaction d'émission, des colonnes historiques typées sur `Sale` et `SaleLine`, puis à faire consommer une source documentaire unique par Web, API et Android. Les anciennes factures doivent être classées par niveau de preuve et ne jamais recevoir silencieusement les valeurs actuelles.

Aucune implémentation, migration, modification de données, exécution sur Neon, modification Flutter, opération Render, commit ou push n'a été effectué dans cette phase. L'implémentation P0-03 doit attendre l'accord explicite sur les décisions de la section 13.
