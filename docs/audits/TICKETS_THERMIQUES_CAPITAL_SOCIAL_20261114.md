# Tickets thermiques simplifiés et capital social

Rapport local du 14 novembre 2026. Aucune publication effectuée.

## Résultat et périmètre

Les corrections sont réalisées et testées localement. Elles concernent les tickets 58/80 mm et le capital social des factures officielles. Aucun calcul de vente, TVA, remise, règlement ou stock n'a été modifié. La passe PostgreSQL étendue présente une erreur d'outillage de sauvegarde hors lot, détaillée ci-dessous ; elle n'est pas déclarée entièrement réussie.

Aucun commit, push, déploiement Render, accès à Neon, changement de droits des comptes SQL/utilisateurs réels, modification de données réelles ou modification des sauvegardes P0-A. Aucun nouvel APK n'a été construit pour cette mission. L'APK fourni précédemment ne contient donc pas ces nouvelles corrections.

Les modifications préexistantes ont été conservées. Les empreintes de 19 fichiers hors périmètre déjà modifiés au départ restent identiques, notamment sauvegardes/workflows P0-A, paramètres/démarrage, travail Android de stock et transport Bluetooth. Les fichiers d'impression qui se recoupent avec le travail antérieur ont été étendus, pas réinitialisés. Les dépendances applicatives et leurs fichiers de verrouillage n'ont pas changé.

## 1. Composants et comportement des tickets

| Chemin | Composant utilisé |
| --- | --- |
| Web et API HTML, 58/80 mm | `commerce/sale_ticket.html`, contexte historique commun |
| Données d'impression API | `apps.printing.services.invoice_print_data` |
| Android, aperçu | `InvoiceDocumentView` et `InvoicePaperSpec` |
| Android, impression Bluetooth | Capture du même widget, `rasterTicket`, bandes ESC/POS, transport existant |
| Windows | Wrapper Tauri du Web ; même HTML backend, pas de moteur facture indépendant |
| Facture PDF officielle | Générateur ReportLab backend, également utilisé par Android pour ouvrir le PDF |

### Option OFF

- En-tête : uniquement le logo enregistré puis le nom enregistré, centrés. Pour les factures historisées, leur source est le snapshot du document, pas une nouvelle lecture de la fiche actuelle.
- Aucun logo ni nom de remplacement codé en dur dans ce mode. Logo absent/inaccessible : omission sans image de remplacement inventée. Sur Android, attente du décodage, limitée à huit secondes, image décodée bornée à 256 × 256 pixels ; aucun jeton API envoyé au serveur de médias.
- Suppression des coordonnées et identifiants entreprise, du capital, des numéros de facture/ticket, date/heure, caisse, informations client, QR, paiement et mentions complémentaires.
- Conservation des produits dans leur ordre, quantités, unités/conditionnements, prix unitaires, totaux des lignes et total général réellement calculé par le serveur. Les remises/taxes ne sont pas recalculées ni changées : seul leur détail est masqué.
- Payload thermique société limité à `name_fr` et `logo_url`. Pas de données légales/client réintroduites dans le payload minimal.
- Retour à la ligne des noms longs, séparation des colonnes, grandes valeurs conservées sans ellipses. Pas d'espace d'en-tête réservé si nom et logo manquent.

### Option ON

Les informations et blocs complets existants sont conservés. Le nouveau capital n'est pas ajouté arbitrairement au ticket thermique complet : son ajout demandé concerne les factures officielles. Les espacements de colonnes ont été sécurisés sans suppression de contenu.

### Cohérence aperçu / impression Android

Tous les tickets Android, français/anglais/arabe, OFF ou ON, passent désormais par le raster du même aperçu. La vérification serveur d'autorisation d'imprimer est effectuée avant l'envoi ; ses données ne remplacent pas le snapshot affiché. L'impression attend aussi le logo embarqué existant du mode ON avant capture.

