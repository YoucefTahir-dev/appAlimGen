# Récupération et gestion des mots de passe

## 1. Changer son mot de passe

L'utilisateur connecté peut modifier son mot de passe depuis la page "Mon Profil".

- Accéder à : `/profile/`
- Remplir les champs : mot de passe actuel, nouveau mot de passe, confirmation du nouveau mot de passe
- Le système vérifie :
  - le mot de passe actuel
  - la correspondance des deux nouveaux mots de passe
  - la validité du mot de passe selon les validateurs Django

### Message
- en cas de succès, le message de confirmation s'affiche
- en cas d'erreur, des messages explicites sont affichés

## 2. Mot de passe oublié

Sur la page de connexion, cliquer sur "Mot de passe oublié ?".

### Processus
1. Saisir l'adresse email associée au compte
2. Recevoir un email contenant un lien sécurisé de réinitialisation
3. Cliquer sur le lien et définir un nouveau mot de passe

### Configuration
- Par défaut, l'application utilise `django.core.mail.backends.console.EmailBackend`
- En production, configurer un serveur SMTP via les variables d'environnement :
  - `EMAIL_BACKEND`
  - `EMAIL_HOST`
  - `EMAIL_PORT`
  - `EMAIL_USE_TLS`
  - `EMAIL_HOST_USER`
  - `EMAIL_HOST_PASSWORD`
  - `DEFAULT_FROM_EMAIL`

## 3. Réinitialisation administrateur

L'administrateur peut réinitialiser le mot de passe d'un utilisateur depuis l'administration Django.

- Sélectionner un utilisateur
- Choisir l'action "Réinitialiser le mot de passe utilisateur"
- Définir un nouveau mot de passe et, optionnellement, forcer le changement à la prochaine connexion

## 4. Compte administrateur de secours

Pour recréer un administrateur, exécuter :

```bash
python manage.py create_admin
```

ou utiliser la nouvelle commande :

```bash
python manage.py reset_admin
```

Le script demande :
- nom d'utilisateur
- email
- mot de passe

Il crée un superutilisateur compatible avec le système d'authentification Django.
