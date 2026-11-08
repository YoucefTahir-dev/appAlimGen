# Correctif ciblé des dépendances — 8 novembre 2026

Autorisation propriétaire : mettre à jour les quatre dépendances signalées,
tester le lot isolément, puis publier/déployer uniquement si les contrôles passent.
Aucun export, restauration de production, secret distant ou forfait à modifier.

## Versions retenues

| Paquet | Avant | Après |
| --- | --- | --- |
| urllib3 | 2.7.0 | 2.8.0 |
| Django REST framework | 3.17.2 | 3.18.3 |
| Pillow | 12.3.0 | 13.0.0 |
| PyJWT | 2.13.0 | 2.15.1 |

Le lock a été régénéré avec les empreintes depuis PyPI officiel. Une comparaison
automatique avec `HEAD:requirements.lock` confirme exactement ces quatre changements
de version, aucun ajout/retrait ni autre mise à jour. Les deux dépendances
transitives de sécurité sont aussi explicites dans `requirements.in`.

## Compatibilité API v1

Un contrôle isolé sur les versions ancienne/nouvelle confirme que DRF change par
défaut les erreurs des listes imbriquées : liste positionnelle vers dictionnaire
d'indices. Le réglage officiel `LIST_SERIALIZER_ERRORS_AS_DICT=False` conserve le
contrat v1, sans retirer les correctifs de sécurité. Trois tests figent les
positions, les champs ventes/achats/chargements et les entrées non-listes.
Cette compatibilité est dépréciée par DRF : migration versionnée à prévoir avant
DRF 3.20. Aucun changement de règle métier, modèle, migration ou authentification.

## Vérifications

- Nouvelle venv jetable, distincte de la `.venv` du propriétaire ; installation
  des deux locks par roues binaires et `--require-hashes` : PASS.
- `pip check` : PASS ; Django 5.2.17 et cryptography 50.0.2 conservés.
- Audit courant des deux locks : **45 dépendances, zéro avis connu, exit 0**.
  L'audit utilise les racines de confiance du système, sans désactiver TLS.
  Un audit sans avis ne constitue pas une garantie d'absence de vulnérabilité.
- Promotion sur `main` conditionnée à la suite complète sur copie propre et à une
  CI Linux/Python 3.12/PostgreSQL réussie sur une branche `security/…`, avant tout
  déploiement de production. Les rapports locaux sont sous
  `tmp/security-dependency-tests-20261108/` (ignorés par Git) et les résultats CI
  sont attachés au commit candidat. Aucun résultat PostgreSQL n'est déduit
  d'une suite SQLite.

Les changements préexistants de `.env.example`, des workflows, du module backup
historique et de ses tests restent exclus. Les clés des bancs ne sont pas réutilisées.
Les deux indicateurs d'activation Drive restent désactivés ; aucune sauvegarde
réelle n'est créée par ce lot.

## Sources primaires

- [Notes DRF 3.18 et réglage de compatibilité](https://www.django-rest-framework.org/community/release-notes/#318x-series).
- [urllib3 2.8.0](https://github.com/urllib3/urllib3/releases/tag/2.8.0).
- [Pillow 13.0.0](https://pypi.org/project/Pillow/13.0.0/).
- [PyJWT 2.15.1](https://github.com/jpadilla/pyjwt/blob/2.15.1/CHANGELOG.rst).
- [Audit CI précédent](https://github.com/YoucefTahir-dev/appAlimGen/actions/runs/42521859345).
