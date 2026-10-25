# Validation de l'application Windows El Amine

Date : 25 octobre 2026

Client : Tauri `0.1.0`, cible Windows x64

Périmètre : client Windows local, backend Django en base de test isolée, contrôles publics en lecture seule sur Render

Garanties de validation : aucune écriture Neon, aucun déploiement, aucun e-mail réel et aucune distribution de version officielle

## Verdict

Le client Windows est techniquement apte à entrer en **recette utilisateur interne**, mais pas encore à être publié comme version officielle `1.0.0`.

Les contrôles locaux donnent :

- suite Django/API SQLite : **454 tests exécutés, succès, 12 ignorés** car spécifiques à PostgreSQL ;
- complément PostgreSQL 15 isolé : **12 tests sur 12 réussis**, dont verrouillages et concurrence ;
- client Tauri : **4 tests sur 4 réussis**, formatage réussi et Clippy sans avertissement ;
- build release et installateur NSIS x64 : **réussis** ;
- chargement réel de l'ERP dans WebView2 : **réussi** ;
- disponibilité Render : `/healthz/` et `/readyz/` répondent HTTP 200 avec TLS valide ;
- aucun secret exploitable trouvé dans les sources du client ;
- l'installateur reste **non signé** ;
- les impressions physiques 58/80 mm, les téléchargements authentifiés et les écritures métier dans une vraie WebView2 restent à valider sur une instance de recette.

Un défaut backend non bloquant pour le client a été confirmé : le sélecteur anglais est présent, mais plusieurs textes de connexion restent en français car les entrées correspondantes manquent du catalogue `locale/en`. Le backend partagé n'a pas été modifié dans cette mission.

## Environnements et méthode

### Backend isolé

La suite Django a été lancée avec :

- `DATABASE_ENGINE=sqlite` et `DATABASE_URL` vide ;
- base temporaire de tests créée puis détruite par Django ;
- backend e-mail `locmem` ;
- stockage média local ;
- clé secrète exclusivement locale de validation.

Commande représentative :

```powershell
$env:DJANGO_DEBUG='True'
$env:SECRET_KEY='windows-validation-only-secret-key-...'
$env:DATABASE_URL=''
$env:DATABASE_ENGINE='sqlite'
$env:DATABASE_NAME='windows_validation.sqlite3'
$env:EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
$env:MEDIA_STORAGE_BACKEND='filesystem'
$env:ALLOWED_HOSTS='testserver localhost 127.0.0.1'
.\.venv\Scripts\python.exe manage.py test --verbosity 1
```

Dernière exécution complète du 25 octobre 2026 : `Ran 454 tests in 534.252s — OK (skipped=12)`.

Les 12 cas spécifiques ont ensuite été exécutés sur un conteneur PostgreSQL 15 jetable, lié uniquement à `127.0.0.1:55432`. Résultat : `Ran 12 tests in 40.678s — OK`. La base `test_gestio_validation` a été détruite par Django, puis le conteneur supprimé et Docker Desktop arrêté.

### Client Windows

Commandes exécutées dans `windows-client/src-tauri` :

```powershell
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
cargo tauri build --bundles nsis
```

Le contrôle WebView2 réel a utilisé l'exécutable installé. Il a confirmé l'ouverture de la page de connexion HTTPS, le changement vers l'arabe/RTL, la page de réinitialisation du mot de passe, la fermeture et la réouverture. Aucun identifiant n'a été saisi et aucune donnée métier de production n'a été modifiée.

## Tableau détaillé

