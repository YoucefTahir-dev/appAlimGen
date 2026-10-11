# Réinitialisation du mot de passe — Brevo sur Render

## Cause du changement de transport

Le service Render est configuré sur le plan gratuit. Ce plan bloque le trafic sortant vers les ports SMTP 25, 465 et 587. L'ancienne configuration utilisait `smtp-relay.brevo.com:587` avec un délai de 10 secondes ; elle expirait donc avant l'envoi.

La production utilise maintenant l'API transactionnelle HTTPS Brevo. Le service central `PasswordResetService` et les protections de sécurité restent inchangés. Le transport SMTP Django reste disponible pour le développement local ou un hébergement qui autorise SMTP.

## Variables Render

| Variable | Utilité | Source | Obligatoire | Secrète |
|---|---|---|---:|---:|
| `PASSWORD_RESET_EMAIL_PROVIDER` | Sélectionne le transport | Valeur `brevo_api` | Oui | Non |
| `BREVO_API_KEY` | Authentifie l'API HTTPS | Brevo → SMTP & API → Clés API | Oui | Oui |
| `DEFAULT_FROM_EMAIL` | Expéditeur vérifié | Brevo → Expéditeurs | Oui | Non |
| `DEFAULT_FROM_NAME` | Nom affiché | Par exemple `El Amine` | Oui | Non |
| `EMAIL_TIMEOUT` | Timeout HTTPS en secondes | Valeur recommandée `10` | Oui | Non |
| `PASSWORD_RESET_PUBLIC_BASE_URL` | Base HTTPS des liens | URL publique Render | Oui | Non |
| `PASSWORD_RESET_TIMEOUT` | Durée de validité en secondes | Valeur recommandée `3600` | Oui | Non |
| `PASSWORD_RESET_LIMIT` | Limite par adresse | Valeur recommandée `5` | Oui | Non |
| `PASSWORD_RESET_IP_LIMIT` | Limite globale par IP | Valeur recommandée `10` | Oui | Non |
| `PASSWORD_RESET_WINDOW_SECONDS` | Fenêtre de limitation | Valeur recommandée `3600` | Oui | Non |
| `API_PASSWORD_RESET_THROTTLE_RATE` | Limite API mobile | Valeur recommandée `10/hour` | Oui | Non |

`BREVO_API_KEY` doit être une clé **API Brevo**, et non la clé SMTP ni le mot de passe du compte Brevo. Ne placez jamais cette valeur dans Git, une capture d'écran ou les logs.

Les anciennes variables `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER` et `EMAIL_HOST_PASSWORD` ne sont pas utilisées lorsque `PASSWORD_RESET_EMAIL_PROVIDER=brevo_api`. Elles peuvent être supprimées de Render après validation.

## Configuration Brevo

1. Vérifier que le compte transactionnel est actif.
2. Vérifier l'expéditeur dans Brevo et utiliser exactement son adresse dans `DEFAULT_FROM_EMAIL`.
3. Créer une clé dans **SMTP & API → Clés API**.
4. Enregistrer cette clé uniquement dans `BREVO_API_KEY` sur Render.
5. Si la sécurité IP Brevo est activée, autoriser les plages sortantes du service Render.

## Journaux attendus

Les échecs produisent une ligne sans donnée personnelle ni secret, par exemple :

```text
Password reset delivery failed provider=brevo transport=https_api stage=response exception=HTTPError timeout=false status=401 code=unauthorized
```

- `401 / unauthorized` : clé API absente, invalide ou révoquée.
- `400 / invalid_parameter` : payload ou expéditeur refusé ; vérifier l'expéditeur Brevo.
- `timeout=true` : connexion HTTPS ou fournisseur indisponible.
- `stage=configuration` : clé ou expéditeur absent dans Render.

Le message affiché à l'utilisateur reste volontairement générique.

## Validation après déploiement

1. Redéployer après avoir configuré toutes les variables.
2. Vérifier `/healthz/` et `/readyz/`.
3. Utiliser un compte de test actif avec une adresse autorisée.
4. Demander un lien depuis le Web, puis depuis Android.
5. Vérifier la réception, ouvrir le lien HTTPS et définir un nouveau mot de passe.
6. Vérifier que l'ancien mot de passe et le lien déjà utilisé sont refusés.
7. Contrôler les événements transactionnels Brevo et les logs Render sans rechercher ni afficher le token.
