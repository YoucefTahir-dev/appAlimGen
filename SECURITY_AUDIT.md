# Audit de sécurité backend / API — 13 septembre 2026

## Executive Summary

Audit statique ciblé des apps versionnées et tests automatisés locaux. Plusieurs
écarts de permissions/validation et une course de stock ont été corrigés. Le
backend ne doit pas être déclaré intégralement prêt pour le périmètre Android
demandé : droits opérateur par objet absents et révocation JWT après changement
de mot de passe à compléter. Aucun test intrusif de la production effectué.

## Scope / Architecture

Base Git `f18cee2`, main. Django 5.2, DRF et SimpleJWT ; configurations exactes
épinglées dans requirements.in/lock. Web avec sessions/CSRF, REST versionné avec
Bearer JWT. Voir ARCHITECTURE_AUDIT.md pour les modèles, services et points d’entrée.
L’audit n’est pas une preuve d’absence de toute vulnérabilité. Console Render,
IAM GCP, données Neon et appareil physique non inspectés.

## Authentication

Access token 10 minutes, refresh 7 jours par défaut, rotation et blacklist
activées. Clé JWT dédiée configurable, repli sur SECRET_KEY pour compatibilité.
Pas de changement de clé ni de révocation globale pendant cet audit.
Authentification DRF recharge l’utilisateur : compte désactivé refusé, droits
modifiés relus à la requête suivante. BusinessPermission bloque force_password_change.

Corrections : refresh d’un compte supprimé retourne 401 au lieu d’une erreur
non gérée ; refresh refusé si changement de mot de passe imposé ; logout refuse
de blacklister le refresh d’un autre utilisateur. Un access token déjà émis
reste normalement utilisable jusqu’à son expiration après logout.

Réserve HIGH H-R1 : le changement normal de mot de passe ne révoque pas tous
les refresh tokens précédents. CHECK_REVOKE_TOKEN n’est pas activé. Activer
cette option sans transition invaliderait les tokens actuels dépourvus du claim
correspondant. Préparer une migration explicite (reconnexion annoncée, ou version
de session serveur avec période de transition), puis tester changement/reset,
refresh, access et rotation concurrente. La rotation SimpleJWT n’est pas une
garantie de consommation unique sous deux refresh simultanés. Android devra
sérialiser les refresh ; durcissement serveur à prévoir.

## Authorization

Groupes Django, permissions directes, refus individuels ; fallback de rôles
historiques uniquement sans groupe. Superuser reste souverain. RoutePermissionMiddleware
et décorateurs protègent le Web ; BusinessPermission mappe les actions REST.
Les permissions de modèles ne définissent pas la propriété d’une ressource.
Client, fournisseur, produit et imprimante sont actuellement partagés par entreprise.
Ne pas qualifier un accès interutilisateur de sécurisé par propriétaire : aucune
affectation de tournée/opérateur ne permet aujourd’hui de l’imposer.

Les historiques partenaires exigeaient seulement view_client/view_supplier et
révélaient des documents commerciaux interdits. Ils exigent désormais aussi
view_sale/view_purchase. La fiche client Web n’inclut plus ses ventes sans le droit
correspondant. Les alertes API n’exposent plus factures, achats et charges sans
les permissions de lecture de ces modules.

## API Security / contrat

Pagination 25, maximum 100, sauf listes de références. Recherche ORM ; filtres
et tris déclarés. Dates invalides et ID de filtre stock invalide retournent 400.
Enveloppe réelle : `{"success":true,"data":...}` ; erreurs avec success=false,
error.code/message/details. Les tests utilisent aussi response.json(), car
response.data DRF précède le renderer et ne prouve pas le contrat réseau.
Le schéma OpenAPI a été corrigé pour décrire l’enveloppe, les erreurs et le média
PDF/HTML des factures. Les objets dynamiques dashboard/print-data ne sont pas
encore complètement typés ; ne pas générer un SDK en supposant ces détails stables.

Mass assignment : auteur/numéros/totaux vente et auteur charge sont contrôlés
côté serveur. La quantité produit est volontairement éditable avec change_product
comme sur le Web et passe par le journal ; elle n’est pas un champ libre pour vendeur.
SaleLine.unit_cost est maintenant supprimé sans view_product_pricing.

## Web Security

Session, expiration d’inactivité, changement de mot de passe, limites login/reset
adossées aux audit logs ; CSRF middleware présent. Pas de csrf_exempt trouvé dans
les apps auditées. Templates avec échappement Django, notes via linebreaksbr,
graphiques via json_script, autocomplete via textContent ; insertions HTML trouvées
pour les prototypes de formsets locaux. Pas de SQL brut dynamique trouvé : SELECT 1
de readiness est constant ; SQL de tests paramétré.

DEBUG=False par défaut. SECRET_KEY, hôtes, DB et stockage production ont des gardes.
HTTPS, Secure cookies, HttpOnly session, SameSite=Lax, nosniff, frame DENY,
Referrer-Policy et CSP configurés. CSP contient unsafe-inline et jsdelivr, à
durcir progressivement. Pas de CORS_ALLOW_ALL_ORIGINS configuré ; Android natif
n’en a pas besoin. HSTS preload désactivé volontairement, warning W021 documenté.

## Data validation / Database / Concurrency

TVA 0–100 partagée Web/API ; prix unitaire ≥ coût et remise limitée à la marge ;
API charges strictement positives. Stock : transactions, verrous, mise à jour
conditionnelle, contrepassations et ledger immuable via ORM. Coût historisé en
SaleLine. Deux séquences facture/ticket annuelles indépendantes ; suppression
ne remet pas les compteurs à zéro. Le rollback d’une transaction non validée
peut réutiliser une allocation interne qui n’a jamais été publiée.

