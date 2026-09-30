from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from sqlite3 import Connection, Row

from .db import parse_utc, utcnow


PASSWORD_ITERATIONS = 260_000
SESSION_COOKIE = "docgov_session"


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PASSWORD_ITERATIONS) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: Connection, user_id: int, hours: int = 10) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(UTC).replace(microsecond=0)
    conn.execute(
        """
        INSERT INTO sessions(token_hash, user_id, csrf_token, created_at, expires_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            hash_token(token),
            user_id,
            csrf,
            now.isoformat().replace("+00:00", "Z"),
            (now + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
            now.isoformat().replace("+00:00", "Z"),
        ),
    )
    return token, csrf


def parse_cookie_header(cookie_header: str | None) -> dict[str, str]:
    if not cookie_header:
        return {}
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return {key: morsel.value for key, morsel in cookie.items()}


def load_session(conn: Connection, cookie_header: str | None) -> tuple[Row | None, Row | None]:
    token = parse_cookie_header(cookie_header).get(SESSION_COOKIE)
    if not token:
        return None, None
    session = conn.execute(
        "SELECT * FROM sessions WHERE token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if session is None:
        return None, None
    if parse_utc(session["expires_at"]) <= datetime.now(UTC):
        conn.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
        conn.commit()
        return None, None
    user = conn.execute(
        "SELECT id, name, email, active, created_at FROM users WHERE id = ? AND active = 1",
        (session["user_id"],),
    ).fetchone()
    if user is None:
        return None, None
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (utcnow(), session["id"]))
    conn.commit()
    return session, user


def destroy_session(conn: Connection, cookie_header: str | None) -> None:
    token = parse_cookie_header(cookie_header).get(SESSION_COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
        conn.commit()


def cookie_header_for_session(token: str, *, max_age: int = 36000) -> str:
    return f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"


def expired_cookie_header() -> str:
    return f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
