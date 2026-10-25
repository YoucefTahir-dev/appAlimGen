# Application Windows El Amine — audit et préparation

Date de l'audit : 18 octobre 2026

Version du client : `0.1.0`

Statut : prototype installable construit et testé localement, non signé et non distribué

## 1. Résultat

Un client Windows isolé a été créé dans `windows-client/`. Il utilise Tauri v2 et
le moteur Microsoft Edge WebView2 pour afficher l'ERP officiel :

`https://gestio-stock-web.onrender.com/`

Le client ne réimplémente aucune règle métier. Produits, clients, achats, ventes,
factures, stocks, paiements, créances, authentification et permissions restent
entièrement fournis par Django et PostgreSQL. Aucune modification Django, Android,
Neon ou Render n'a été nécessaire.

Un installateur NSIS a réellement été produit :

`windows-client/src-tauri/target/release/bundle/nsis/El Amine_0.1.0_x64-setup.exe`

- Taille du build final de validation du 25 octobre 2026 : 1 950 430 octets (1,86 Mio).
- SHA-256 : `877CEF1C8C5D9DF1245A360D64B7FBA38F353B2E16876E40C846DAFAF8574D45`.
- Signature Authenticode : absente à ce stade ; ne pas distribuer officiellement
  avant signature et tests terrain.

## 2. Audit de l'existant

Le dépôt contient :

- l'application Web Django et ses vues métier ;
- l'API Django REST Framework consommée par Android ;
- les protections Django de session, CSRF, cookies sécurisés, HSTS, CSP et
  permissions ;
- des factures PDF produites côté serveur avec `Content-Disposition: attachment` ;
- des aperçus A4 et tickets thermiques qui utilisent `window.print()` ;
- un document conceptuel `WINDOWS_CLIENT_ARCHITECTURE.md`, mais aucun client
  Windows installable avant ce lot.

`X_FRAME_OPTIONS = DENY` ne bloque pas cette architecture : l'ERP est chargé
comme document principal de WebView2 et non dans une iframe.

## 3. Choix Tauri face à Electron

| Critère | Tauri v2 + WebView2 | Electron |
| --- | --- | --- |
| Moteur Web | WebView2 déjà présent sur Windows moderne | Chromium embarqué |
| Runtime applicatif | Rust, sans Node.js dans cette V1 | Processus principal Node.js + Chromium |
| Taille obtenue ici | Installateur NSIS 1,86 Mio, WebView2 téléchargé seulement si absent | Généralement plus volumineux car Chromium est empaqueté |
| Surface native | Aucune commande ni aucun plugin exposé au contenu distant | Exige une configuration stricte de Node integration, sandbox et context isolation |
| Maintenance métier | Les écrans Web compatibles sont immédiatement repris | Même avantage possible |
| Choix | **Retenu** | Non retenu pour cette enveloppe Web légère |

Tauri est adapté au besoin parce que le poste cible est Windows, WebView2 est
disponible, l'ERP est déjà responsive et aucune logique métier locale n'est
requise. Electron resterait pertinent si une version future devait embarquer un
frontend hors ligne important ou de nombreux modules desktop multiplateformes.

Références officielles :

