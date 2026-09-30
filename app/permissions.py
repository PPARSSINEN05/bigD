from __future__ import annotations

from sqlite3 import Connection, Row


ADMIN = "ADMIN"
CONTRIBUTOR = "CONTRIBUTOR"
AUDITOR = "AUDITOR"
REVIEWER = "REVIEWER"
DATA_OWNER = "DATA_OWNER"


def roles(conn: Connection, user_id: int, team_id: int | None = None) -> set[str]:
    params: list[object] = [user_id]
    sql = "SELECT role FROM role_assignments WHERE user_id = ? AND active = 1"
    if team_id is None:
        sql += " AND team_id IS NULL"
    else:
        sql += " AND (team_id IS NULL OR team_id = ?)"
        params.append(team_id)
    return {row["role"] for row in conn.execute(sql, params).fetchall()}


def is_admin(conn: Connection, user_id: int) -> bool:
    return ADMIN in roles(conn, user_id, None)


def is_member(conn: Connection, user_id: int, team_id: int) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM team_memberships
            WHERE user_id = ? AND team_id = ? AND active = 1
            """,
            (user_id, team_id),
        ).fetchone()
        is not None
    )


def document_by_id(conn: Connection, document_id: str) -> Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()


def user_by_id(conn: Connection, user_id: int) -> Row | None:
    return conn.execute(
        "SELECT id, name, email, active, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def _grant_allows(conn: Connection, user_id: int, document_id: str, permission: str) -> bool:
    sql = f"""
        SELECT 1
        FROM document_access_grants g
        LEFT JOIN team_memberships tm
            ON g.team_id = tm.team_id AND tm.user_id = ? AND tm.active = 1
        WHERE g.document_id = ?
          AND g.revoked_at IS NULL
          AND g.{permission} = 1
          AND (g.user_id = ? OR tm.id IS NOT NULL)
        LIMIT 1
    """
    return conn.execute(sql, (user_id, document_id, user_id)).fetchone() is not None


def has_workflow_access(conn: Connection, user_id: int, doc: Row) -> bool:
    if doc["owner_id"] == user_id:
        return True
    if _grant_allows(conn, user_id, doc["id"], "can_view"):
        return True
    if conn.execute(
        """
        SELECT 1 FROM document_versions
        WHERE document_id = ? AND author_id = ? AND status IN ('DRAFT','CHALLENGE')
        LIMIT 1
        """,
        (doc["id"], user_id),
    ).fetchone():
        return True
    if conn.execute(
        """
        SELECT 1 FROM reviews
        WHERE document_id = ? AND reviewer_id = ? AND status != 'CANCELLED'
        LIMIT 1
        """,
        (doc["id"], user_id),
    ).fetchone():
        return True
    if conn.execute(
        """
        SELECT 1 FROM tasks
        WHERE document_id = ? AND assignee_id = ? AND status IN ('TODO','IN_PROGRESS')
        LIMIT 1
        """,
        (doc["id"], user_id),
    ).fetchone():
        return True
    if conn.execute(
        """
        SELECT 1 FROM audits
        WHERE document_id = ? AND assignee_id = ? AND status NOT IN ('CLOSED','CANCELLED')
        LIMIT 1
        """,
        (doc["id"], user_id),
    ).fetchone():
        return True
    return False


def can_view_document(conn: Connection, user: Row, doc: Row) -> bool:
    if user is None or doc is None or user["active"] != 1:
        return False
    user_id = user["id"]
    if doc["current_version_id"] is None:
        return has_workflow_access(conn, user_id, doc)
    if doc["owner_id"] == user_id:
        return True
    if _grant_allows(conn, user_id, doc["id"], "can_view"):
        return True
    confidentiality = doc["confidentiality"]
    if confidentiality == "INTERNE":
        return True
    if confidentiality == "EQUIPE":
        return is_member(conn, user_id, doc["team_id"])
    return False


def can_comment(conn: Connection, user: Row, doc: Row) -> bool:
    return can_view_document(conn, user, doc) and (
        _grant_allows(conn, user["id"], doc["id"], "can_comment")
        or doc["owner_id"] == user["id"]
        or doc["confidentiality"] in ("INTERNE", "EQUIPE")
    )


def can_create_document(conn: Connection, user: Row, team_id: int) -> bool:
    user_roles = roles(conn, user["id"], team_id)
    return is_member(conn, user["id"], team_id) and (
        CONTRIBUTOR in user_roles or DATA_OWNER in user_roles or ADMIN in user_roles
    )


def can_contribute(conn: Connection, user: Row, doc: Row) -> bool:
    if doc["owner_id"] == user["id"]:
        return True
    if _grant_allows(conn, user["id"], doc["id"], "can_contribute"):
        return True
    if CONTRIBUTOR in roles(conn, user["id"], doc["team_id"]) and is_member(conn, user["id"], doc["team_id"]):
        return True
    return (
        conn.execute(
            """
            SELECT 1 FROM tasks
            WHERE document_id = ? AND assignee_id = ? AND status IN ('TODO','IN_PROGRESS')
            LIMIT 1
            """,
            (doc["id"], user["id"]),
        ).fetchone()
        is not None
    )


def can_review(conn: Connection, user: Row, doc: Row) -> bool:
    if doc["owner_id"] == user["id"]:
        return True
    if _grant_allows(conn, user["id"], doc["id"], "can_review"):
        return True
    if REVIEWER in roles(conn, user["id"], doc["team_id"]) and is_member(conn, user["id"], doc["team_id"]):
        return True
    return (
        conn.execute(
            "SELECT 1 FROM reviews WHERE document_id = ? AND reviewer_id = ? AND status = 'PENDING' LIMIT 1",
            (doc["id"], user["id"]),
        ).fetchone()
        is not None
    )


def can_manage_document(conn: Connection, user: Row, doc: Row) -> bool:
    if doc["owner_id"] == user["id"]:
        return True
    return _grant_allows(conn, user["id"], doc["id"], "can_manage")


def can_audit(conn: Connection, user: Row, doc: Row) -> bool:
    if can_manage_document(conn, user, doc):
        return True
    if _grant_allows(conn, user["id"], doc["id"], "can_audit"):
        return True
    if AUDITOR in roles(conn, user["id"], doc["auditor_team_id"]) and is_member(conn, user["id"], doc["auditor_team_id"]):
        return True
    return (
        conn.execute(
            """
            SELECT 1 FROM audits
            WHERE document_id = ? AND assignee_id = ? AND status NOT IN ('CLOSED','CANCELLED')
            LIMIT 1
            """,
            (doc["id"], user["id"]),
        ).fetchone()
        is not None
    )


def can_administer(conn: Connection, user: Row) -> bool:
    return is_admin(conn, user["id"])
