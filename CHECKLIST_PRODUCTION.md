# Checklist production

## Sécurité

- [ ] `DJANGO_DEBUG=False`
- [ ] `SECRET_KEY` longue, unique et non versionnée
- [ ] `ALLOWED_HOSTS` contient le domaine Render ou personnalisé
- [ ] `CSRF_TRUSTED_ORIGINS` contient l’origine HTTPS
- [ ] HTTPS forcé
- [ ] Cookies session/CSRF sécurisés
- [ ] HSTS activé après validation HTTPS
- [ ] Compte admin récupéré et mot de passe changé
- [ ] Aucun mot de passe en clair dans les logs

## Base de données

- [ ] PostgreSQL Render connecté via `DATABASE_URL`
- [ ] Migrations appliquées
- [ ] Sauvegardes Render configurées
- [ ] Données locales sensibles non déployées

## Application

- [ ] `python manage.py check`
- [ ] `python manage.py check --deploy`
- [ ] Tests automatisés OK
- [ ] `collectstatic` OK
- [ ] Gunicorn démarre
- [ ] WhiteNoise sert les fichiers statiques
- [ ] Login OK
- [ ] Dashboard OK
- [ ] Produits/clients/fournisseurs OK
- [ ] Ventes/factures PDF OK
- [ ] Permissions rôles OK

## Exploitation

- [ ] Logs Render consultables
- [ ] Procédure reset admin connue
- [ ] Documentation de déploiement à jour
- [ ] Plan de rollback défini