| Test | Résultat | Méthode | Preuve | Commentaire |
|---|---|---|---|---|
| Configuration Tauri | PASS | Audit de `tauri.conf.json` et `lib.rs` | Identifiant stable, fenêtre redimensionnable, minimum 1024×700, NSIS `currentUser` | Aucun plugin natif exposé |
| Compilation Rust debug | PASS | `cargo test` | 4 tests réussis, 0 échec | Rust/Tauri compilent |
| Formatage Rust | PASS | `cargo fmt --check` | Code retour 0 | Aucun changement requis |
| Analyse statique Rust | PASS | `cargo clippy --all-targets -- -D warnings` | Code retour 0 | Clippy installé localement pour le contrôle |
| Origine HTTPS officielle | PASS | Tests unitaires Rust | URL officielle, requêtes et fragments autorisés | Port effectif 443 exigé |
| Origines non fiables | PASS | Tests unitaires Rust | HTTP, domaine tiers, sous-domaine, port 444, `file:` et `javascript:` refusés | Test de non-régression ajouté |
| Identifiants intégrés dans une URL | PASS | Test unitaire Rust | `https://user:password@...` refusé | Durcissement ajouté pendant la mission |
| Fenêtres secondaires | PASS | Audit de construction | `on_new_window(...Deny)` | Les liens externes sont bloqués par conception |
| Pont Tauri distant | PASS | Test statique Rust et configuration | `withGlobalTauri=false`, aucune capability, aucun `invoke()` | Réduit la surface native |
| CSP de la page locale | PASS | Audit `tauri.conf.json` | `default-src 'none'`, origine Render explicitement limitée | La CSP distante reste celle de Django |
| Secret dans le client | PASS | `rg` ciblé hors `target` et test statique | Aucun secret exploitable ; seul le littéral de test `api_key` apparaît | Aucun accès Neon, SMTP ou API dans le binaire source |
| Page locale de démarrage | PASS | Audit HTML/JS + lancement réel | Sonde image HTTPS et délai 60 s | Aucun stockage métier local |
| Perte complète de connexion | BLOCKED | Revue du chemin d'erreur seulement | Page « Connexion indisponible » et bouton « Réessayer » présents | La coupure réseau Windows n'a pas été imposée à la machine |
| Démarrage WebView2 réel | PASS | Lancement de l'exécutable installé | Page de connexion de l'origine officielle visible | WebView2 Runtime 155.0.4283.62 détecté |
| Fermeture et réouverture | PASS | Fermeture puis relance de l'exécutable installé | La page HTTPS s'est rechargée | Aucun plantage observé |
| Cookies et persistance de langue | PASS | Changement de langue, fermeture, réouverture | La préférence française sélectionnée avant fermeture a persisté | Validation de session métier limitée car aucun compte de recette n'a été utilisé |
| Connexion réelle | BLOCKED | Non rejouée par l'automate | Le propriétaire confirme son essai manuel réussi | Aucun identifiant n'a été transmis à l'automate |
| Déconnexion réelle | BLOCKED | Session authentifiée indisponible | Couvert par les tests Django | À rejouer sur recette WebView2 |
| Réinitialisation du mot de passe | PASS | Navigation WebView2 sans soumission | `/password-reset/` chargé puis retour connexion | Aucun e-mail envoyé |
| Arabe / RTL | PASS | Clic réel dans WebView2 | Page de connexion entièrement en arabe et RTL | Fonctionnel |
| Anglais | FAIL | Clic réel dans WebView2 + audit du catalogue | Bouton anglais sélectionné, mais plusieurs textes restent français ; traductions absentes du `.po` anglais | Défaut backend à corriger dans un lot i18n séparé |
| HTTPS public | PASS | `curl` en lecture seule | `ssl_verify_result=0` sur les ressources testées | Aucun contournement TLS |
| Santé Render | PASS | GET `/healthz/` | HTTP 200, JSON, `Cache-Control: no-store` | Lecture seule |
| Disponibilité Render | PASS | GET `/readyz/` | HTTP 200 | Lecture seule |
| En-têtes de sécurité | PASS | HEAD/GET public | HSTS, CSP, `nosniff`, `X-Frame-Options: DENY`, politiques cross-origin | Chargement comme document principal, pas comme iframe |
| Ressource de sonde | PASS | GET `/static/img/logo.png` | HTTP 200, `image/png` | Utilisée par l'écran local |
| Suite Django/API complète | PASS | `manage.py test` sur base jetable | 454 tests, 0 échec ; 12 cas PostgreSQL ignorés puis validés séparément | Aucune production touchée |
| Vérification Django | PASS | `manage.py check` | Aucun problème | Environnement local isolé |
| Migrations cohérentes | PASS | `makemigrations --check --dry-run` | `No changes detected` | Aucune migration créée |
| Produits : liste/recherche/consultation | PASS | Tests Django/API existants | Recherche nom/code-barres, détail, pagination couverts | Backend utilisé par WebView2 |
| Produits : création/modification | PASS | Tests isolés | Création, modification, QR/EAN, stock initial couverts | Données fictives en base de test |
| Carton/pièce et prix | PASS | Tests multi-unités | Facteurs, prix carton/pièce, trois tarifs et garde-fous couverts | Calculs côté serveur |
| Stock | PASS | Tests inventaire/API | Entrées, sorties, ajustements, retours, insuffisance et atomicité couverts | Concurrence PostgreSQL séparée ci-dessous |
| Clients | PASS | Tests inventaire/API | Liste, recherche, détail, création, modification et doublons couverts | Base isolée |
| Achats | PASS | Tests commerce/API | Formulaire, lignes mixtes, prix, totaux, mise à jour et rollback couverts | Base isolée |
| Ventes | PASS | Tests commerce/API | Création, PATCH, lignes mixtes, tarifs, totaux, validations et rollback couverts | Base isolée |
| Factures PDF A4 | PASS | Tests Django de génération PDF | PDF, police Unicode, arabe, mentions légales et instantanés historiques couverts | Génération serveur validée, téléchargement WebView2 non authentifié non rejoué |
| Tickets 58/80 mm | PASS | Tests de rendu Django | Deux largeurs, numérotation et aperçu couverts | Rendu logique, pas la sortie papier physique |
| Informations ticket ON/OFF | PASS | Tests d'absence dans le rendu final | En-têtes/pieds réellement absents lorsque OFF | Teste le contenu, pas seulement le CSS |
| Paiements | PASS | Tests commerce/API | Impayé, partiel, complet, surpaiement refusé, annulation et audit couverts | Base isolée |
| Créances clients | PASS | Tests API/services | Affectation FIFO, dette antérieure, annulation et permissions couvertes | Base isolée |
| Bons de chargement | PASS | Tests API | Validation, annulation, clôture, stock opérateur et permissions couverts | Base isolée |
| Authentification/permissions | PASS | Tests comptes/API | Connexion, déconnexion, rôles et restrictions couverts | Pas de compte réel utilisé |
| PostgreSQL et concurrence | PASS | PostgreSQL 15 jetable sous Docker, localhost uniquement | 12 tests, 0 échec, 40.678 s ; base et conteneur supprimés | Ventes, paiements, idempotence, PATCH avec/sans chargement, e-mails uniques et conversion couverts |
| Téléchargement PDF dans WebView2 | BLOCKED | Endpoint couvert, session WebView2 non authentifiée | Aucun fichier métier réel téléchargé | À exécuter sur recette |
| Dialogue d'impression Windows | BLOCKED | Non déclenché dans une page métier authentifiée | `window.print()` et rendu serveur couverts | À rejouer sur recette ; aucune impression réelle |
| Impression physique A4 | BLOCKED | Aucune imprimante de test utilisée | Non exécuté | Pilote et marges à qualifier |
| Impression physique 58 mm | BLOCKED | Imprimante non pilotée depuis cet environnement | Non exécuté | Tester ticket complet et simplifié |
| Impression physique 80 mm | BLOCKED | Imprimante non pilotée depuis cet environnement | Non exécuté | Tester ticket complet et simplifié |
| Build release Tauri | PASS | `cargo tauri build --bundles nsis` | Exécutable release produit | Cible annoncée par Tauri : x64 |
| Packaging NSIS | PASS | Même commande | Installateur de 1 950 430 octets produit | NSIS bootstrapper lui-même est x86, charge utile x64 |
| Architecture de la charge utile | PASS | Lecture en-tête PE | `el-amine.exe` : machine `0x8664` | x86-64 confirmé |
| Version | PASS | Métadonnées PE et Tauri | `0.1.0` | Version de test, pas 1.0.0 |
| Intégrité installateur | PASS | `Get-FileHash -Algorithm SHA256` | `877CEF1C8C5D9DF1245A360D64B7FBA38F353B2E16876E40C846DAFAF8574D45` | Hash du build final du 25/10/2026 |
| Signature numérique | FAIL | `Get-AuthenticodeSignature` | `NotSigned` | Bloquant pour diffusion officielle |
| Dépendance WebView2 | PASS | Inventaire local + configuration | Runtime 155.0.4283.62 présent ; mode `downloadBootstrapper` | Internet requis si Runtime absent |
| Installation/désinstallation automatisée | PASS | Campagne précédente sur ce même poste | Installation, lancement, désinstallation : codes retour 0 | Le propriétaire confirme aussi une installation/connexion manuelle réussie |
| Raccourci courant | FAIL | Lecture du `.lnk` actuel | Cible résiduelle `C:\Users\CodexSandboxOffline\...` inexistante | Artefact de l'installation automatisée sous compte sandbox ; refaire l'essai sur un compte Windows propre avant diffusion |
| Menu Démarrer courant | BLOCKED | Inventaire du profil courant | Aucun raccourci El Amine retrouvé dans le profil DELL au moment du contrôle | À vérifier après installation propre du nouvel installeur |
| Mise à jour automatique | NOT RUN | Audit configuration | Aucun updater activé | Choix prudent tant que les paquets ne sont pas signés |
| Playwright | NOT RUN | Non nécessaire pour les preuves principales | WebView2 réel et tests Django utilisés | Playwright ne remplacerait pas les validations WebView2/thermiques manquantes |

