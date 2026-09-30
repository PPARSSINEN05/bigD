# bigD — Transitions métier

## Propositions éditoriales

| État source | Action | Acteur | État cible | Règles |
| --- | --- | --- | --- | --- |
| `DRAFT` | Soumettre | Contributeur autorisé | `CHALLENGE` | Crée une review et notifie le reviewer. |
| `CHALLENGE` | Demander correction | Reviewer ou owner | `DRAFT` | Commentaire obligatoire. |
| `CHALLENGE` | Demander intervention | Reviewer ou owner | `CHALLENGE` | Crée une tâche, bloquante par défaut. |
| `CHALLENGE` | Publier | Owner ou `can_manage` | `UP` | Métadonnées requises, aucune tâche bloquante ouverte, version de départ encore courante. |
| `DRAFT`/`CHALLENGE` | Archiver document | Owner ou `can_manage` | `CANCELLED` | Motif obligatoire, traçabilité conservée. |

Publication :

- Initiale : `1.0`.
- Edit : incrémente la mineure.
- Nouvelle version : incrémente la majeure et remet la mineure à `0`.
- Review seule : crée un événement de validation sans changer le numéro publié.

## Audits

| État source | Action | Acteur | État cible |
| --- | --- | --- | --- |
| Aucun audit actif | Planificateur ou owner | Système/owner | `TO_DO` |
| `TO_DO` | Prendre en charge | Auditeur autorisé | `IN_PROGRESS` |
| `TO_DO`/`IN_PROGRESS` | Soumettre résultat | Auditeur autorisé | `AWAITING_OWNER_DECISION` |
| `AWAITING_OWNER_DECISION` | Review seule | Owner | `CLOSED` |
| `AWAITING_OWNER_DECISION` | Edit ou majeure | Owner | `REMEDIATION_IN_PROGRESS` |
| `REMEDIATION_IN_PROGRESS` | Publication finale | Owner | `CLOSED` |
| Tout état actif | Archivage | Owner | `CLOSED` |

La prochaine échéance est calculée depuis la validation finale avec des jours ou mois calendaires. Les mois calendaires ajustent au dernier jour du mois si nécessaire.

## Archivage

L'archivage exige un motif et :

- arrête la planification future ;
- clôt l'audit actif ;
- annule tâches et propositions actives ;
- donne une résolution explicite aux signalements ouverts ;
- désactive les flags actifs ;
- conserve versions, audits, signalements et historique.