Course corrigée : ProductSerializer.update utilisait instance.quantity avant
verrouillage même sans champ quantity fourni, et pouvait annuler une vente
concurrente. La valeur par défaut vient désormais du produit verrouillé.

Paiements : contraintes DB montant positif/exactement un document, verrouillage
document, interdiction de surpaiement. Ajout de tests concurrence PostgreSQL
pour deux factures distinctes et deux paiements ; test stock concurrent existant.
Pas de garantie d’idempotence sur répétition HTTP d’une création vente. Les
verrous protègent les stocks mais ne dédupliquent pas une intention utilisateur.
Prévoir clé d’idempotence avant synchronisation hors ligne/retries Android.

## Secrets / Logging

.env n’est pas suivi ; exemples seulement. Recherche de signatures Google/AWS et
clés privées dans les fichiers suivis et historique accessible : pas de résultat.
Recherche de .env et clés privées dans les noms historiques : pas de résultat.
Ce contrôle ciblé n’est pas un scanner de secrets exhaustif ni un audit des
secrets Render/GitHub. La clé Maps visible dans une ancienne capture utilisateur
doit avoir été remplacée ; rotation non vérifiable depuis ce dépôt.

Le géocodeur journalise des codes diagnostiques et ne logue ni URL/clé ni réponse
Google brute. Les audit logs ne stockent pas les corps JWT/password. Les changements
GPS journalisent les coordonnées, donc une politique de rétention/accès est à
définir. EMAIL_BACKEND console par défaut peut écrire les liens de reset dans
les logs : vérifier que le backend SMTP est effectivement configuré en production.

## File uploads / Exports

Noms UUID pour médias, limites de taille, extensions, MIME et validation réelle
Pillow ; XLSX vérifié comme ZIP borné et XML. PDF justificatif vérifié par signature
seulement : ce n’est ni un antivirus ni une suppression de contenu actif. Stockage
objet S3 compatible (GCP possible par interopérabilité), URL signées selon settings.
Permissions réelles du bucket et persistance non certifiées ici.

Excel produits/clients/charges utilisaient déjà excel_safe_text. Le Dashboard
omettait cette protection sur ses catégories et utilisateur : corrigé et testé.
Paragraph utilisateur PDF échappé également. PDF facture protège ses données
via pdf_safe_text. Traductions de plusieurs libellés PDF encore codées en français.

## Rate limits / Performance

DRF : 60/min anonyme, 1200/h utilisateur, auth 10/min configurables. Cache par
défaut local au processus ; pas de cache Redis partagé ni NUM_PROXIES explicite.
Ces limites ne sont pas une barrière forte contre un attaquant distribué/proxy.
Les limites Web utilisent la DB et une adresse proxy interprétée séparément.
Unifier les limites API/proxy sur le déploiement avant charge publique mobile.
Paiements préchargés sur listes/historiques API ; test de croissance des requêtes
sur données non vides. Pas de benchmark de charge grandeur production.

## Findings / Corrections applied

| ID | Niveau | Constat | État |
|---|---|---|---|
| H1 | HIGH | Édition produit sans quantity rétablissant un stock obsolète | Corrigé, test de régression |
| H2 | HIGH | Historiques partenaires et alertes contournant permissions documents | Corrigé Web/API |
| H3 | HIGH | Coût historique exposé dans ventes/factures REST | Corrigé, test rôle restreint/admin |
| H4 | HIGH | TVA API négative/hors limites, charges nulles/négatives | Corrigé, rollback testé |
| H5 | HIGH | Formules Excel injectables dans catégories Dashboard | Corrigé, fichier XLSX relu dans test |
| M1 | MEDIUM | Créations commerciales Web/API dupliquées | Créations centralisées, éditions Web restent distinctes |
| M2 | MEDIUM | Erreurs refresh compte supprimé et logout autre compte | Corrigé |
| M3 | MEDIUM | Sommes de paiement N+1 | Corrigé sur listes/historiques API |
| M4 | MEDIUM | OpenAPI sans enveloppe réelle / mauvais média PDF | Corrigé ; objets dynamiques encore partiels |
| M5 | MEDIUM | Filtres de dates et ID non validés | Corrigé |
| H-R1 | HIGH | Refresh antérieur réutilisable après changement normal de mot de passe | Réserve, migration de révocation nécessaire |
| M-R1 | MEDIUM | Throttling API local, proxy/Redis à qualifier | Réserve infrastructure |
| M-R2 | MEDIUM | Création HTTP sans idempotence ; refresh concurrent | Réserve avant offline/retries |
| M-R3 | MEDIUM | Dashboard : droit global pouvant donner des agrégats de coûts | Règle métier à préciser sans inventer une politique |
| L-R1 | LOW | Encodages/libellés PDF, serializers larges, fonctions longues | Dette documentée, pas de refactor esthétique |

Critical confirmé restant : 0 dans le périmètre inspecté, pas une certification.
High confirmé restant : 1 (H-R1). L’absence de stock opérateur constitue en plus
un blocage fonctionnel/sécurité pour la cible tournée, pas une implémentation validée.

## Dependencies / contrôles / verdict

`pip check` : aucune dépendance incohérente. `pip-audit -r requirements.lock` :
deux essais échoués par certificat ; exécution réussie avec truststore et les
certificats Windows, sans désactiver TLS : **No known vulnerabilities found**.
`manage.py check` et `makemigrations --check --dry-run` : OK.
`check --deploy` sous variables production fictives isolées : seul W021 HSTS preload.
OpenAPI validé ; Python compilé et collecte statique exécutée.

Tests et état CI final consignés dans ANDROID_READINESS.md. Le verdict pour tout
le périmètre demandé est **NOT READY**. Les corrections améliorent le backend
actuel mais ne remplacent ni le module de tournée ni la recette de staging.