Le pilote natif texte ON reste disponible pour compatibilité et tests. Un appel texte natif OFF est explicitement refusé : ce chemin ne sait pas incorporer le logo et ne doit pas produire silencieusement un ticket différent. L'écran d'impression utilise le chemin raster pris en charge. Fragmentation, ordre des bandes, pauses, gestion des erreurs et sérialisation Bluetooth préexistants sont conservés.

Les factures officielles restent complètes même lorsque l'option thermique est OFF.

## 2. Capital social : stockage et validation

- `CompanySettings.share_capital` : décimal facultatif, 14 chiffres au total dont deux décimales, nullable, sans montant par défaut, minimum zéro. Devise DZD comme le reste du document.
- Champ de gestion Web et Android, soumis aux permissions d'édition déjà existantes. Aucun droit supplémentaire accordé.
- Validation formulaire/modèle/API : négatifs, valeurs non finies, précision excessive et dépassement de capacité refusés. Le formulaire Android accepte aussi la virgule décimale ; la capacité maximale est confirmée par le serveur.
- API : chaîne décimale en réponse ; omission en PATCH conserve la valeur ; `null` ou chaîne vide efface la valeur selon le comportement standard DRF. Le mobile envoie `null` lorsqu'il est vide.
- Zéro est une valeur connue affichable, pas une absence. Aucune valeur illustrative n'est utilisée comme capital réel.
- Factures officielles HTML, PDF et aperçu A4 Android : mention conditionnelle dans les coordonnées légales. Aucun libellé vide lorsque la valeur est inconnue. Chaque rendu utilise sa convention monétaire existante, à deux décimales.
- Windows reçoit le même HTML officiel ; le PDF ouvert depuis Android est celui du backend.

## 3. Documents anciens et choix confirmé

Le mécanisme d'instantanés existait déjà. Il est étendu par `Sale.company_share_capital_snapshot`, nullable, sans reprise des anciennes lignes et sans requalification de leur version ou statut.

Règle confirmée par le propriétaire : **lors d'une modification volontaire de la facture, le capital reprend les paramètres du moment, comme les autres coordonnées**. Cela couvre `update_sale` et `refresh_invoice_snapshot`. Une simple consultation, réimpression, modification de la fiche entreprise ou réception d'un paiement ne réécrit pas ce capital.

- Anciennes factures certifiées non modifiées : capital inconnu conservé, pas de valeur actuelle injectée.
- Factures anciennes `legacy_unknown` non modifiées : aucun capital courant ajouté. Leur avertissement historique reste présent.
- Après modification volontaire d'une facture legacy : seul le capital explicitement capturé dans sa colonne snapshot est affiché, jamais le capital lu en direct. Il reste stable si la fiche change ensuite. Le document reste non certifié, avec son avertissement et sa version antérieure.
- Une nouvelle émission sans fiche entreprise ne mémorise plus de nom inventé pour le ticket OFF. Les valeurs déjà enregistrées dans les anciens snapshots ne sont pas réécrites ni devinées.

Limite préexistante : le snapshot du logo conserve son chemin média, pas une copie immuable des octets. La conservation et la disponibilité de ce média restent nécessaires. Aucun accès aux médias réels n'a été effectué pour cette mission.

## 4. Migrations préparées

1. `apps/core/migrations/0006_companysettings_share_capital.py`
2. `apps/commerce/migrations/0018_sale_company_share_capital_snapshot.py`

Deux ajouts de champs nullables, sans montant par défaut ni migration de données. Création d'une base vide et upgrade depuis les migrations antérieures testés sur bases jetables ; anciens libellés/statuts et capitaux inconnus préservés. Aucune migration appliquée à la base réelle locale ni à Neon.

## 5. Tests et preuves locales finales

