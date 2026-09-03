# Architecture des imprimantes

## Responsabilités

Le backend stocke les profils logiques (`PrinterProfile`, `PrintProfile`) et produit
des données/payloads. Il n'accède à aucun Bluetooth ou USB du magasin.

Le client local assure la découverte, l'autorisation du système, la connexion et
l'envoi : Android pour Bluetooth/Print Service, ou un futur agent Windows pour
USB, réseau et imprimantes système.

## Modèles

- `PrinterProfile` : format, protocole, encodage et préférence métier ;
- `PrintProfile` : ticket 58/80, A4, bon, étiquette, copies et langue ;
- `UserPrinterPreference` : imprimante logique préférée par utilisateur.

Une seule imprimante d'entreprise peut être marquée par défaut. La préférence
utilisateur active est prioritaire. Une adresse Bluetooth peut rester uniquement
dans le stockage sécurisé du terminal.

## Drivers et transports

`PrinterDriver` transforme un document en octets. `GenericEscPosDriver` et
`Rpp02nDiagnosticDriver` sont fournis. `PrinterTransport` est une interface locale ;
`MockPrinterTransport` valide les octets en CI sans matériel.

Un protocole non ESC/POS doit recevoir un nouvel adapter isolé, sans modifier la
facturation ni les endpoints.
