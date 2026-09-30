from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db, seed, services
from app.config import public_config


class ExperienceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="docgov-experience-", suffix=".sqlite3", delete=False)
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

    def test_bigD_identity_is_centralized(self):
        config = public_config()
        self.assertEqual(config["app_name"], "bigD")
        self.assertEqual(config["brand_mark"], "bD")
        self.assertIn("bigD", Path("app/web/index.html").read_text(encoding="utf-8"))

    def test_owner_dashboard_actions_match_counter_lists(self):
        alice = self.user("alice@example.test")
        dashboard = services.dashboard(self.conn, alice)
        for metric in dashboard["metrics"]:
            work = services.list_work_items(self.conn, alice, metric["key"])
            self.assertEqual(metric["value"], work["total"])
        self.assertGreaterEqual(dashboard["counts"]["overdue_audits"], 1)
        blocked = [item for item in dashboard["priority_items"] if item["blocked"]]
        self.assertTrue(blocked)
        self.assertIn("intervention bloquante", blocked[0]["block_reason"])

    def test_authorized_search_is_case_and_accent_tolerant(self):
        alice = self.user("alice@example.test")
        erik = self.user("erik@example.test")
        alice_results = services.list_documents(self.conn, alice, {"search": "remuneration", "include_archived": "1"})
        erik_results = services.list_documents(self.conn, erik, {"search": "rémunération", "include_archived": "1"})
        self.assertTrue(any(item["id"] == "DOC-REMUNERATION-RESTREINT" for item in alice_results["items"]))
        self.assertEqual(erik_results["total"], 0)

    def test_published_document_and_active_proposal_are_distinguished(self):
        alice = self.user("alice@example.test")
        detail = services.document_detail(self.conn, alice, "DOC-EDIT-11")
        self.assertEqual(detail["publication_label"], "Publié · v1.0")
        self.assertEqual(detail["proposal_label"], "Mise à jour v1.1 · Brouillon")
        major = services.document_detail(self.conn, alice, "DOC-MAJEUR-20")
        self.assertEqual(major["publication_label"], "Publié · v1.2")
        self.assertEqual(major["proposal_label"], "Nouvelle version v2.0 · En préparation")

    def test_audit_notification_opens_the_precise_audit_context(self):
        alice = self.user("alice@example.test")
        notification = services.list_notifications(self.conn, alice)[0]
        self.assertEqual(notification["type_label"], "Audit à réaliser")
        self.assertEqual(notification["action"]["label"], "Ouvrir l'audit")
        audit_id = int(notification["action"]["href"].rsplit("/", 1)[-1])
        audit = services.audit_detail(self.conn, alice, audit_id)
        self.assertEqual(audit["document"]["id"], notification["document_id"])
        self.assertEqual(audit["version_examined"]["id"], audit["document"]["current_version_id"])

    def test_audit_issue_intervention_task_and_proposal_are_linked(self):
        bruno = self.user("bruno@example.test")
        alice = self.user("alice@example.test")
        audit = self.conn.execute("SELECT * FROM audits WHERE document_id = 'DOC-CONTROLES-RETARD'").fetchone()
        services.submit_audit(
            self.conn,
            bruno,
            audit["id"],
            {"conclusion": "Traitement requis", "issues": [{"title": "Contrôle obsolète", "description": "À traiter"}]},
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
        detail = services.audit_detail(self.conn, alice, audit["id"])
        self.assertIsNotNone(detail["plan"])
        self.assertIsNotNone(detail["proposal"])
        self.assertEqual(detail["issues"][0]["intervention_plan_id"], detail["plan"]["id"])
        self.assertEqual(detail["tasks"][0]["intervention_plan_id"], detail["plan"]["id"])

    def test_publication_11_updates_document_audit_counter_and_due_date(self):
        clara = self.user("clara@example.test")
        alice = self.user("alice@example.test")
        before = services.dashboard(self.conn, alice)["counts"]["validations"]
        version = self.conn.execute(
            "SELECT * FROM document_versions WHERE document_id = 'DOC-EDIT-11' AND status = 'DRAFT'"
        ).fetchone()
        old_due = self.conn.execute("SELECT next_audit_due_at FROM documents WHERE id = 'DOC-EDIT-11'").fetchone()["next_audit_due_at"]
        services.submit_version(self.conn, clara, version["id"])
        services.publish_version(self.conn, alice, version["id"])
        detail = services.document_detail(self.conn, alice, "DOC-EDIT-11")
        after = services.dashboard(self.conn, alice)["counts"]["validations"]
        self.assertEqual(detail["publication_label"], "Publié · v1.1")
        self.assertEqual(detail["proposal_state"], "none")
        self.assertNotEqual(detail["next_audit_due_at"], old_due)
        self.assertLess(after, before + 1)

    def test_marking_notification_read_does_not_close_work(self):
        alice = self.user("alice@example.test")
        notification = services.list_notifications(self.conn, alice)[0]
        audit_id = int(notification["action"]["href"].rsplit("/", 1)[-1])
        before = services.audit_detail(self.conn, alice, audit_id)["status"]
        services.mark_notification_read(self.conn, alice, notification["id"])
        after = services.audit_detail(self.conn, alice, audit_id)["status"]
        self.assertEqual(after, before)

    def test_revoked_access_removes_search_and_notification_content(self):
        alice = self.user("alice@example.test")
        erik = self.user("erik@example.test")
        services.grant_access(
            self.conn,
            alice,
            "DOC-REMUNERATION-RESTREINT",
            {"user_id": erik["id"], "can_comment": True, "reason": "Test temporaire"},
        )
        services.notify(
            self.conn,
            erik["id"],
            "DOC-REMUNERATION-RESTREINT",
            "test-restricted",
            "ISSUE_CREATED",
            "Signalement à décider",
            "Visible temporairement",
            "#/documents/DOC-REMUNERATION-RESTREINT",
        )
        self.conn.commit()
        self.assertTrue(services.list_notifications(self.conn, erik))
        grant_id = self.conn.execute(
            """
            SELECT id FROM document_access_grants
            WHERE document_id = 'DOC-REMUNERATION-RESTREINT' AND user_id = ? AND revoked_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (erik["id"],),
        ).fetchone()["id"]
        services.revoke_access(self.conn, alice, grant_id, "Fin du test")
        docs = services.list_documents(self.conn, erik, {"search": "rémunération", "include_archived": "1"})
        notifications = services.list_notifications(self.conn, erik)
        self.assertEqual(docs["total"], 0)
        self.assertEqual(notifications, [])

    def test_direct_forbidden_audit_detail_does_not_reveal_object(self):
        alice = self.user("alice@example.test")
        erik = self.user("erik@example.test")
        services.open_manual_audit(self.conn, alice, "DOC-REMUNERATION-RESTREINT")
        audit = self.conn.execute("SELECT id FROM audits WHERE document_id = 'DOC-REMUNERATION-RESTREINT'").fetchone()
        with self.assertRaises(services.AppError) as raised:
            services.audit_detail(self.conn, erik, audit["id"])
        self.assertEqual(raised.exception.status, 404)

    def test_document_filters_are_returned_for_url_reload(self):
        alice = self.user("alice@example.test")
        result = services.list_documents(self.conn, alice, {"search": "guide", "proposal_state": "draft", "page": "1"})
        self.assertEqual(result["filters"]["search"], "guide")
        self.assertEqual(result["filters"]["proposal_state"], "draft")
        self.assertEqual(result["page"], 1)


if __name__ == "__main__":
    unittest.main()
