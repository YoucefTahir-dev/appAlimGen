# Approbation ciblée des migrations de capital social

Le propriétaire a explicitement autorisé la fusion backend, son déploiement Render et les deux migrations Neon de ce lot. Cette autorisation ne couvre aucun changement de droits SQL, compte utilisateur, sauvegarde P0-A ou reprise des données historiques.

## Périmètre approuvé

Deux opérations `AddField` atomiques, sans défaut, reprise de données, index, relation, contrainte `NOT NULL` ou opération de données :

| Migration | Colonne facultative | Empreinte UTF-8 normalisée LF |
| --- | --- | --- |
| `core.0006_companysettings_share_capital` | `core_companysettings.share_capital`, décimal 14,2 nullable | `7577bd3b0da9bd0cced6b8b3dc44653b552999b8d0cb57e20e9971706e96519f` |
| `commerce.0018_sale_company_share_capital_snapshot` | `commerce_sale.company_share_capital_snapshot`, décimal 14,2 nullable | `a4bf12b34bf4ee95ada0efa77de227b4daf6a262b1b16b0626fcb01b63ad36f5` |

Les anciennes lignes conservent une valeur inconnue `NULL`, distincte de zéro. L'ancien code peut continuer à lire et insérer sans connaître les nouvelles colonnes. L'application valide les montants ; aucune contrainte SQL ni permission supplémentaire n'est ajoutée par ces migrations. Le capital réel ne sera pas renseigné automatiquement.

`APPROVED_EXPANSIONS` contient uniquement ces deux nouvelles entrées exactes, en plus des approbations existantes. Aucune règle générique, wildcard ou suppression de garde n'est introduite. Une empreinte modifiée, une opération supplémentaire, une migration inconnue, un plan descendant ou non atomique restent refusés.

## Préconditions réelles de publication

- Confirmer que Render utilise `bash predeploy.sh` comme pré-déploiement et la connexion directe déjà vérifiée, avec son attestation opérateur inchangée.
- Conserver les limites : attente advisory 30 secondes, attente de verrou DDL 5 secondes, durée d'un statement 120 secondes et durée globale de pré-déploiement 300 secondes par défaut.
- Le build ne migre pas. Le démarrage vérifie le schéma en lecture seule et refuse tout retard ; il n'applique jamais de migration de secours.
- Aucun bootstrap, `--fake`, downgrade compensatoire ou restauration automatique n'est autorisé. Une migration peut être validée avant l'échec de la suivante ; examiner l'état appliqué et corriger en avant, sans supprimer de données.

## Qualification prévue avant fusion

Les tests ajoutés contrôlent les deux contrats de champs et leur acceptation exacte, le refus des empreintes altérées et des opérations supplémentaires. Le scénario PostgreSQL utilise uniquement une base jetable : il prépare l'ancien schéma, vérifie un plan en lecture seule de deux migrations, applique la vraie commande gardée, puis vérifie les valeurs historiques inconnues et la compatibilité des classes d'anciens modèles.

La préparation de l'ancien schéma par le test n'est pas une procédure de production. Les résultats de qualification et le déploiement réel doivent être attestés séparément ; ce document ne prétend pas qu'une migration réelle a déjà été exécutée.
