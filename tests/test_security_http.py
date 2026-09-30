from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app import db, seed, server, services


class HttpClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        set_cookie = response.getheader("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        conn.close()
        return response.status, payload

    def login(self, email: str, password: str = seed.DEMO_PASSWORD) -> None:
        status, payload = self.request("POST", "/api/login", {"email": email, "password": password})
        if status != 200:
            raise AssertionError(payload)
        self.csrf = payload["csrf_token"]


class SecurityHttpTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="docgov-http-", suffix=".sqlite3", delete=False)
        handle.close()
        cls.db_path = Path(handle.name)
        seed.seed_demo(cls.db_path, reset=True, verbose=False)
        with db.connection(cls.db_path) as conn:
            alice = conn.execute("SELECT id, name, email, active, created_at FROM users WHERE email = 'alice@example.test'").fetchone()
            restricted = "DOC-REMUNERATION-RESTREINT"
            services.open_manual_audit(conn, alice, restricted)
            conn.execute(
                """
                INSERT INTO tasks(document_id, title, instructions, assignee_id, blocking, status, created_by, created_at)
                VALUES (?, 'Tâche restreinte', 'Invisible hors accès', ?, 1, 'TODO', ?, ?)
                """,
                (restricted, alice["id"], alice["id"], db.utcnow()),
            )
            conn.execute(
                """
                INSERT INTO issues(document_id, version_id, author_id, title, description, severity, status, created_at)
                VALUES (?, (SELECT current_version_id FROM documents WHERE id = ?), ?, 'Signalement restreint', 'Invisible hors accès', 'HAUTE', 'OPEN', ?)
                """,
                (restricted, restricted, alice["id"], db.utcnow()),
            )
            conn.commit()
        cls.httpd = server.make_server("127.0.0.1", 0, cls.db_path)
        cls.host, cls.port = cls.httpd.server_address
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        for path in [cls.db_path, Path(str(cls.db_path) + "-wal"), Path(str(cls.db_path) + "-shm")]:
            if path.exists():
                path.unlink()

    def client(self, email: str) -> HttpClient:
        client = HttpClient(self.host, self.port)
        client.login(email)
        return client

    def test_direct_restricted_document_access_is_denied(self):
        erik = self.client("erik@example.test")
        status, payload = erik.request("GET", "/api/documents/DOC-REMUNERATION-RESTREINT")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_related_objects_are_protected_by_document_permission(self):
        with db.connection(self.db_path) as conn:
            task_id = conn.execute("SELECT id FROM tasks WHERE document_id = 'DOC-REMUNERATION-RESTREINT'").fetchone()["id"]
            audit_id = conn.execute("SELECT id FROM audits WHERE document_id = 'DOC-REMUNERATION-RESTREINT'").fetchone()["id"]
            issue_id = conn.execute("SELECT id FROM issues WHERE document_id = 'DOC-REMUNERATION-RESTREINT'").fetchone()["id"]
        erik = self.client("erik@example.test")
        self.assertEqual(erik.request("POST", f"/api/tasks/{task_id}/status", {"status": "DONE"})[0], 403)
        self.assertEqual(erik.request("POST", f"/api/audits/{audit_id}/claim", {})[0], 403)
        self.assertEqual(erik.request("POST", f"/api/issues/{issue_id}/comments", {"body": "Tentative"})[0], 403)

    def test_search_and_dashboard_do_not_leak_restricted_document(self):
        erik = self.client("erik@example.test")
        status, payload = erik.request("GET", "/api/documents?search=r%C3%A9mun%C3%A9rations&include_archived=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 0)
        status, dashboard = erik.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertNotIn("Politique rémunérations sensibles", json.dumps(dashboard, ensure_ascii=False))

    def test_mutation_is_denied_even_without_ui_button(self):
        erik = self.client("erik@example.test")
        status, payload = erik.request("POST", "/api/documents/DOC-REMUNERATION-RESTREINT/archive", {"reason": "URL forgée"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_sensitive_fields_cannot_change_owner_through_version_update(self):
        clara = self.client("clara@example.test")
        with db.connection(self.db_path) as conn:
            version_id = conn.execute("SELECT id FROM document_versions WHERE document_id = 'DOC-PAIE-DRAFT'").fetchone()["id"]
            owner_before = conn.execute("SELECT owner_id FROM documents WHERE id = 'DOC-PAIE-DRAFT'").fetchone()["owner_id"]
        status, _ = clara.request("POST", f"/api/versions/{version_id}/update", {"title": "Titre modifié", "owner_id": 999})
        self.assertEqual(status, 200)
        with db.connection(self.db_path) as conn:
            owner_after = conn.execute("SELECT owner_id FROM documents WHERE id = 'DOC-PAIE-DRAFT'").fetchone()["owner_id"]
        self.assertEqual(owner_after, owner_before)

    def test_access_revocation_is_effective_on_next_request(self):
        alice = self.client("alice@example.test")
        erik = self.client("erik@example.test")
        with db.connection(self.db_path) as conn:
            erik_id = conn.execute("SELECT id FROM users WHERE email = 'erik@example.test'").fetchone()["id"]
        status, _ = alice.request(
            "POST",
            "/api/documents/DOC-REMUNERATION-RESTREINT/grant-access",
            {"user_id": erik_id, "can_comment": True, "reason": "Test révocation"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(erik.request("GET", "/api/documents/DOC-REMUNERATION-RESTREINT")[0], 200)
        with db.connection(self.db_path) as conn:
            grant_id = conn.execute(
                """
                SELECT id FROM document_access_grants
                WHERE document_id = 'DOC-REMUNERATION-RESTREINT' AND user_id = ? AND revoked_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (erik_id,),
            ).fetchone()["id"]
        self.assertEqual(alice.request("POST", f"/api/grants/{grant_id}/revoke", {"reason": "Fin du test"})[0], 200)
        self.assertEqual(erik.request("GET", "/api/documents/DOC-REMUNERATION-RESTREINT")[0], 403)

    def test_html_comment_is_returned_as_json_text(self):
        bruno = self.client("bruno@example.test")
        with db.connection(self.db_path) as conn:
            issue_id = conn.execute("SELECT id FROM issues WHERE document_id = 'DOC-REMUNERATION-RESTREINT'").fetchone()["id"]
        body = "<script>alert('x')</script>"
        status, payload = bruno.request("POST", f"/api/issues/{issue_id}/comments", {"body": body})
        self.assertEqual(status, 200)
        self.assertIn("DOC-REMUNERATION-RESTREINT", json.dumps(payload))
        with db.connection(self.db_path) as conn:
            stored = conn.execute("SELECT body FROM issue_comments WHERE issue_id = ? ORDER BY id DESC LIMIT 1", (issue_id,)).fetchone()["body"]
        self.assertEqual(stored, body)


if __name__ == "__main__":
    unittest.main()
