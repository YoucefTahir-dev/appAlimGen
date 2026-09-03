# Architecture API centralisée

## Source de vérité

```text
Web Django (sessions) ─┐
Android (JWT/HTTPS) ───┼─> Django ─> services métier ─> PostgreSQL Neon
Windows (JWT/HTTPS) ───┘
```

Les clients ne connaissent jamais `DATABASE_URL`. Les règles de stock, prix,
numérotation, paiements et impression sont exécutées côté Django. Les vues HTML et
les serializers appellent les mêmes modèles/services transactionnels.

## Découpage

- `apps/inventory/services.py` : journal et mutations atomiques du stock ;
- `apps/commerce/services.py` : création transactionnelle ventes/achats et séquences ;
- `apps/printing/services.py` : contrat d'impression et drivers sans I/O matériel ;
- `apps/api/` : adaptation HTTP/JWT, validation, permissions et OpenAPI ;
- `openapi.yaml` : contrat versionné pour Android et Windows.

## Sécurité

Chaque route applique les permissions Django effectives, y compris les refus
individuels. JWT utilise une clé distincte via `JWT_SIGNING_KEY`, une durée courte,
rotation et blacklist. Les uploads passent par les validateurs Web existants.
CORS n'est pas une protection pour une application native : HTTPS, authentification,
autorisation et limitation de débit restent obligatoires.
