# P1 — Confirmation opérateur de la configuration Neon

13 novembre 2026. Complément au rapport P1_11 et au commit local `08b964ebea40af9e859e7b9f346c4755a2b52897`. **Aucun push, changement Render ou accès à la base de production effectué par l'agent.** Les rapports et manifestes précédents restent inchangés.

## Éléments reçus

L'opérateur confirme que le fournisseur est Neon et qu'il a uniquement basculé le bouton d'affichage `Connection pooling`, sans modifier `DATABASE_URL` dans Render. Ses captures montrent les variantes poolée et directe du même endpoint primaire, pour la base `neondb` de la branche `production`. Le mot de passe est masqué ; aucune URL complète ou valeur d'authentification n'est recopiée ici.

Une commande Python standard, communiquée à l'opérateur pour le Shell Render, lit uniquement l'environnement du processus. Elle ne charge ni Django, ni dotenv, ne lit aucun fichier et n'ouvre aucune connexion. Sa logique a été vérifiée localement sur onze cas synthétiques : correspondance, mauvais endpoint/base, pooler, configuration absente ou invalide, et surcharges de routage connues. Un premier lancement local avec `-c` a échoué sur la transmission des guillemets par PowerShell, avant toute exécution ; le même contrôle enregistré dans un fichier temporaire et lancé avec Python isolé a ensuite passé les onze cas. Il ne s'agit pas d'un test de la production.

Harnais synthétique conservé localement : `tmp/p0c-admin-tests-20261111/qualify_neon_readonly_control_20261113.py`, SHA-256 `968315bd961d2c417e88c5ee35391caba99a31c37f3516b8a7c6e431ec5af299`.

La capture de résultat transmise par l'opérateur affiche :

```text
URL_DIRECTE_NEON_IDENTIQUE = OUI
SURCHARGE_ROUTAGE_DETECTEE = NON
```

La première ligne vérifie le schéma PostgreSQL de l'URL, l'égalité exacte du nom d'hôte avec la variante directe affichée dans Neon, le port absent ou 5432 et la base `neondb`. La seconde vérifie les clés URL de routage connues et les variables `PGHOST`, `PGHOSTADDR`, `PGPORT`, `PGDATABASE`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS`. Elle ne déclare pas tous les paramètres libpq ou TLS validés.

**Conclusion : correspondance de configuration directe Neon attestée par la sortie fournie par l'opérateur**, et absence de surcharge connue détectée dans ce contrôle. Ce résultat est plus précis que le précédent `POOLER = NON`. L'agent n'a pas exécuté lui-même la commande sur Render et n'a pas attesté une session SQL réelle.

## Limites et suites

Ce contrôle ne prouve ni la résolution DNS/TLS, ni l'identité effective d'une session serveur, ni ses privilèges ou sa stabilité, ni l'historique des migrations. Aucun verrou, SELECT de diagnostic ou migration n'est demandé à la production. Le champ historique `direct_connection_verified=false` des anciens manifestes ne doit pas être réécrit comme si leurs tests avaient vérifié le serveur réel.

Le lot local des trois phases reste qualifié par ses preuves P1_11 : 321/321 tests ciblés PostgreSQL, douze tests synthétiques de scripts, préflight distinct et trois scénarios de processus concurrents. Aucun de ces tests n'est relancé ou annoncé comme une nouvelle recette dans ce complément purement documentaire.

Avant publication restent nécessaires :

1. Coordonner la première bascule depuis `1006fe9`, le raccordement Render/Blueprint du pré-déploiement et le plancher de retour. Ne pas enregistrer de commande pointant vers un script absent du code publié. Le retour à `1006fe9` reste non sûr après le premier journal, même lorsque les corrections sont désactivées.
2. Faire approuver toute fenêtre sans écritures métier si elle est retenue pour cette première transition ; aucune interruption n'est lancée ou supposée autorisée ici. Une telle fenêtre ne résout pas à elle seule la question du retour automatique après reprise du trafic.
3. Résoudre ou arbitrer explicitement la réserve R03 relative au transport synchrone des emails de réinitialisation. Aucun worker, coût ou service externe supplémentaire n'est engagé.

Ne pas confondre cette confirmation de configuration avec une autorisation de définir immédiatement `MIGRATION_DIRECT_CONNECTION_CONFIRMED`, une validation globale du déploiement ou une clôture de l'audit.