- [Prérequis Windows Tauri](https://v2.tauri.app/start/prerequisites/)
- [Création d'un projet Tauri](https://v2.tauri.app/start/create-project/)
- [Installateurs Windows Tauri](https://v2.tauri.app/distribute/windows-installer/)
- [Versions des WebView utilisées par Tauri](https://v2.tauri.app/reference/webview-versions/)
- [Modèle de processus Electron](https://www.electronjs.org/docs/latest/tutorial/process-model)
- [Recommandations de sécurité Electron](https://www.electronjs.org/docs/latest/tutorial/security/)

## 4. Architecture retenue

```text
El Amine.exe
  -> page locale minimale de démarrage/hors connexion
  -> contrôle de disponibilité HTTPS
  -> WebView2 vers gestio-stock-web.onrender.com uniquement
  -> session + CSRF + permissions Django
  -> backend Django / Neon identiques au Web
```

La page locale sonde une image statique publique, attend jusqu'à 60 secondes
pour tolérer un réveil Render, puis navigue vers l'ERP. En cas d'échec, elle
affiche un message explicite et un bouton « Réessayer ». Elle ne lit ni ne stocke
de donnée métier.

Les cookies de session sont conservés par le profil WebView2 associé à
l'identifiant stable `dz.elamine.erp.windows`. La connexion et la déconnexion
restent donc les mécanismes Django existants. Aucun jeton n'est copié dans le
code Rust ou JavaScript.

## 5. Sécurité appliquée

- Seules la page locale Tauri et l'origine HTTPS exacte
  `gestio-stock-web.onrender.com:443` peuvent devenir le document principal.
- HTTP, les domaines similaires, `file://` et les autres domaines sont refusés.
- Les demandes `window.open` sont refusées dans la V1 ; un lien externe ne peut
  donc pas ouvrir silencieusement une deuxième WebView privilégiée.
- Aucun plugin Tauri, aucune commande `invoke`, aucune capability distante et
  aucune fonction système ne sont exposés.
- `withGlobalTauri` est désactivé.
- Une CSP restrictive protège exclusivement la page locale de démarrage.
- Les protections WebView2 ne sont pas désactivées et les DevTools ne sont pas
  activés en version release.
- Aucun secret, mot de passe, token ou accès Neon n'est présent dans le client.
- La politique stricte implique qu'un futur changement de domaine officiel doit
  être revu dans `web/app.js`, `src/lib.rs` et la CSP, puis recompilé.

Les liens externes sont volontairement bloqués plutôt qu'ouverts dans la
WebView. Si le métier en a besoin plus tard, une liste blanche et une ouverture
dans le navigateur Windows devront être ajoutées avec des tests dédiés.

## 6. Fichiers créés

| Fichier | Rôle |
| --- | --- |
| `windows-client/web/index.html` | Page locale de démarrage et d'erreur réseau |
| `windows-client/web/app.js` | Sonde HTTPS et redirection vers l'origine autorisée |
| `windows-client/web/styles.css` | Identité visuelle et accessibilité de la page locale |
| `windows-client/web/logo.png` | Logo El Amine redimensionné |
| `windows-client/src-tauri/Cargo.toml` | Dépendances Rust/Tauri |
| `windows-client/src-tauri/Cargo.lock` | Versions résolues et reproductibles |
| `windows-client/src-tauri/build.rs` | Intégration du build Tauri |
| `windows-client/src-tauri/src/main.rs` | Point d'entrée Windows |
| `windows-client/src-tauri/src/lib.rs` | Fenêtre, liste blanche et tests d'URL |
| `windows-client/src-tauri/tauri.conf.json` | Métadonnées, CSP et installateur NSIS |
| `windows-client/src-tauri/icons/*` | Icônes application/installateur |
| `windows-client/README.md` | Commandes de compilation courtes |

Le dossier `target/` est ignoré par Git. L'installateur reste donc un artefact
local de build et non un binaire versionné.

## 7. Dépendances et compilation

Prérequis Windows vérifiés sur le poste :

- Microsoft C++ Build Tools (Visual Studio Community installé) ;
- Microsoft Edge WebView2 Runtime `155.0.4283.62` ;
- Rust MSVC `1.99.1` installé pendant ce lot ;
- Tauri CLI `2.12.3` installé pendant ce lot ;
- Node.js non requis, car la page locale utilise du HTML/CSS/JavaScript simple.

Commandes exactes :

```powershell
cd C:\Users\DELL\Documents\GitHub\gestio_stock\windows-client\src-tauri
$cargo = "$env:USERPROFILE\.cargo\bin\cargo.exe"
& $cargo test
& $cargo tauri build --bundles nsis
```

Le mode `downloadBootstrapper` de Tauri garde l'installateur petit et télécharge
WebView2 seulement si le poste cible ne le possède pas. Une connexion Internet
est donc nécessaire sur un ancien poste dépourvu de WebView2.

## 8. Tests exécutés

| Test | Résultat | Preuve/limite |
| --- | --- | --- |
| Compilation Rust/Tauri release | PASS | `el-amine.exe` produit |
| Tests unitaires de navigation | PASS | 4/4 : origine officielle et page locale autorisées ; HTTP, domaines tiers, ports non standard, identifiants URL et schémas locaux refusés ; amorçage sans pont Tauri ni secret |
| Packaging NSIS | PASS | Installateur 1,86 Mio produit |
| Installation silencieuse | PASS | Code retour 0, version 0.1.0 enregistrée |
| Raccourci Bureau | PASS | `El Amine.lnk` créé |
| Menu Démarrer | PASS | raccourci créé dans le dossier `El Amine` |
| Lancement de l'exécutable installé | PASS | processus actif et répondant après 12 secondes |
| Désinstallation | PASS | code retour 0 ; dossier, raccourcis et entrée registre absents après retrait |
| `/healthz/` | PASS | HTTP 200 en lecture seule |
| `/readyz/` | PASS | HTTP 200 en lecture seule |
| Asset de sonde | PASS | HTTP 200, `image/png` |
| Connexion/déconnexion interactive | À FAIRE | aucun identifiant utilisateur n'a été utilisé pendant ce lot |
| Navigation métier authentifiée | À FAIRE | nécessite un compte de test et une campagne manuelle |
| Création produit/vente/règlement | NON EXÉCUTÉ | interdit sur la production par la mission ; prévoir une instance de recette |
| Téléchargement PDF dans WebView2 | À FAIRE | endpoint et en-tête audités, validation interactive authentifiée requise |
| Impression A4/PDF | À FAIRE | code Web `window.print()` audité, imprimante non disponible dans l'environnement automatisé |
| Ticket 58/80 mm et mode simplifié | À FAIRE SUR MATÉRIEL | dépend du pilote, du profil papier et de l'imprimante réelle |
| Perte de connexion | PARTIEL | page locale et bouton de reprise présents ; coupure réseau réelle non automatisée |

Le build prouve que l'enveloppe s'installe, démarre et se désinstalle. Il ne
constitue pas une validation métier complète : celle-ci exige un environnement
de recette ou un compte de test explicitement autorisé.

## 9. Impression et téléchargements

### A4 et PDF

Les pages actuelles appellent `window.print()`, mécanisme pris en charge par les
WebView. Le dialogue Windows permet de choisir une imprimante ou « Microsoft
Print to PDF ». Les réponses PDF Django sont des téléchargements même origine ;
WebView2 doit présenter son flux normal de téléchargement. Ces deux parcours
doivent encore être validés avec une session réelle.

### Tickets 58 mm et 80 mm

Le rendu HTML, y compris le bouton d'informations simplifiées ON/OFF, reste celui
du Web. La précision physique dépend toutefois :

- du pilote Windows de l'imprimante ;
- d'un format papier 58 ou 80 mm créé dans le pilote ;
- des marges non imprimables et de la coupe automatique ;
- de la prise en charge des caractères français/arabe par le pilote.

WebView2 ne garantit pas une impression thermique silencieuse ni l'envoi brut
ESC/POS. Aucun engagement d'impression directe n'est donc pris avant essais sur
les imprimantes concernées. Si le dialogue Windows ne suffit pas, la solution
future recommandée est un agent d'impression local signé et limité aux travaux
émis par l'origine officielle, avec authentification, liste blanche des
imprimantes et aucune API système générale exposée au site.

## 10. Signature et SmartScreen

L'installateur actuel est non signé. Windows SmartScreen peut afficher un
avertissement « éditeur inconnu ». Avant distribution officielle :

1. acquérir un certificat de signature de code OV ou EV au nom de l'éditeur ;
2. conserver la clé dans un coffre ou token matériel, jamais dans Git ;
3. signer l'exécutable et l'installateur avec horodatage ;
4. vérifier `Get-AuthenticodeSignature` et le hash après signature ;
5. tester installation et désinstallation sur une machine Windows propre.

Le certificat n'a pas été simulé et aucun secret de signature n'a été créé.

## 11. Mise à jour

- Les évolutions Web compatibles sont visibles automatiquement au prochain
  chargement, sans republier le client Windows.
- Une modification du domaine, des règles de navigation, de l'icône ou du code
  natif exige d'augmenter la version, relancer les tests, construire, signer et
  distribuer un nouvel installateur.
- Aucun auto-updater n'est activé dans cette V1 : un canal de mise à jour non
  signé serait un risque inutile.
- Conserver l'identifiant `dz.elamine.erp.windows` afin de préserver le profil
  WebView2 et les sessions lors des mises à niveau.

## 12. Étapes avant distribution officielle

1. Installer ce build sur un poste de recette Windows.
2. Tester connexion, déconnexion, fermeture/réouverture et expiration de session.
3. Exécuter la navigation fonctionnelle complète avec un compte de test.
4. Tester un PDF et une impression A4.
5. Tester physiquement une imprimante 58 mm puis 80 mm, ticket complet et
   simplifié.
6. Vérifier les téléchargements dans le dossier choisi par l'utilisateur.
7. Signer le binaire et l'installateur.
8. Refaire installation, mise à niveau et désinstallation sur un poste propre.
9. Obtenir l'autorisation explicite avant toute distribution officielle.

## 13. Limites connues

- L'application requiert Internet et la disponibilité de Render.
- Elle n'est pas une application hors ligne et ne contient pas de copie locale
  de Neon.
- Les liens vers des domaines externes sont bloqués dans la V1.
- Le téléchargement et l'impression n'ont pas encore été validés dans une
  session authentifiée.
- L'impression thermique directe reste à qualifier sur le matériel réel.
- L'installateur non signé est uniquement un artefact de validation interne.
