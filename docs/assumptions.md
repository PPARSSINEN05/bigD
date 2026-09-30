# Hypothèses et limites

- Les références de source sont fictives et ne sont jamais récupérées côté serveur.
- SharePoint est stocké comme emplacement de source, distinct du format `PDF`, `Word`, `Excel`, etc.
- Les dates sont stockées en UTC et affichées côté interface en `Europe/Brussels`.
- Un seul audit actif et une seule proposition active sont autorisés par document pour le MVP.
- Les rôles sont stockés par utilisateur et éventuellement par équipe. Le data owner final est le `owner_id` du document.
- Les brouillons sans version publiée ne sont visibles qu'aux participants de workflow ou aux personnes avec accès explicite.
- Les commentaires sont affichés en texte via le DOM, sans HTML exécutable.
- Le journal métier est append-only au niveau de l'application.
- Les notes/scores utilisateurs ne sont pas implémentés.

