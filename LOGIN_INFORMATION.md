# Informations de connexion

## Compte administrateur

Aucun identifiant ni mot de passe par défaut n'est conservé dans le dépôt.

Le compte administrateur doit être créé ou réinitialisé depuis un environnement
de confiance, puis son mot de passe doit être stocké uniquement dans un
gestionnaire de secrets. Ne placez jamais un mot de passe dans Git, un ticket,
un journal applicatif ou une variable versionnée.

- URL locale de connexion : `http://localhost:8000/`

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

La commande demande le nouveau mot de passe sans l'afficher et applique les
validateurs de mot de passe Django. En production, révoquez également les
sessions existantes après toute récupération d'urgence.

## Notes

- En environnement de développement, les emails sont envoyés dans la console Django.
- En production, configurez le backend SMTP via les variables d'environnement dans `.env`.