## Anomalies et corrections

### Correction locale appliquée

La liste blanche native acceptait déjà uniquement l'hôte officiel en HTTPS. Elle a été durcie pour refuser aussi une URL officielle contenant des identifiants intégrés. Des tests supplémentaires couvrent désormais :

- query string et fragment valides ;
- sous-domaine trompeur ;
- port non standard ;
- URL avec utilisateur/mot de passe ;
- schémas `file:` et `javascript:` ;
- absence de pont Tauri, d'appel `invoke` et de secret dans l'amorçage.

Tous les tests Rust et Clippy repassent après la correction.

### Défauts non corrigés dans le backend partagé

1. **Catalogue anglais incomplet** : l'interface de connexion reste partiellement française après sélection de l'anglais. Proposition : compléter `locale/en/LC_MESSAGES/django.po`, recompiler les messages et ajouter un test analogue au test arabe.
2. **PostgreSQL local** : les 12 scénarios spécifiques ont finalement été exécutés avec succès sur PostgreSQL 15 jetable. Neon production n'a jamais été utilisé.

### Point d'installation à requalifier

Le raccourci présent sur le Bureau a été créé lors d'une exécution automatisée sous l'identité sandbox et pointe vers ce profil sandbox. Cela ne prouve pas un défaut du script NSIS, mais empêche de déclarer les raccourcis du build courant validés sur un compte Windows propre. Le nouvel installateur ne doit pas être distribué avant cet essai propre.

