# Connexion Windows/Web : formulaire CSRF périmé

Date : 31 octobre 2026.

## Constat et cause

Les journaux fournis montrent une connexion réussie à 14:15:46, puis plusieurs
POST sur `/` rejetés pour `CSRF token from POST incorrect`, avec l'utilisateur
déjà authentifié. Un GET `/core/` réussit aussi : les identifiants ont été
acceptés et le tableau de bord reste accessible.

Django renouvelle le secret CSRF à chaque connexion. Un formulaire chargé avant
la connexion devient donc périmé dans les fenêtres partageant le même profil de
cookies. Le scénario est reproduit avec `Client(enforce_csrf_checks=True)` :
GET du formulaire, POST réussi, rotation du cookie, nouveau POST avec le jeton
initial, HTTP 403. Deux profils de navigateur indépendants peuvent en revanche
se connecter normalement. Les logs seuls ne permettent pas d'affirmer si les
POST supplémentaires proviennent de doubles clics, d'un retour arrière ou de
plusieurs fenêtres ouvertes.

Le formulaire n'avait pas de garde contre les doubles soumissions. De plus,
`UserLoginView` affichait encore un formulaire à un utilisateur déjà connecté.

Référence : [Django — protection CSRF et rotation à la connexion](https://docs.djangoproject.com/en/5.2/ref/csrf/).

## Correctif

- Bloquer les soumissions répétées du formulaire de connexion pendant l'envoi.
- Recharger par GET une page de connexion restaurée depuis le cache de navigation.
- Rediriger les utilisateurs déjà connectés vers leur destination autorisée.
- Éviter les boucles si `next` désigne la connexion elle-même ; préserver les
  redirections internes sûres.
- Remplacer l'erreur technique par une page de reprise traduite. Le POST invalide
  reste refusé avec HTTP 403 ; le lien de reprise effectue uniquement un GET.
- Conserver les contrôles CSRF, origine, HTTPS, session, permissions et limitation
  des tentatives. Aucun POST métier ni mot de passe n'est rejoué automatiquement.

Le correctif est servi par Django aux navigateurs et à la WebView2 Windows.
Le client natif et l'installateur n'ont pas besoin d'être recompilés.

## Vérifications

| Contrôle | Résultat | Preuve |
|---|---|---|
| Reproduction avant correction | PASS | Rotation du cookie puis 403 sur le jeton initial ; tests de reprise et redirection échouant avant le correctif |
| Tests ciblés Django | PASS | 48 tests comptes, rôles et sécurité réussis lors du premier passage vert |
| Non-régression comptes et socle | PASS | 126 tests, 125 réussis, 1 ignoré (unicité e-mail concurrente réservée à PostgreSQL) ; 118,108 secondes |
| Vérification système et migrations | PASS | `manage.py check` sans problème ; aucune migration manquante |
| Collecte des ressources statiques | PASS | Nouveau script inclus dans le manifeste de `collectstatic` |
| Protection réelle | PASS | Client de tests avec contrôle CSRF actif, cookie/jeton absents refusés, origine étrangère refusée, profil non modifiable avec un jeton invalide |
| Connexions indépendantes | PASS | Deux clients avec deux profils de cookies peuvent se connecter |
| Session existante | PASS | Un ancien formulaire ne change pas le compte connecté ; un seul événement de connexion est enregistré |
| JavaScript sous Edge isolé | PASS | 4 tests : double envoi, formulaire incomplet corrigé, touche Entrée, événement de restauration de page |
| HTTPS Render | PASS | `/healthz/` et `/readyz/` répondent HTTP 200 ; validation TLS réussie |
| Essai avec le compte réel | NOT RUN | Aucun mot de passe réel utilisé ; aucune donnée métier de production modifiée |

Les tests navigateur utilisent un profil Edge éphémère et des réponses HTTP
interceptées sur un domaine `.invalid` ; aucune donnée n'est envoyée à Render.
La restauration est testée par un événement `pageshow` avec `persisted=true`,
pas par une coupure réseau ou une session réelle dans l'application installée.

Commandes principales, après configuration d'une base locale SQLite jetable,
du backend e-mail `locmem` et de `DATABASE_URL` vide :

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py test apps.accounts apps.core
.\.venv\Scripts\python.exe scripts\validate_login_browser.py
```

Playwright et Microsoft Edge sont des dépendances de développement facultatives
du script navigateur ; ils ne sont pas nécessaires au serveur.

## Point de configuration Render constaté

La capture montre l'aide technique de Django avec `DEBUG=True`. Un GET public
sur une URL inexistante confirme que la page de diagnostic est actuellement
exposée. `render.yaml` prévoit déjà `DJANGO_DEBUG=False` : vérifier que cette
valeur est réellement appliquée dans **Render > Environment**, puis redéployer.
Le correctif CSRF ne nécessite pas d'activer DEBUG ni d'élargir les origines
autorisées. La variable Render n'a pas été modifiée depuis ce poste.

## Utilisation et livraison

Sur une erreur affichée par l'ancienne version, revenir à l'URL racine de l'ERP
par un nouveau chargement (ou fermer toutes les fenêtres El Amine et relancer).
Ne pas renvoyer l'ancien formulaire avec Actualiser.

Après mise en ligne du correctif, relancer El Amine pour charger le nouveau
formulaire. Aucune migration, aucun nouveau fichier APK et aucune nouvelle
installation Windows ne sont requis.
