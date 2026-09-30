from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import auth, db, services


DEMO_PASSWORD = "demo1234"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def insert_user(conn, name: str, email: str) -> int:
    return conn.execute(
        "INSERT INTO users(name, email, password_hash, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (name, email, auth.hash_password(DEMO_PASSWORD), db.utcnow()),
    ).lastrowid


def insert_role(conn, user_id: int, role: str, team_id: int | None = None) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO role_assignments(user_id, team_id, role, active, reason, created_at)
        VALUES (?, ?, ?, 1, 'Données de démonstration', ?)
        """,
        (user_id, team_id, role, db.utcnow()),
    )


def insert_membership(conn, user_id: int, team_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO team_memberships(user_id, team_id, active, created_at) VALUES (?, ?, 1, ?)",
        (user_id, team_id, db.utcnow()),
    )


def add_doc(
    conn,
    *,
    document_id: str,
    title: str,
    team_id: int,
    owner_id: int,
    created_by: int,
    confidentiality: str = "EQUIPE",
    description: str = "",
    category: str = "Processus",
    tags: list[str] | None = None,
    frequency_value: int = 6,
    frequency_unit: str = "months",
    current: tuple[int, int] | None = (1, 0),
    last_validation: datetime | None = None,
    archived: bool = False,
) -> int | None:
    now = datetime.now(UTC).replace(microsecond=0)
    last_validation = last_validation or (now - timedelta(days=8))
    next_due = services.add_frequency(iso(last_validation), frequency_value, frequency_unit) if current else None
    archived_at = iso(now - timedelta(days=4)) if archived else None
    conn.execute(
        """
        INSERT INTO documents(
            id, title, description, category, tags_json, team_id, owner_id, confidentiality,
            audit_frequency_value, audit_frequency_unit, auditor_role, auditor_team_id,
            audit_instructions, audit_checklist_json, created_by, created_at, last_validation_at,
            next_audit_due_at, archived_at, archive_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AUDITOR', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            title,
            description,
            category,
            services._dumps(tags or ["demo"]),
            team_id,
            owner_id,
            confidentiality,
            frequency_value,
            frequency_unit,
            team_id,
            "Vérifier la validité métier, le owner, la source fictive et les accès.",
            services._dumps(["Source placeholder identifiée", "Owner actif", "Checklist métier revue"]),
            created_by,
            iso(now - timedelta(days=90)),
            iso(last_validation) if current else None,
            next_due,
            archived_at,
            "Obsolète dans le jeu de démonstration" if archived else None,
        ),
    )
    conn.execute(
        """
        INSERT INTO document_access_grants(document_id, user_id, can_view, can_comment, can_contribute, can_review, can_audit, can_manage, granted_by, reason, created_at)
        VALUES (?, ?, 1, 1, 1, 1, 1, 1, ?, 'Owner de démonstration', ?)
        """,
        (document_id, owner_id, created_by, db.utcnow()),
    )
    if current is None:
        return None
    major, minor = current
    version_id = conn.execute(
        """
        INSERT INTO document_versions(
            document_id, major, minor, target_major, target_minor, change_type, title, description,
            source_location_type, source_location_label, document_format, placeholder_ref,
            change_summary, author_id, status, created_at, submitted_at, published_at, validator_id,
            immutable_at
        )
        VALUES (?, ?, ?, ?, ?, 'INITIAL', ?, ?, 'SharePoint', ?, 'PDF', ?, 'Publication de démonstration', ?, 'UP', ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            major,
            minor,
            major,
            minor,
            title,
            description,
            f"Site SharePoint fictif / {document_id}",
            f"PLACEHOLDER-{document_id}",
            created_by,
            iso(now - timedelta(days=40)),
            iso(last_validation),
            iso(last_validation),
            owner_id,
            iso(last_validation),
        ),
    ).lastrowid
    conn.execute("UPDATE documents SET current_version_id = ? WHERE id = ?", (version_id, document_id))
    return version_id


def add_initial_draft(conn, document_id: str, author_id: int, title: str, *, challenge: bool = False, reviewer_id: int | None = None) -> int:
    now = db.utcnow()
    status = "CHALLENGE" if challenge else "DRAFT"
    submitted_at = now if challenge else None
    version_id = conn.execute(
        """
        INSERT INTO document_versions(
            document_id, target_major, target_minor, change_type, title, description,
            source_location_type, source_location_label, document_format, placeholder_ref,
            change_summary, author_id, status, created_at, submitted_at
        )
        VALUES (?, 1, 0, 'INITIAL', ?, 'Brouillon initial de démonstration', 'SharePoint', ?, 'Word', ?, 'Création initiale', ?, ?, ?, ?)
        """,
        (document_id, title, f"Espace fictif / {document_id}", f"PLACEHOLDER-{document_id}", author_id, status, now, submitted_at),
    ).lastrowid
    if challenge and reviewer_id:
        conn.execute(
            """
            INSERT INTO reviews(document_id, version_id, requester_id, reviewer_id, status, created_at)
            VALUES (?, ?, ?, ?, 'PENDING', ?)
            """,
            (document_id, version_id, author_id, reviewer_id, now),
        )
    return version_id


def add_proposal(conn, document_id: str, author_id: int, *, current_version_id: int, change_type: str, target_major: int, target_minor: int, status: str = "DRAFT") -> int:
    current = conn.execute("SELECT * FROM document_versions WHERE id = ?", (current_version_id,)).fetchone()
    now = db.utcnow()
    return conn.execute(
        """
        INSERT INTO document_versions(
            document_id, target_major, target_minor, change_type, title, description,
            source_location_type, source_location_label, document_format, placeholder_ref,
            change_summary, author_id, status, created_at, submitted_at, based_on_version_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            target_major,
            target_minor,
            change_type,
            current["title"],
            current["description"],
            current["source_location_type"],
            current["source_location_label"],
            current["document_format"],
            current["placeholder_ref"],
            "Proposition de démonstration",
            author_id,
            status,
            now,
            now if status == "CHALLENGE" else None,
            current_version_id,
        ),
    ).lastrowid


