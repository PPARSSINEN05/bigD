# Gouvernance documentaire SD Worx - prototype fonctionnel

Application full-stack sans dépendance externe : Python standard library, SQLite, API JSON, sessions par cookie, CSRF, interface web native et tests `unittest`.

## Choix de stack

Le dépôt était vide. J'ai choisi une stack Python + SQLite pour fournir rapidement une application réellement exécutable dans ce workspace sans téléchargement de packages. SQLite apporte une base relationnelle avec contraintes, index et migrations SQL. L'authentification utilise des sessions serveur, cookies `HttpOnly`/`SameSite=Strict`, jetons CSRF et mots de passe hachés avec `hashlib.pbkdf2_hmac`.

Hypothèse importante : dans un contexte production réel, on remplacerait cette authentification maison par un framework maintenu ou un IdP client. Ici, elle reste volontairement compacte pour un MVP autonome.

## Installation et lancement

```bash
python3 -m app.seed --reset
python3 -m app.server --migrate --host 127.0.0.1 --port 8000
```

Puis ouvrir `http://127.0.0.1:8000`.

Comptes fictifs :

- `admin@example.test`
- `alice@example.test`
- `bruno@example.test`
- `clara@example.test`
- `diane@example.test`
- `erik@example.test`

Mot de passe commun : `demo1234`.

Le sélecteur de comptes est disponible uniquement si `APP_ENV` n'est pas `production` et `DEMO_USER_SELECTOR=1`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Planificateur

Le planificateur idempotent crée les audits arrivés à échéance, le flag `AUDIT_OVERDUE` et une notification unique par échéance.

```bash
python3 -m app.scheduler
```

Une route API existe aussi pour le mode démonstration : `POST /api/scheduler/run`, réservée aux administrateurs.

## Périmètre livré

- Catalogue documentaire filtré et paginé côté API.
- Fiche document avec métadonnées, versions, audits, tâches, signalements, flags et historique.
- Workflow `DRAFT -> CHALLENGE -> UP`, corrections, interventions bloquantes et publication atomique.
- Versionnement initial `1.0`, edit mineur `x.y+1`, nouvelle majeure `x+1.0`, review seule sans changement de numéro.
- Audits périodiques, copie de checklist, décision owner, plan d'intervention, clôture par validation finale.
- Signalements, commentaires, notifications internes, snooze de flag et archivage.
- Permissions serveur à chaque lecture et mutation.
- Données de démonstration fictives uniquement.

## Limites restantes

- Pas de connecteur externe, pas de téléchargement ni d'analyse de fichiers.
- Pas de restauration d'archive ni de suppression définitive.
- Pas de relance périodique avancée des notifications.
- L'administration est volontairement minimale pour le MVP.
- L'authentification est autonome pour le prototype ; production recommandée avec IdP ou framework maintenu.

