from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import auth, db, permissions as perm, services
from .config import APP_NAME, APP_TITLE, public_config


WEB_DIR = Path(__file__).resolve().parent / "web"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class LoginLimiter:
    def __init__(self, max_attempts: int = 8, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.failures: dict[str, list[float]] = {}

    def is_limited(self, key: str) -> bool:
        now = time.time()
        attempts = [item for item in self.failures.get(key, []) if now - item < self.window_seconds]
        self.failures[key] = attempts
        return len(attempts) >= self.max_attempts

    def register_failure(self, key: str) -> None:
        self.failures.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)


class BigDServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], db_path: str | os.PathLike[str]):
        super().__init__(server_address, handler)
        self.db_path = str(db_path)
        self.login_limiter = LoginLimiter()


class Handler(BaseHTTPRequestHandler):
    server: BigDServer

    def log_message(self, fmt: str, *args: Any) -> None:
        if (os.environ.get("BIGD_ACCESS_LOG") or os.environ.get("DOCUMENT_GOV_ACCESS_LOG")) == "1":
            super().log_message(fmt, *args)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_default_headers()
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed.path, parse_qs(parsed.query))
        else:
            self._serve_static(parsed.path)

    def _send_default_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_default_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, code: str = "error") -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise services.AppError(400, "JSON invalide.", "invalid_json") from exc
        if not isinstance(value, dict):
            raise services.AppError(400, "Le corps JSON doit être un objet.", "invalid_json")
        return value

    def _client_key(self, email: str) -> str:
        host = self.client_address[0] if self.client_address else "unknown"
        return f"{host}:{email.lower()}"

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/config" and self.command == "GET":
                return self._json(200, public_config())
            if path == "/api/login" and self.command == "POST":
                return self._login()
            if path == "/api/dev/users" and self.command == "GET":
                return self._dev_users()

            with db.connection(self.server.db_path) as conn:
                session, user = auth.load_session(conn, self.headers.get("Cookie"))
                if user is None or session is None:
                    return self._error(401, "Authentification requise.", "unauthenticated")
                if self.command in MUTATING:
                    csrf = self.headers.get("X-CSRF-Token")
                    if not csrf or csrf != session["csrf_token"]:
                        services.emit_log(conn, user["id"], "csrf_failed", "request", path, technical=True)
                        conn.commit()
                        return self._error(403, "Jeton CSRF invalide.", "csrf")
                if path == "/api/logout" and self.command == "POST":
                    auth.destroy_session(conn, self.headers.get("Cookie"))
                    return self._json(200, {"ok": True}, {"Set-Cookie": auth.expired_cookie_header()})
                result = self._route(conn, user, path, query)
                return self._json(200, result)
        except services.AppError as exc:
            self._error(exc.status, exc.message, exc.code)
        except Exception as exc:
            if (os.environ.get("BIGD_DEBUG") or os.environ.get("DOCUMENT_GOV_DEBUG")) == "1":
                raise
            self._error(500, f"Erreur serveur: {exc}", "server_error")

    def _login(self) -> None:
        payload = self._read_json()
        email = (payload.get("email") or "").strip().lower()
        password = payload.get("password") or ""
        key = self._client_key(email)
        if self.server.login_limiter.is_limited(key):
            return self._error(429, "Trop de tentatives. Réessayez plus tard.", "rate_limited")
        with db.connection(self.server.db_path) as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
            if user is None or not auth.verify_password(password, user["password_hash"]):
                self.server.login_limiter.register_failure(key)
                return self._error(401, "Identifiants invalides.", "bad_credentials")
            token, csrf = auth.create_session(conn, user["id"])
            conn.commit()
            self.server.login_limiter.reset(key)
            safe_user = {"id": user["id"], "name": user["name"], "email": user["email"]}
            self._json(
                200,
                {"user": safe_user, "csrf_token": csrf, "config": public_config()},
                {"Set-Cookie": auth.cookie_header_for_session(token)},
            )

    def _dev_users(self) -> None:
        if os.environ.get("APP_ENV") == "production" or os.environ.get("DEMO_USER_SELECTOR", "1") != "1":
            return self._error(404, "Indisponible.", "not_found")
        with db.connection(self.server.db_path) as conn:
            users = conn.execute("SELECT id, name, email FROM users WHERE active = 1 ORDER BY name").fetchall()
            self._json(200, {"users": [dict(row) for row in users], "demo_password": "demo1234"})

    def _route(self, conn: db.sqlite3.Connection, user: db.sqlite3.Row, path: str, query: dict[str, list[str]]) -> Any:
        payload = self._read_json() if self.command in MUTATING else {}
        if path == "/api/session" and self.command == "GET":
            return {
                "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
                "config": public_config(),
                "csrf_token": conn.execute(
                    "SELECT csrf_token FROM sessions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (user["id"],),
                ).fetchone()["csrf_token"],
            }
        if path == "/api/reference" and self.command == "GET":
            return services.list_reference_data(conn, user)
        if path == "/api/dashboard" and self.command == "GET":
            scope = (query.get("scope") or ["mine"])[-1]
            return services.dashboard(conn, user, scope)
        if path == "/api/work-items" and self.command == "GET":
            kind = (query.get("kind") or [""])[-1]
            return services.list_work_items(conn, user, kind)
        if path == "/api/documents" and self.command == "GET":
            filters = {key: values[-1] for key, values in query.items()}
            return services.list_documents(conn, user, filters)
        if path == "/api/audits" and self.command == "GET":
            filters = {key: values[-1] for key, values in query.items()}
            return services.list_audits(conn, user, filters)
        if path == "/api/tasks" and self.command == "GET":
            filters = {key: values[-1] for key, values in query.items()}
            return services.list_tasks(conn, user, filters)
        if path == "/api/documents" and self.command == "POST":
            return services.create_document(conn, user, payload)
        if path == "/api/notifications" and self.command == "GET":
            filters = {key: values[-1] for key, values in query.items()}
            items = services.list_notifications(conn, user, filters)
            return {"items": items, "total": len(items), "unread": services.unread_notification_count(conn, user)}
        if path == "/api/notifications/mark-all-read" and self.command == "POST":
            return services.mark_all_notifications_read(conn, user)
        if path == "/api/scheduler/run" and self.command == "POST":
            if not perm.can_administer(conn, user):
                raise services.AppError(403, "Planificateur réservé aux administrateurs.", "forbidden")
            return services.run_scheduler(conn)
        if path == "/api/admin/users" and self.command == "POST":
            return services.admin_create_user(conn, user, payload, auth.hash_password(payload.get("password") or "ChangeMe123!"))

        patterns: list[tuple[str, str, Callable[..., Any]]] = [
            ("GET", r"^/api/documents/([^/]+)$", lambda document_id: services.document_detail(conn, user, document_id)),
            ("GET", r"^/api/audits/(\d+)$", lambda audit_id: services.audit_detail(conn, user, int(audit_id))),
            ("POST", r"^/api/documents/([^/]+)/proposals$", lambda document_id: services.create_proposal(conn, user, document_id, payload)),
            ("POST", r"^/api/documents/([^/]+)/issues$", lambda document_id: services.create_issue(conn, user, document_id, payload)),
            ("POST", r"^/api/documents/([^/]+)/audits$", lambda document_id: services.open_manual_audit(conn, user, document_id)),
            ("POST", r"^/api/documents/([^/]+)/review-only$", lambda document_id: services.review_only_validation(conn, user, document_id, payload)),
            ("POST", r"^/api/documents/([^/]+)/archive$", lambda document_id: services.archive_document(conn, user, document_id, payload)),
            ("POST", r"^/api/documents/([^/]+)/grant-access$", lambda document_id: services.grant_access(conn, user, document_id, payload)),
            ("POST", r"^/api/documents/([^/]+)/transfer-owner$", lambda document_id: services.transfer_owner(conn, user, document_id, payload)),
            ("POST", r"^/api/grants/(\d+)/revoke$", lambda grant_id: services.revoke_access(conn, user, int(grant_id), payload.get("reason") or "")),
            ("POST", r"^/api/versions/(\d+)/update$", lambda version_id: services.update_version(conn, user, int(version_id), payload)),
            ("POST", r"^/api/versions/(\d+)/submit$", lambda version_id: services.submit_version(conn, user, int(version_id), payload.get("reviewer_id"))),
            ("POST", r"^/api/versions/(\d+)/request-correction$", lambda version_id: services.request_correction(conn, user, int(version_id), payload.get("comment") or "")),
            ("POST", r"^/api/versions/(\d+)/request-intervention$", lambda version_id: services.request_intervention(conn, user, int(version_id), payload)),
            ("POST", r"^/api/versions/(\d+)/approve-review$", lambda version_id: services.approve_review(conn, user, int(version_id), payload.get("comment") or "")),
            ("POST", r"^/api/versions/(\d+)/publish$", lambda version_id: services.publish_version(conn, user, int(version_id))),
            ("POST", r"^/api/tasks/(\d+)/status$", lambda task_id: services.update_task_status(conn, user, int(task_id), payload.get("status") or "", payload.get("reason") or "")),
            ("POST", r"^/api/audits/(\d+)/claim$", lambda audit_id: services.claim_audit(conn, user, int(audit_id))),
            ("POST", r"^/api/audits/(\d+)/submit$", lambda audit_id: services.submit_audit(conn, user, int(audit_id), payload)),
            ("POST", r"^/api/audits/(\d+)/decide$", lambda audit_id: services.decide_audit(conn, user, int(audit_id), payload)),
            ("POST", r"^/api/issues/(\d+)/resolve$", lambda issue_id: services.resolve_issue(conn, user, int(issue_id), payload)),
            ("POST", r"^/api/issues/(\d+)/comments$", lambda issue_id: services.add_issue_comment(conn, user, int(issue_id), payload.get("body") or "")),
            ("POST", r"^/api/notifications/(\d+)/read$", lambda notification_id: services.mark_notification_read(conn, user, int(notification_id))),
            ("POST", r"^/api/notifications/(\d+)/unread$", lambda notification_id: services.mark_notification_unread(conn, user, int(notification_id))),
            ("POST", r"^/api/flags/(\d+)/snooze$", lambda flag_id: services.snooze_flag(conn, user, int(flag_id), payload)),
        ]
        for method, pattern, callback in patterns:
            match = re.match(pattern, path)
            if method == self.command and match:
                return callback(*match.groups())
        raise services.AppError(404, "Route API introuvable.", "not_found")

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        requested = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(requested).startswith(str(WEB_DIR.resolve())) or not requested.exists() or requested.is_dir():
            self._error(404, "Page introuvable.", "not_found")
            return
        body = requested.read_bytes()
        mime = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._send_default_headers()
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, db_path: str | os.PathLike[str]) -> BigDServer:
    return BigDServer((host, port), Handler, db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Serveur {APP_TITLE}")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--db", default=str(db.db_path_from_env()))
    parser.add_argument("--migrate", action="store_true", help="Applique les migrations avant de démarrer")
    args = parser.parse_args(argv)
    if args.migrate:
        db.migrate(args.db)
    server = make_server(args.host, args.port, args.db)
    print(f"{APP_NAME} lancé sur http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