## Artefacts du build local

Installateur :

`windows-client/src-tauri/target/release/bundle/nsis/El Amine_0.1.0_x64-setup.exe`

- taille : `1 950 430` octets ;
- SHA-256 : `877CEF1C8C5D9DF1245A360D64B7FBA38F353B2E16876E40C846DAFAF8574D45` ;
- signature : absente ;
- cible de la charge utile : x64 (`0x8664`) ;
- version : `0.1.0`.

L'exécutable déjà installé avant ce nouveau build a un hash différent, ce qui est attendu : il ne contient pas le dernier durcissement. Le nouvel installeur a été construit mais n'a pas été distribué officiellement.

## Tests restant à réaliser en recette

1. Installer le nouvel installeur sur un compte Windows propre et vérifier Bureau, menu Démarrer, mise à niveau et désinstallation.
2. Utiliser une instance Django de recette avec copie anonymisée ou données fictives, jamais Neon production.
3. Rejouer connexion, déconnexion, expiration et réouverture avec un compte de recette.
4. Créer et modifier un produit, un client, un achat, une vente et un règlement fictifs dans cette instance.
5. Télécharger un PDF depuis WebView2 et vérifier son ouverture.
6. Déclencher le dialogue Windows pour A4, 58 mm et 80 mm.
7. Imprimer physiquement les tickets complet et simplifié sur les deux largeurs.
8. Corriger le catalogue anglais puis rejouer le parcours de langue.
9. Signer l'exécutable et l'installateur avec horodatage avant toute publication.

## Bilan demandé

1. **Tests réellement exécutés** : 454 tests Django/API sur SQLite, les 12 cas spécifiques rejoués sur PostgreSQL 15, 4 tests Rust, formatage, Clippy, build release/NSIS, contrôles PE/hash/signature/secrets/WebView2, contrôles HTTPS Render et parcours WebView2 public.
2. **Tests réussis** : tous les tests automatisés backend et client ; les seuls échecs fonctionnels observés concernent l'anglais et le raccourci courant issu du profil sandbox.
3. **Problèmes trouvés** : anglais incomplet, installateur non signé, raccourci sandbox résiduel, parcours authentifiés/impression physique non automatisables en sécurité sur la production.
4. **Problèmes corrigés** : durcissement de la liste blanche URL et ajout des tests de non-régression du client.
5. **Tests impossibles ici** : opérations métier réelles sans recette, téléchargement authentifié, impression physique 58/80/A4 et coupure réseau système contrôlée.
6. **Prête pour recette utilisateur** : **OUI**, en environnement de recette isolé.
7. **Faut-il reconstruire l'installateur ?** **C'est fait** après la correction locale ; utiliser uniquement l'artefact et le hash indiqués dans ce rapport.
8. **Version officielle 1.0.0 ?** **NON pour le moment** : signature, installation propre/raccourcis et impression/téléchargement en recette restent à valider.
