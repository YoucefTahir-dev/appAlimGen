# Lot C — CY-15 : confidentialité et rétention des journaux GPS

Date : 16 novembre 2026. Analyse du code et tests exclusivement synthétiques.
Aucun journal de production, export, archive ou sauvegarde n'a été téléchargé,
lu en masse, déchiffré, supprimé ou modifié.

## État vérifié

Les nouveaux changements de position passent par `audit_location`. L'événement
persisté et envoyé au logger contient seulement l'identifiant du client et le
type de changement (`set`, `changed` ou `cleared`). Il ne contient ni latitude,
ni longitude, ni précision, ni adresse formatée, ni identifiant fournisseur.
L'auteur, la date, le client concerné et les métadonnées générales de l'audit
restent disponibles.

L'ancien code produisait exactement :

```text
client.location.updated id=<entier> old=(<latitude>, <longitude>) new=(<latitude>, <longitude>)
```

La commande `anonymize_location_logs` reste en simulation par défaut, traite au
plus 1 000 lignes par appel (limite configurable de 1 à 10 000) et exige la
confirmation exacte `ANONYMISER GPS 90 JOURS` pour écrire. Elle ne supprime
aucune ligne et ne modifie jamais la position métier du client.

CY-15 rend la reconnaissance stricte sur le seul format historique réellement
émis. Les charges tronquées, étendues ou inconnues restent inchangées et sont
comptées dans `unrecognised` pour revue. Elles ne sont jamais supprimées ou
réécrites silencieusement. Le rapport console expose uniquement des compteurs.

La règle est « strictement plus ancien que 90 jours » : un événement de 89 jours
et un événement âgé d'exactement 90 jours restent intacts ; un événement de
91 jours au format connu devient `change=legacy_redacted`. La ligne, l'auteur,
la date, le chemin, le niveau, le statut et l'adresse IP restent inchangés.

## Destinations : confirmé ou non vérifié

| Destination | Statut | Preuve et limite |
| --- | --- | --- |
| `AuditLog` dans la base applicative PostgreSQL/Neon | Confirmée | `log_security_event` crée une ligne ; aucune donnée réelle n'a été lue et aucun droit SQL n'a été modifié. |
| Sortie standard du logger Django `security` | Confirmée par le code | Le handler `console` reçoit le même événement minimisé. |
| Journaux du service Render | Observée dans les preuves antérieures disponibles | Render collecte la sortie du service ; une recherche opérateur limitée à sept jours n'avait trouvé aucun événement GPS. La durée réelle de conservation et les drains éventuels ne sont pas prouvés par le dépôt. |
| Django admin | Vue, pas une copie | Lecture réservée à un superutilisateur actif ; aucun ajout, changement ou effacement n'est exposé. Les accès SQL restent hors de ce contrôle. |
| Journal d'accès Gunicorn | Aucune copie GPS confirmée | Le démarrage courant ne l'active pas ; s'il est activé séparément, son format exclut les requêtes. Aucune configuration externe n'a été consultée. |
| Consoles locales de développement | Possible selon l'exécution | Le handler console s'applique aussi localement ; aucune console locale historique n'a été inventoriée. |
| Exports, drains externes ou archives de journaux | Non vérifiés | Aucun mécanisme de ce type n'est configuré dans le code inspecté. Leur absence globale ne peut pas être déduite du dépôt. |
| Copies de base et sauvegardes existantes | Hypothétiques pour ce point | Une copie de base pourrait contenir d'anciennes lignes, mais aucune archive réelle n'a été ouverte. Les sauvegardes P0-A et les workflows désactivés restent hors périmètre. |

## Conservation et procédure d'exploitation

- Les nouveaux événements minimisés n'ont pas de durée de purge spécifique dans
  le code ; ils suivent la politique générale du journal, non définie ici.
- Les anciennes charges GPS connues deviennent éligibles après 90 jours. Le
  traitement n'est pas planifié automatiquement.
- La simulation sûre est :
  `python manage.py anonymize_location_logs --limit 1000`.
- Si `unrecognised` est non nul, arrêter l'exploitation et qualifier les formats
  sans afficher leur contenu dans un ticket, un e-mail ou une capture.
- Une application réelle exige une autorisation distincte, une fenêtre
  d'exploitation et la confirmation intégrée à la commande. Répéter les lots
  seulement si `limit_reached` vaut `true`, puis terminer par une simulation.
- Ne jamais utiliser cette commande comme purge : elle ne couvre ni Render, ni
  consoles, ni exports, ni archives et ne doit pas être étendue à ces systèmes
  sans inventaire et autorisation propres.

## Qualification synthétique

- SQLite : 9/9 tests ciblés réussis.
- PostgreSQL 17 local jetable : 9/9 tests ciblés réussis ; base de test détruite,
  serveur arrêté et runtime supprimé.
- Suite SQLite élargie (audit, GPS, clients Web/API, RBAC, CSRF et confidentialité
  du géocodage) : 32 tests découverts, 31 réussis et 1 test PostgreSQL ignoré comme
  prévu sur SQLite.
- `makemigrations --check --dry-run` : aucune migration détectée. Compilation
  Python : réussie.
- Cas couverts : 89/90/91 jours, événement connu avec et sans coordonnées,
  format ancien, deux formats malformés, lot borné, confirmation obligatoire,
  simulation sans écriture, conservation des métadonnées et absence de valeurs
  GPS dans la sortie.
- Les tests de minimisation vérifient également quatre transitions synthétiques
  (`set`, deux `changed`, `cleared`) dans `AuditLog` et dans le logger console.

## Risques résiduels et actions nécessitant un accord

- Les formats inconnus restent volontairement présents jusqu'à qualification.
- Les copies historiques éventuelles hors de la base active ne sont pas traitées.
- L'identité de l'auteur, le client, la date, le chemin et l'IP restent des
  données d'audit ; leur durée générale doit relever d'une politique globale.
- La position actuelle enregistrée dans la fiche client est une donnée métier et
  n'est jamais modifiée par cette commande.
- Toute application sur la production, modification de rétention Render, lecture
  d'archives, suppression d'export ou purge d'une copie exige une autorisation
  supplémentaire. Aucune de ces actions n'a été exécutée dans le lot C.
