# Configuration de la réinitialisation du mot de passe

Le Web, l'API mobile et l'action d'administration utilisent tous `PasswordResetService`. En V1, le seul canal actif est l'e-mail. Le lien ouvre la page Web responsive HTTPS ; aucune clé Brevo n'est présente dans Flutter.

## 1. Préparer Brevo

1. Dans Brevo, ouvrez **Expéditeurs, domaines et IP dédiées**.
2. Ajoutez l'adresse d'expédition, puis validez l'e-mail reçu. Pour la production, authentifiez aussi le domaine avec les enregistrements DNS SPF/DKIM proposés par Brevo.
3. Ouvrez **SMTP & API > Clés SMTP**, puis générez une nouvelle clé SMTP dédiée à Render.
4. Copiez le login SMTP et la clé une seule fois dans les variables secrètes Render. Ne mettez jamais cette clé dans Git, Django, Flutter ou une capture d'écran.

Le code repose sur le backend e-mail standard de Django. Brevo peut donc être remplacé par un autre fournisseur SMTP en changeant uniquement les variables `EMAIL_*`.

## 2. Variables Render

Dans **Render > Web Service > Environment**, configurez :

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=<login SMTP Brevo>
EMAIL_HOST_PASSWORD=<clé SMTP Brevo>
DEFAULT_FROM_EMAIL=<adresse expéditeur vérifiée>
DEFAULT_FROM_NAME=El Amine
EMAIL_TIMEOUT=10
PASSWORD_RESET_PUBLIC_BASE_URL=https://<votre-service>.onrender.com
PASSWORD_RESET_TIMEOUT=3600
PASSWORD_RESET_LIMIT=5
PASSWORD_RESET_IP_LIMIT=10
PASSWORD_RESET_WINDOW_SECONDS=3600
API_PASSWORD_RESET_THROTTLE_RATE=10/hour
```

`PASSWORD_RESET_TIMEOUT=3600` correspond à une heure. Après toute modification des variables, redéployez le service.

## 3. Contrôles avant activation

Les adresses e-mail historiques ne sont pas encore contraintes `UNIQUE` en base afin d'éviter une migration destructive. Auditez d'abord les doublons :

```powershell
python manage.py shell -c "from django.contrib.auth import get_user_model; from django.db.models import Count; print(list(get_user_model().objects.exclude(email='').values('email').annotate(n=Count('id')).filter(n__gt=1)))"
```

Corrigez aussi les comptes actifs sans e-mail. Une demande concernant une adresse inconnue ou inactive affiche volontairement le même message neutre et n'envoie rien.

## 4. Test réel obligatoire

Après déploiement :

1. créez ou utilisez un compte de test avec une adresse réelle ;
2. demandez un lien depuis **Mot de passe oublié ?** sur Web puis sur Android ;
3. vérifiez l'expéditeur, le HTML et le texte de repli ;
4. ouvrez le lien HTTPS sur mobile, définissez un mot de passe robuste et reconnectez-vous ;
5. vérifiez que le lien ne fonctionne plus et que les anciens jetons Android sont refusés ;
6. répétez via l'action administrateur **Envoyer le lien** ;
7. contrôlez les journaux Render sans y rechercher ni afficher le token.

En cas d'échec SMTP, l'utilisateur reçoit un message générique et l'événement technique est journalisé sans e-mail ni token.

## 5. SMS et évolution Android

Le formulaire administrateur présente le SMS comme **non configuré**. Aucun envoi SMS n'est simulé : les numéros existants ne sont ni vérifiés ni uniques. L'abstraction de livraison permet d'ajouter plus tard un canal SMS après mise en place de la vérification des numéros.

La V1 Android demande le lien via l'API Django, puis l'utilisateur termine l'opération dans la page Web responsive. `AuthRepository.confirmPasswordReset` utilise déjà le contrat API de confirmation pour une future stratégie App Links, sans embarquer de secret fournisseur.

## 6. Contrat API et OpenAPI

- `POST /api/v1/auth/password-reset/request/` reçoit `{"email":"user@example.com"}`. Succès public `200`, validation `400`, limitation `429`, fournisseur indisponible `503`.
- `POST /api/v1/auth/password-reset/confirm/` reçoit `uid`, `token`, `new_password` et `new_password_confirm`. Succès `200`, lien ou mot de passe invalide `400`, limitation `429`.

Les schémas de requête et de réponse sont publiés dans `openapi.yaml` et dans la documentation DRF réservée aux administrateurs. Le token n'est jamais renvoyé par l'endpoint de demande.

## 7. Dépannage et changement de fournisseur

- Aucun message reçu : vérifier l'expéditeur Brevo, les variables Render, les spams et les journaux génériques `password_reset...`.
- Erreur `503` : tester la connectivité SMTP, le port 587, TLS et la validité de la clé SMTP dans Render.
- Lien invalide : vérifier `PASSWORD_RESET_PUBLIC_BASE_URL`, l'heure système et `PASSWORD_RESET_TIMEOUT`, puis demander un nouveau lien.
- Pour changer de fournisseur SMTP, remplacez seulement `EMAIL_HOST`, le port, TLS et les identifiants. Le Web, Flutter, les tokens et `PasswordResetService` restent inchangés.
