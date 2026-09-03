# Architecture du futur client Windows

Le futur EXE utilise HTTPS/JWT vers Django et ne contient aucun secret Neon.

```text
Windows UI -> API client -> Django /api/v1 -> services métier -> Neon
           -> Local Print Agent -> spooler Windows / USB / TCP
```

Le stockage local conserve seulement l'URL API, les tokens protégés par Windows
Credential Manager et l'association locale imprimante/profil. Les opérations
sensibles restent autorisées par Django. Le Local Print Agent doit accepter des
commandes uniquement depuis l'utilisateur local authentifié et journaliser les
échecs sans imprimer les tokens ou données confidentielles.
