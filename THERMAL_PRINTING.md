# Impression thermique 58 et 80 mm

Les tickets HTML existants restent imprimables par le navigateur. L'API fournit en
plus des données neutres via `/api/v1/invoices/{id}/print-data/`.

| Papier | Caractères conseillés | Usage |
|---|---:|---|
| 58 mm | 32 | ticket compact, logo/QR réduits |
| 80 mm | 48 | ticket détaillé |

Le payload contient société, client, lignes avec conditionnement, quantités, prix,
totaux, paiement et QR code. `language` accepte `fr`, `ar`, `en`, `bilingual`.

Les imprimantes thermiques ont souvent une table de caractères sans arabe. Dans ce
cas, Android/Windows doit composer l'en-tête ou le ticket en bitmap monochrome,
puis envoyer une commande raster supportée. Ne pas translittérer ou supprimer le
texte arabe.

Avant utilisation réelle : tester alimentation, initialisation, caractères latins,
QR, raster arabe, avance papier et découpe. Une imprimante sans cutter doit ignorer
la commande de coupe.
