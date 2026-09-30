# bigD — Matrice des permissions

Refus par défaut : toute lecture et mutation vérifie le serveur.

| Action | Utilisateur autorisé | Contributeur | Auditeur | Reviewer | Data owner du document | Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Voir `INTERNE` publié | Oui | Oui | Oui | Oui | Oui | Oui |
| Voir `EQUIPE` publié | Si membre équipe | Si membre équipe | Si membre équipe | Si membre équipe | Oui | Si membre équipe ou accès |
| Voir `RESTREINT` publié | Accès explicite | Accès explicite | Accès explicite | Accès explicite | Oui | Accès explicite |
| Voir brouillon/proposition | Participant workflow ou accès | Auteur/accès | Si audit/accès | Assigné/accès | Oui | Accès explicite |
| Créer document | Non | Dans son équipe | Non | Non | Dans son équipe | Oui |
| Modifier brouillon | Non | Si autorisé | Non | Non | Oui | Accès explicite |
| Demander correction | Non | Non | Non | Si reviewer | Oui | Accès explicite |
| Demander intervention | Non | Non | Non | Si reviewer | Oui | Accès explicite |
| Publier | Non | Non | Non | Non | Oui | Accès explicite `can_manage` |
| Signaler problème | Si consultation/commentaire | Oui | Oui | Oui | Oui | Si consultation |
| Prendre audit | Non | Non | Si rôle auditeur équipe ou assigné | Non | Oui | Accès explicite |
| Décider audit | Non | Non | Non | Non | Oui | Accès explicite `can_manage` |
| Archiver | Non | Non | Non | Non | Oui | Accès explicite `can_manage` |
| Gérer utilisateurs | Non | Non | Non | Non | Non | Oui |

Notes :

- Être administrateur ne donne pas automatiquement accès au contenu `RESTREINT`.
- Une attribution de tâche ne contourne pas les accès : l'API refuse l'assignation si la personne ne peut pas lire le document.
- Les accès exceptionnels sont enregistrés dans `document_access_grants` et tracés dans `activity_logs`.
- La révocation d'un accès est effective dès les requêtes suivantes.
