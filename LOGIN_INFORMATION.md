# Informations de connexion

## Identifiants administrateur

- Nom d'utilisateur : admin
- Mot de passe : Admin123456!
- URL de connexion : http://localhost:8000/

## Récupération du mot de passe

1. Sur la page de connexion, cliquer sur « Mot de passe oublié ? »
2. Saisir l'adresse email du compte
3. Suivre le lien de réinitialisation reçu par email
4. Définir un nouveau mot de passe

## Commande de réinitialisation administrateur

Pour créer ou réinitialiser le compte administrateur :

```bash
python manage.py reset_admin
```

## Notes

- En environnement de développement, les emails sont envoyés dans la console Django.
- En production, configurez le backend SMTP via les variables d'environnement dans `.env`.