| Vérification finale | Réussis | Échecs / erreurs | Ignorés |
| --- | ---: | ---: | ---: |
| Backend, suite SQLite complète : 1 212 cas découverts | 1 173 | 0 | 39, réservés à PostgreSQL |
| Backend PostgreSQL 18, suite étendue : 1 212 cas | 1 211 | 1 erreur d'outillage de sauvegarde | 0 |
| Backend PostgreSQL 18, lot ciblé final migrations/capital/tickets/historique/impression | 59 | 0 | 0 |
| Android, suite Flutter complète du worktree | 314 | 0 | 0 |
| Rendus synthétiques : 10 tickets HTML et 3 PDF officiels, 70 contrôles | 70 | 0 | 0 |

Ces groupes se recoupent : ne pas les additionner comme des scénarios uniques. Les 39 cas ignorés en SQLite ont été exécutés et réussissent dans la passe PostgreSQL étendue.

Erreur PostgreSQL étendue : `BackupCommandTests.test_backup_command_creates_a_verified_snapshot_and_checksum` requiert `pg_dump`, non exposé dans l'environnement fermé du lanceur. Le journal confirme `pg_dump est introuvable`, avant tout export natif. Les sous-processus applicatifs réels sont par ailleurs interdits par ce lanceur. Ce cas ne porte pas sur les modifications tickets/capital et ne constitue pas une validation de sauvegarde en production. Aucun `pg_dump`/`pg_restore` natif n'a été activé, aucun code/test P0-A modifié et aucune sauvegarde réelle exécutée pour lever cette erreur. Elle reste explicitement signalée, pas masquée par un faux succès.

Autres contrôles réussis : cohérence modèles/migrations (`makemigrations --check --dry-run`), contrôles de déploiement, `collectstatic`, validation OpenAPI, compilation des sources Python, import exclusif du snapshot et intégrité de ce snapshot. Analyse Flutter complète : aucune anomalie, code retour zéro. `git diff --check` réussi dans les deux dépôts.

Couverture ajoutée/adaptée : OFF/ON en 58/80, Web/API, vrai PNG fictif, logo absent, absence de fiche entreprise pour nouvelles factures et anciennes factures API, nom et produit très longs, grands montants, champs privés absents, zéro/vide/positif/invalide, permissions sans élévation, stabilité historique/cache, upgrade ancien schéma. Android : 1/4/7/20/50 lignes × deux largeurs × ON/OFF, chargement lent du logo, actions FR/EN, capture hors écran de 50 lignes, reconstruction des bandes et transport simulé.

Les PDF ont été extraits avec pypdf, rendus avec PDFium et inspectés visuellement. Les tickets HTML ont été contrôlés dans Chrome sans réseau : ordre logo/nom, centrage, absence de débordement horizontal, présence de toutes les lignes et du total. Aucun rendu synthétique ne contient de données réelles. L'avertissement de dossier `staticfiles` absent dans le petit exporteur visuel n'est pas un échec de rendu ; la collecte des statiques de la suite complète a réussi.

Des premières passes ont révélé des attentes de tests à corriger : chaîne vide DRF, synchronisation FakeAsync/capture Flutter et champs de formulaire paresseusement construits. Une passe PostgreSQL préalable a aussi expiré avant les tests sous charge locale. Une passe sur un snapshot intermédiaire devenu obsolète a été arrêtée, avec nettoyage du cluster confirmé. La revue finale a corrigé un nom par défaut encore injecté par l'API lorsque la fiche entreprise manque sur une ancienne facture. Ces passes intermédiaires ne sont pas déclarées réussies ; les chiffres ci-dessus proviennent des dernières exécutions correspondantes.

Environnement backend : archive du HEAD local `4e6ba2197ce0e98314d70030d218fc8f0e3eb25b` avec uniquement les fichiers de cette mission superposés, sans `.env`, accès réseau extérieur, cloud, mail réel ou base existante. Les modifications préexistantes hors mission ne sont pas incorporées dans cette qualification backend. PostgreSQL frais, sur loopback, TLS contrôlé, puis cluster arrêté et supprimé.

Références de reproduction locales :

