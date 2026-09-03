# Configuration RPP02N

1. Créer `Paramètres > Imprimantes > Ajouter`.
2. Nom : `RPP02N caisse principale`.
3. Connexion : Bluetooth ; modèle : `RPP02N` ; papier : 80 mm.
4. Protocole initial : `Generic ESC/POS`, 48 caractères, encodage `cp858`.
5. Marquer active et, si souhaité, par défaut.
6. Télécharger « Tester l'impression » ou appeler `/test-payload/` depuis Android.

Le nom Bluetooth ne prouve pas la compatibilité ESC/POS. Le diagnostic vérifie
l'initialisation et le texte courant. QR, raster et coupe doivent être testés sur
le vrai périphérique. Si une commande échoue, créer un driver RPP02N dédié et garder
le profil Generic inchangé pour les autres constructeurs.
