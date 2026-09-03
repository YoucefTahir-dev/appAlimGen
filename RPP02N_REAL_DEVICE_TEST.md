# Test réel RPP02N depuis Android

## Préconditions

- environnement staging, compte vendeur autorisé à imprimer ;
- Bluetooth et permission Android 12+ `BLUETOOTH_CONNECT` accordés ;
- RPP02N appairée dans les réglages du téléphone ;
- aucune donnée ni URL Neon embarquée.

## Procédure

1. Se connecter à l'API staging et récupérer `/printers/default/`.
2. Associer localement l'identifiant Android de la RPP02N au profil logique.
3. Récupérer `/printers/{id}/test-payload/`, décoder Base64 et envoyer les octets.
4. Noter le résultat : initialisation, accents, avance et coupe.
5. Imprimer un bitmap arabe ; vérifier ligatures et sens RTL.
6. Créer une vente staging puis récupérer `/invoices/{id}/print-data/` en 58 et 80.
7. Vérifier logo, conditionnement, quantité, prix, totaux, QR et absence de rognage.
8. Répéter après reconnexion Bluetooth et redémarrage Android.

Ne jamais lancer ces essais sur les ventes de production. Consigner modèle matériel,
firmware, largeur effective, encodage et commandes non supportées.