def seed_demo(path: str | Path | None = None, *, reset: bool = False, verbose: bool = True) -> None:
    db_path = Path(path) if path is not None else db.db_path_from_env()
    if reset and db_path.exists():
        db_path.unlink()
    db.migrate(db_path)
    with db.connection(db_path) as conn:
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] > 0:
            if verbose:
                print("La base contient déjà des utilisateurs, seed ignoré.")
            return
        with conn:
            payroll = conn.execute("INSERT INTO teams(name, slug) VALUES ('Gouvernance paie', 'paie')").lastrowid
            hr = conn.execute("INSERT INTO teams(name, slug) VALUES ('Ressources humaines', 'rh')").lastrowid

            admin = insert_user(conn, "Admin Démo", "admin@example.test")
            alice = insert_user(conn, "Alice Owner", "alice@example.test")
            bruno = insert_user(conn, "Bruno Reviewer", "bruno@example.test")
            clara = insert_user(conn, "Clara Contributrice", "clara@example.test")
            diane = insert_user(conn, "Diane Owner RH", "diane@example.test")
            erik = insert_user(conn, "Erik Autre Équipe", "erik@example.test")

            for user_id in (admin, alice, bruno, clara, diane, erik):
                insert_role(conn, user_id, "USER")
            insert_role(conn, admin, "ADMIN")
            for user_id in (admin, alice, bruno, clara):
                insert_membership(conn, user_id, payroll)
            for user_id in (admin, diane, erik):
                insert_membership(conn, user_id, hr)
            insert_role(conn, alice, "DATA_OWNER", payroll)
            insert_role(conn, alice, "CONTRIBUTOR", payroll)
            insert_role(conn, bruno, "REVIEWER", payroll)
            insert_role(conn, bruno, "AUDITOR", payroll)
            insert_role(conn, clara, "CONTRIBUTOR", payroll)
            insert_role(conn, diane, "DATA_OWNER", hr)
            insert_role(conn, diane, "CONTRIBUTOR", hr)
            insert_role(conn, erik, "CONTRIBUTOR", hr)

            now = datetime.now(UTC).replace(microsecond=0)
            add_doc(
                conn,
                document_id="DOC-ONBOARDING-PAIE",
                title="Guide onboarding paie",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="INTERNE",
                description="Document interne publié et à jour.",
                tags=["paie", "onboarding"],
            )

            add_doc(
                conn,
                document_id="DOC-PAIE-DRAFT",
                title="Procédure DSN brouillon",
                team_id=payroll,
                owner_id=alice,
                created_by=clara,
                current=None,
                description="Document non publié en brouillon.",
            )
            add_initial_draft(conn, "DOC-PAIE-DRAFT", clara, "Procédure DSN brouillon")

            add_doc(
                conn,
                document_id="DOC-AVANTAGES-CHALLENGE",
                title="Catalogue avantages en revue",
                team_id=payroll,
                owner_id=alice,
                created_by=clara,
                current=None,
                description="Première proposition en CHALLENGE.",
            )
            add_initial_draft(conn, "DOC-AVANTAGES-CHALLENGE", clara, "Catalogue avantages en revue", challenge=True, reviewer_id=bruno)

            restricted_current = add_doc(
                conn,
                document_id="DOC-REMUNERATION-RESTREINT",
                title="Politique rémunérations sensibles",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="RESTREINT",
                description="Document restreint avec accès explicites uniquement.",
                tags=["restreint", "rémunération"],
            )
            conn.execute(
                """
                INSERT INTO document_access_grants(document_id, user_id, can_view, can_comment, can_review, can_audit, granted_by, reason, created_at)
                VALUES ('DOC-REMUNERATION-RESTREINT', ?, 1, 1, 1, 1, ?, 'Reviewer explicitement autorisé', ?)
                """,
                (bruno, alice, db.utcnow()),
            )

            overdue_last = now - timedelta(days=75)
            add_doc(
                conn,
                document_id="DOC-CONTROLES-RETARD",
                title="Contrôles trimestriels paie",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="EQUIPE",
                description="Document publié avec audit en retard.",
                frequency_value=30,
                frequency_unit="days",
                last_validation=overdue_last,
            )

            reporting_current = add_doc(
                conn,
                document_id="DOC-REPORTING-BLOQUANT",
                title="Modèle reporting social",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="EQUIPE",
                description="Document publié avec une correction bloquante en cours.",
            )
            reporting_proposal = add_proposal(
                conn,
                "DOC-REPORTING-BLOQUANT",
                clara,
                current_version_id=reporting_current,
                change_type="EDIT",
                target_major=1,
                target_minor=1,
                status="CHALLENGE",
            )
            review_id = conn.execute(
                """
                INSERT INTO reviews(document_id, version_id, requester_id, reviewer_id, status, created_at)
                VALUES ('DOC-REPORTING-BLOQUANT', ?, ?, ?, 'PENDING', ?)
                """,
                (reporting_proposal, clara, bruno, db.utcnow()),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO tasks(document_id, version_id, title, instructions, assignee_id, blocking, status, created_by, created_at)
                VALUES ('DOC-REPORTING-BLOQUANT', ?, 'Clarifier les colonnes obligatoires', 'Intervention bloquante de démonstration', ?, 1, 'TODO', ?, ?)
                """,
                (reporting_proposal, bruno, alice, db.utcnow()),
            )

            edit_current = add_doc(
                conn,
                document_id="DOC-EDIT-11",
                title="Guide variables paie",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="EQUIPE",
                description="Document avec un edit 1.1 en préparation.",
            )
            add_proposal(conn, "DOC-EDIT-11", clara, current_version_id=edit_current, change_type="EDIT", target_major=1, target_minor=1)

            major_current = add_doc(
                conn,
                document_id="DOC-MAJEUR-20",
                title="Politique de contrôle paie",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="EQUIPE",
                description="Document avec une version majeure 2.0 en préparation.",
                current=(1, 2),
            )
            add_proposal(conn, "DOC-MAJEUR-20", clara, current_version_id=major_current, change_type="NEW_VERSION", target_major=2, target_minor=0)

            add_doc(
                conn,
                document_id="DOC-ARCHIVE-OLD",
                title="Ancienne procédure chèques papier",
                team_id=payroll,
                owner_id=alice,
                created_by=alice,
                confidentiality="EQUIPE",
                description="Document archivé de démonstration.",
                archived=True,
            )

            conn.execute(
                """
                INSERT INTO issues(document_id, version_id, author_id, title, description, severity, suggestions, status, created_at)
                VALUES ('DOC-EDIT-11', ?, ?, 'Exemple de signalement mineur', 'Une métadonnée éditoriale doit être corrigée.', 'MOYENNE', 'Préparer un edit 1.1', 'OPEN', ?)
                """,
                (edit_current, bruno, db.utcnow()),
            )
            conn.execute(
                """
                INSERT INTO issues(document_id, version_id, author_id, title, description, severity, suggestions, status, created_at)
                VALUES ('DOC-MAJEUR-20', ?, ?, 'Exemple de changement majeur', 'Le processus métier a changé structurellement.', 'HAUTE', 'Préparer une version 2.0', 'OPEN', ?)
                """,
                (major_current, bruno, db.utcnow()),
            )

            for doc_id in (
                "DOC-ONBOARDING-PAIE",
                "DOC-PAIE-DRAFT",
                "DOC-AVANTAGES-CHALLENGE",
                "DOC-CONTROLES-RETARD",
                "DOC-REPORTING-BLOQUANT",
                "DOC-EDIT-11",
                "DOC-MAJEUR-20",
                "DOC-ARCHIVE-OLD",
            ):
                services.emit_log(conn, alice, "demo_seeded", "document", doc_id, document_id=doc_id, reason="Seed de démonstration")
            services.emit_log(conn, alice, "demo_seeded", "document", "DOC-REMUNERATION-RESTREINT", document_id="DOC-REMUNERATION-RESTREINT", reason=f"Seed restreint version {restricted_current}")

        services.run_scheduler(conn)
        if verbose:
            print(f"Base de démonstration créée dans {db_path}")
            print("Comptes: admin@example.test, alice@example.test, bruno@example.test, clara@example.test, diane@example.test, erik@example.test")
            print(f"Mot de passe commun: {DEMO_PASSWORD}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crée les données de démonstration")
    parser.add_argument("--db", default=str(db.db_path_from_env()))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    seed_demo(args.db, reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