- Snapshot final : `tmp/p0c-admin-tests-20261111/snapshot-117810c9f2274fabac56e5bbfecbaa42`.
- Rapports backend étendus : `sqlite_tests-1794686173108864400.json` et `postgresql_tests-1794686406876925700.json` dans le même répertoire de tests.
- Rapport PostgreSQL ciblé final : `postgresql_tests-1794686995501527900.json`, 59 réussis, aucun échec/erreur/ignoré ; arrêt et suppression du cluster confirmés.
- Flutter : `tmp/thermal-capital-20261114/mobile-full.jsonl`, `mobile-full-analyze.log`.
- Rendus : `tmp/thermal-capital-20261114/artifacts/visual-checks.json`, PNG individuels et `contact-sheet.png`.
- Exporteurs de QA jetables : `tmp/thermal-capital-20261114/render_synthetic.py`, `inspect_documents.py`. Leurs outils PDF/navigateur sont isolés de l'environnement applicatif.

## 6. Vérification réglementaire ciblée

L'article 3 du décret exécutif algérien n° 05-468 du 10 décembre 2005 prévoit le capital social parmi les mentions du vendeur lorsqu'il est applicable. Le ministère du Commerce le rappelle dans sa FAQ. Le champ facultatif permet de ne rien inventer ; cela ne dispense pas une entreprise concernée de renseigner son montant exact. Sources : [Journal officiel n° 80, article 3](https://www.joradp.dz/FTP/jo-francais/2005/F2005080.pdf), [Ministère du Commerce — Facture](https://www.commerce.gov.dz/fr/questions-frequentes/themes/facture).

Cette vérification porte sur l'ajout demandé, pas sur une certification globale de conformité juridique/fiscale de l'ERP. Le bon simplifié n'est pas présenté comme un substitut universel à la facture officielle. Les autres mentions fiscales existantes sont conservées sur cette dernière. La forme juridique et le capital réel doivent être confirmés par une personne compétente de l'entreprise.

## 7. Fichiers applicatifs modifiés ou ajoutés

### Backend : 22 fichiers

Sources et contrat :

- `apps/api/serializers.py`
- `apps/commerce/forms.py`
- `apps/commerce/models.py`
- `apps/commerce/services.py`
- `apps/commerce/utils.py`
- `apps/commerce/templates/commerce/sale_ticket.html`
- `apps/commerce/templates/commerce/sale_invoice_preview.html`
- `apps/core/models.py`
- `apps/core/forms.py`
- `apps/core/templates/core/company_settings.html`
- `apps/core/templates/core/company_settings_form.html`
- `apps/printing/services.py`
- `openapi.yaml`

Migrations : les deux fichiers de la section 4.

Tests :

- `apps/api/tests/test_share_capital.py`
- `apps/core/tests/test_share_capital.py`
- `apps/commerce/tests/test_share_capital.py`
- `apps/commerce/tests/test_share_capital_migrations.py`
- `apps/commerce/tests/test_thermal_branding.py`
- `apps/commerce/tests/test_sale_improvements.py`
- `apps/printing/tests/test_printing.py`

### Mobile : 17 fichiers dans `appAlimGen-mobile`

Sources :

- `lib/app/providers.dart`
- `lib/features/business_lists/domain/business_entities.dart`
- `lib/features/business_lists/domain/invoice_document.dart`
- `lib/features/business_lists/presentation/invoice_document_view.dart`
- `lib/features/business_lists/presentation/invoice_logo_loader.dart`
- `lib/features/business_lists/presentation/invoice_preview_screen.dart`
- `lib/features/company_settings/domain/company_settings.dart`
- `lib/features/company_settings/presentation/company_settings_screen.dart`
- `lib/features/printers/services/esc_pos_printer_driver.dart`

Tests :

- `test/features/business_lists/invoice_preview_test.dart`
- `test/features/business_lists/invoice_thermal_header_test.dart`
- `test/features/business_lists/ticket_preview_pipeline_test.dart`
- `test/features/company_settings/company_settings_repository_test.dart`
- `test/features/company_settings/company_settings_screen_test.dart`
- `test/features/printers/printer_test_service_test.dart`
- `test/features/printers/ticket_stream_integrity_test.dart`
- `test/support/printing_fixtures.dart`

Ce rapport est ajouté séparément. Les nombreux autres fichiers déjà modifiés/non suivis dans les dépôts ne font pas partie de ce lot et ne doivent pas être embarqués automatiquement dans un futur commit.

## 8. Vérifications manuelles restant nécessaires

1. Après autorisation de construction d'un APK de test, vérifier sur téléphone et imprimantes physiques 58 et 80 mm : OFF/ON, logo, noms longs, accents/arabe, 1/7/50 produits, unités, prix, total, fin du dernier produit et avance/coupure. Aucun vrai tirage Bluetooth n'a été effectué ici.
2. Dans le navigateur et Windows/WebView : sélectionner le papier/pilote correct, désactiver les en-têtes/pieds automatiques du navigateur, vérifier les marges et l'absence de coupure. Les limites matérielles de la tête d'impression ne sont pas simulées par les tests de widget.
3. Limite Windows préexistante : certains liens ticket demandent `target="_blank"`, tandis que Tauri refuse les nouvelles fenêtres. Passer par l'aperçu facture puis le ticket pour la qualification ; ce comportement du wrapper n'a pas été changé par la mission.
4. Dans un environnement de recette uniquement : saisir un capital connu, émettre une facture, modifier la fiche sans toucher la facture et vérifier son ancien capital ; modifier volontairement la facture et vérifier le nouveau. Répéter avec zéro et champ vide. Confirmer ensuite le montant légal réel avant toute saisie réelle autorisée.
5. Vérifier le maintien/disponibilité des logos médias historisés. Les trois nouvelles captures évoquées dans le texte de mission n'étaient pas jointes ; le contrôle visuel local repose sur des documents fictifs, pas sur une comparaison à ces captures.

## 9. Autorisations avant publication

Attendre la validation du propriétaire. Puis autoriser séparément, si souhaité :

- préparation de commits ciblés dans les deux dépôts, sans les travaux P0-A ni autres modifications hors mission ;
- push/revue/CI et éventuelle fusion ;
- déploiement backend Render et exécution encadrée des deux migrations sur la vraie base ;
- construction/signature d'un nouvel APK, installation sur téléphone et essais physiques ;
- saisie du capital réel par un utilisateur déjà autorisé.

Aucune de ces opérations de publication n'avait été effectuée à la clôture de la qualification locale ci-dessus.

## Autorisation ultérieure : commits, branches de revue et APK

Le propriétaire a ensuite autorisé les commits, le push et la génération d'un APK. Cette autorisation ne couvre pas une fusion vers `main`, un déploiement Render ou une migration de la base réelle. Le présent lot est préparé depuis le `main` distant `c317fd5c289e8d0dc9630958db626d639f3c6078`, sur la branche de revue `feature/thermal-capital-20261114`. Les 22 fichiers applicatifs de cette mission ne recoupent aucun des changements intervenus entre le HEAD de qualification et ce `main`.

Le verrou de publication des migrations (`APPROVED_EXPANSIONS`) reste inchangé. Les deux nouvelles migrations devront faire l'objet d'une autorisation et d'une revue dédiées avant leur activation réelle. Le workflow de sauvegarde reste désactivé manuellement ; aucun fichier P0-A n'est inclus dans le commit.

L'APK Android doit rester compatible avec le backend actuel : en l'absence du champ `share_capital` dans l'API, le formulaire signale la mise à jour serveur nécessaire et ne prétend pas enregistrer le capital. Stock, impression et correctif produit déjà présent dans l'APK précédent sont conservés avec l'accord explicite du propriétaire.
