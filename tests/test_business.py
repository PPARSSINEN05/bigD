from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db, seed, services


class BusinessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="docgov-business-", suffix=".sqlite3", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        seed.seed_demo(self.db_path, reset=True, verbose=False)
        self.conn = db.connect(self.db_path)
        self.users = {
            row["email"]: row
            for row in self.conn.execute("SELECT id, name, email, active, created_at FROM users").fetchall()
        }

    def tearDown(self) -> None:
        self.conn.close()
        for path in [self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")]:
            if path.exists():
                path.unlink()

    def user(self, email: str):
        return self.users[email]

    def active_version(self, document_id: str):
        return self.conn.execute(
            "SELECT * FROM document_versions WHERE document_id = ? AND status IN ('DRAFT','CHALLENGE') ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()

    def current_version(self, document_id: str):
        doc = self.conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self.conn.execute("SELECT * FROM document_versions WHERE id = ?", (doc["current_version_id"],)).fetchone()

    def test_initial_publication_requires_mandatory_metadata(self):
        clara = self.user("clara@example.test")
        team_id = self.conn.execute("SELECT id FROM teams WHERE slug = 'paie'").fetchone()["id"]
        detail = services.create_document(
            self.conn,
            clara,
            {
                "title": "Document incomplet",
                "team_id": team_id,
                "description": "Sans source placeholder",
            },
        )
        version = self.active_version(detail["id"])
        services.submit_version(self.conn, clara, version["id"])
        with self.assertRaisesRegex(services.AppError, "source fictif"):
            services.publish_version(self.conn, clara, version["id"])

    def test_correction_request_returns_challenge_to_draft(self):
        bruno = self.user("bruno@example.test")
        version = self.active_version("DOC-AVANTAGES-CHALLENGE")
        services.request_correction(self.conn, bruno, version["id"], "Corriger la référence source.")
        updated = self.conn.execute("SELECT status FROM document_versions WHERE id = ?", (version["id"],)).fetchone()
        self.assertEqual(updated["status"], "DRAFT")

    def test_blocking_intervention_blocks_publication(self):
        alice = self.user("alice@example.test")
        version = self.active_version("DOC-REPORTING-BLOQUANT")
        with self.assertRaisesRegex(services.AppError, "bloquante"):
            services.publish_version(self.conn, alice, version["id"])

    def test_non_owner_cannot_publish(self):
        bruno = self.user("bruno@example.test")
        version = self.active_version("DOC-REPORTING-BLOQUANT")
        with self.assertRaises(services.AppError) as raised:
            services.publish_version(self.conn, bruno, version["id"])
        self.assertEqual(raised.exception.status, 403)

    def test_current_version_remains_during_new_proposal(self):
        current = self.current_version("DOC-EDIT-11")
        draft = self.active_version("DOC-EDIT-11")
        self.assertEqual((current["major"], current["minor"]), (1, 0))
        self.assertEqual((draft["target_major"], draft["target_minor"]), (1, 1))

    def test_published_versions_are_immutable(self):
        alice = self.user("alice@example.test")
        current = self.current_version("DOC-ONBOARDING-PAIE")
        with self.assertRaisesRegex(services.AppError, "brouillon"):
            services.update_version(self.conn, alice, current["id"], {"title": "Mutation interdite"})

    def test_review_only_keeps_version_number_and_renews_due_date(self):
        alice = self.user("alice@example.test")
        before = self.current_version("DOC-ONBOARDING-PAIE")
        before_doc = self.conn.execute("SELECT next_audit_due_at FROM documents WHERE id = 'DOC-ONBOARDING-PAIE'").fetchone()
        services.review_only_validation(self.conn, alice, "DOC-ONBOARDING-PAIE", {"reason": "Conforme"})
        after = self.current_version("DOC-ONBOARDING-PAIE")
        after_doc = self.conn.execute("SELECT next_audit_due_at FROM documents WHERE id = 'DOC-ONBOARDING-PAIE'").fetchone()
        self.assertEqual((after["major"], after["minor"]), (before["major"], before["minor"]))
        self.assertNotEqual(before_doc["next_audit_due_at"], after_doc["next_audit_due_at"])

    def test_edit_publication_increments_minor(self):
        clara = self.user("clara@example.test")
        alice = self.user("alice@example.test")
        version = self.active_version("DOC-EDIT-11")
        services.submit_version(self.conn, clara, version["id"])
        services.publish_version(self.conn, alice, version["id"])
        current = self.current_version("DOC-EDIT-11")
        self.assertEqual((current["major"], current["minor"]), (1, 1))

    def test_major_publication_increments_major_and_resets_minor(self):
        clara = self.user("clara@example.test")
        alice = self.user("alice@example.test")
        version = self.active_version("DOC-MAJEUR-20")
        services.submit_version(self.conn, clara, version["id"])
        services.publish_version(self.conn, alice, version["id"])
        current = self.current_version("DOC-MAJEUR-20")
        self.assertEqual((current["major"], current["minor"]), (2, 0))

    def test_calendar_month_end_is_adjusted(self):
        self.assertEqual(
            services.add_frequency("2026-01-31T10:00:00Z", 1, "months"),
            "2026-02-28T10:00:00Z",
        )

    def test_overdue_flag_stays_active_during_correction(self):
        bruno = self.user("bruno@example.test")
        alice = self.user("alice@example.test")
        audit = self.conn.execute("SELECT * FROM audits WHERE document_id = 'DOC-CONTROLES-RETARD'").fetchone()
        services.submit_audit(
            self.conn,
            bruno,
            audit["id"],
            {"conclusion": "Correction requise", "issues": [{"title": "Contrôle obsolète", "description": "Point à traiter"}]},
        )
        issue_id = self.conn.execute("SELECT id FROM issues WHERE audit_id = ?", (audit["id"],)).fetchone()["id"]
        services.decide_audit(
            self.conn,
            alice,
            audit["id"],
            {
                "decision_type": "EDIT",
                "justification": "Correction mineure",
                "primary_responsible_id": alice["id"],
                "issue_ids": [issue_id],
            },
        )
        flag = self.conn.execute(
            "SELECT active FROM flags WHERE document_id = 'DOC-CONTROLES-RETARD' AND flag_type = 'AUDIT_OVERDUE'"
        ).fetchone()
        self.assertEqual(flag["active"], 1)

    def test_audit_closure_requires_explicit_issue_resolution(self):
        bruno = self.user("bruno@example.test")
        alice = self.user("alice@example.test")
        audit = self.conn.execute("SELECT * FROM audits WHERE document_id = 'DOC-CONTROLES-RETARD'").fetchone()
        services.submit_audit(
            self.conn,
            bruno,
            audit["id"],
            {"conclusion": "Un point reste ouvert", "issues": [{"title": "Point ouvert", "description": "À trancher"}]},
        )
        with self.assertRaisesRegex(services.AppError, "signalements"):
            services.decide_audit(
                self.conn,
                alice,
                audit["id"],
                {"decision_type": "REVIEW_ONLY", "justification": "Accepté sans changement"},
            )

    def test_scheduler_is_idempotent_for_audits_and_notifications(self):
        first = services.run_scheduler(self.conn)
        second = services.run_scheduler(self.conn)
        audits = self.conn.execute("SELECT COUNT(*) AS c FROM audits WHERE document_id = 'DOC-CONTROLES-RETARD'").fetchone()["c"]
        notifications = self.conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE event_key LIKE 'audit-due:DOC-CONTROLES-RETARD:%'").fetchone()["c"]
        self.assertEqual(first["created_audits"], [])
        self.assertEqual(second["created_audits"], [])
        self.assertEqual(audits, 1)
        self.assertEqual(notifications, 1)

    def test_archived_document_is_not_scheduled(self):
        alice = self.user("alice@example.test")
        self.conn.execute("UPDATE documents SET next_audit_due_at = '2020-01-01T00:00:00Z' WHERE id = 'DOC-ONBOARDING-PAIE'")
        self.conn.commit()
        services.archive_document(self.conn, alice, "DOC-ONBOARDING-PAIE", {"reason": "Obsolète"})
        services.run_scheduler(self.conn)
        active = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM audits
            WHERE document_id = 'DOC-ONBOARDING-PAIE'
              AND status IN ('TO_DO','IN_PROGRESS','AWAITING_OWNER_DECISION','REMEDIATION_IN_PROGRESS','AWAITING_FINAL_VALIDATION')
            """
        ).fetchone()["c"]
        self.assertEqual(active, 0)

    def test_second_publication_attempt_is_rejected(self):
        clara = self.user("clara@example.test")
        alice = self.user("alice@example.test")
        version = self.active_version("DOC-EDIT-11")
        services.submit_version(self.conn, clara, version["id"])
        services.publish_version(self.conn, alice, version["id"])
        with self.assertRaises(services.AppError) as raised:
            services.publish_version(self.conn, alice, version["id"])
        self.assertEqual(raised.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
