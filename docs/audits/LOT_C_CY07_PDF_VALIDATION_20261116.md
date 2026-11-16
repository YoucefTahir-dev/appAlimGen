# Lot C — CY-07 — validation des justificatifs PDF

Date : 2026-11-16

## Périmètre confirmé

Le seul téléversement PDF non fiable identifié est le justificatif facultatif
`Expense.receipt` : formulaire Web des charges et endpoint DRF `api-expense`.
Les deux parcours conservent leurs permissions `add/change_expense`. Les autres
PDF sont produits par ReportLab côté serveur et téléchargés avec
`Content-Disposition: attachment`.

Les protections préexistantes sont conservées : extensions autorisées, MIME,
limite de 10 Mio par défaut et nom de stockage UUID opaque. En production S3,
les URL média restent signées et expirent par défaut après 900 secondes.

## Politique appliquée

La simple signature `%PDF-` est remplacée par une validation bornée avec
`pypdf` en mode strict :

- en-tête PDF placé au premier octet et version reconnue ;
- marqueur final `%%EOF`, analyse de la table de références et des pages ;
- au moins une page, 500 pages au maximum par défaut ;
- 20 000 objets/références au maximum et profondeur de graphe limitée à 64 ;
- lecture limitée à la taille maximale configurée, sans décompression des flux
  pendant l'inspection du contenu actif ;
- refus des documents chiffrés, XFA, JavaScript, actions externes/exécutables,
  pièces jointes et médias actifs ;
- formulaires AcroForm standards sans action active acceptés.

Le lien Web vers le justificatif ne transmet plus le referrer, isole le nouvel
onglet et demande un téléchargement. Le stockage objet demeure responsable de
l'en-tête HTTP final du média ; un navigateur peut ignorer l'attribut
`download` pour une origine différente. Le rejet du contenu actif réduit le
risque, mais aucun parseur ne transforme un PDF non fiable en fichier sûr.

## Vérifications locales

- 16 tests dédiés : PDF classique, PDF ERP, multipage, AcroForm, document
  volumineux sous la limite, faux en-tête, vide, non-PDF renommé, troncature,
  polyglotte, chiffrement, JavaScript, pièce jointe, limites pages/objets/taille,
  extension/MIME, permissions et réponses Web/API.
- 75 tests ciblés SQLite : sécurité, dépenses, API mobile et CY-07.
- Les mêmes 75 tests sur PostgreSQL 18 local TLS jetable : aucun échec, aucune
  erreur, aucun test ignoré ; aucun accès cloud/production ; cluster arrêté et
  runtime supprimé.
- `makemigrations --check --dry-run` : aucune migration.
- verrou hashé régénéré ; installation `--require-hashes --dry-run` réussie.

L'audit de vulnérabilités local n'a pas pu interroger PyPI ni OSV à cause de la
chaîne de certificats du poste Windows. Le workflow GitHub exécute le même audit
sur Ubuntu ; son succès reste donc une condition de fusion.

## Risques résiduels

- Le temps CPU ne peut pas être interrompu de façon portable dans le processus
  Web Windows/Linux ; il est borné indirectement par la taille, les pages, les
  références, la profondeur et les objets. Une isolation antivirus/CDR serait
  un contrôle supplémentaire, non requis par l'usage actuel.
- Les URL S3 signées restent consultables pendant leur courte durée de validité
  si elles sont divulguées à un tiers.
- Les PDF chiffrés et les formulaires XFA sont désormais incompatibles par
  choix de sécurité ; les AcroForm standards restent compatibles.
