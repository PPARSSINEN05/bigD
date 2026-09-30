from __future__ import annotations

import json
import unicodedata
import uuid
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, IntegrityError, Row
from typing import Any

from . import permissions as perm
from .config import APP_NAME
from .db import parse_utc, row_to_dict, rows_to_dicts, transaction, utcnow


class AppError(Exception):
    def __init__(self, status: int, message: str, code: str = "error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


ACTIVE_AUDIT_STATUSES = {
    "TO_DO",
    "IN_PROGRESS",
    "AWAITING_OWNER_DECISION",
    "REMEDIATION_IN_PROGRESS",
    "AWAITING_FINAL_VALIDATION",
}


NOTIFICATION_LABELS = {
    "REVIEW_REQUEST": "Review request",
    "INTERVENTION_REQUEST": "Intervention request",
    "TASK_ASSIGNED": "Task assigned",
    "AUDIT_DUE": "Audit due",
    "AUDIT_OVERDUE": "Audit overdue",
    "ISSUE_CREATED": "New issue",
    "AUDIT_RESULT": "Decision required",
    "CORRECTION_REQUEST": "Corrections requested",
    "REVIEW_APPROVED": "Review approved",
    "VALIDATION": "Validation recorded",
    "PUBLISHED": "Publication approved",
}


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _safe_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _safe_choice(value: Any, allowed: set[str], default: str = "") -> str:
    text = str(value or "")
    return text if text in allowed else default


def _date_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_utc(value)
    except ValueError:
        return None


def _overdue_days(value: str | None) -> int:
    due = _date_or_none(value)
    if due is None:
        return 0
    delta = datetime.now(UTC) - due
    return max(0, delta.days)


def _version_text(row: Row | dict | None, *, target: bool = False) -> str:
    if not row:
        return "Unpublished"
    major_key = "target_major" if target else "major"
    minor_key = "target_minor" if target else "minor"
    return f"v{row[major_key]}.{row[minor_key]}"


def _proposal_kind(row: Row | dict | None) -> str:
    if not row:
        return ""
    if row["change_type"] == "EDIT":
        return "Minor update"
    if row["change_type"] == "NEW_VERSION":
        return "Major version"
    return "First publication"


def _status_word(status: str) -> str:
    return {
        "DRAFT": "Draft",
        "CHALLENGE": "In review",
        "UP": "Published",
        "CANCELLED": "Cancelled",
        "TO_DO": "To do",
        "IN_PROGRESS": "In progress",
        "AWAITING_OWNER_DECISION": "Owner decision",
        "REMEDIATION_IN_PROGRESS": "Remediation",
        "AWAITING_FINAL_VALIDATION": "Final validation",
        "CLOSED": "Closed",
        "OPEN": "Open",
        "UNDER_REVIEW": "To decide",
        "ACCEPTED": "Accepted",
        "RESOLVED": "Resolved",
        "DISMISSED": "Dismissed",
    }.get(status, status)


def add_frequency(last_validation_at: str, value: int, unit: str) -> str:
    base = parse_utc(last_validation_at)
    if unit == "days":
        result = base + timedelta(days=value)
    elif unit == "months":
        month_index = base.month - 1 + value
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        day = min(base.day, monthrange(year, month)[1])
        result = base.replace(year=year, month=month, day=day)
    else:
        raise AppError(400, "Unknown frequency unit.", "invalid_frequency")
    return result.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit_log(
    conn: Connection,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str | int,
    *,
    document_id: str | None = None,
    reason: str = "",
    changes: dict | None = None,
    technical: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO activity_logs(actor_id, document_id, target_type, target_id, action, reason, changes_json, technical, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id,
            document_id,
            target_type,
            str(target_id),
            action,
            reason,
            _dumps(changes or {}),
            1 if technical else 0,
            utcnow(),
        ),
    )


def _active_user(conn: Connection, user_id: int) -> Row:
    user = perm.user_by_id(conn, user_id)
    if user is None or user["active"] != 1:
        raise AppError(404, "User not found or inactive.", "user_not_found")
    return user


def _document_for_action(conn: Connection, user: Row, document_id: str, action: str = "view") -> Row:
    doc = perm.document_by_id(conn, document_id)
    if doc is None:
        raise AppError(404, "Document not found.", "document_not_found")
    if action == "view" and not perm.can_view_document(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_view", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "Access denied for this document.", "forbidden")
    if action == "manage" and not perm.can_manage_document(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_manage", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "This action is reserved for the data owner or an explicitly authorized manager.", "forbidden")
    if action == "contribute" and not perm.can_contribute(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_contribute", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "You cannot contribute to this document.", "forbidden")
    if action == "review" and not perm.can_review(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_review", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "You cannot review this document.", "forbidden")
    if action == "audit" and not perm.can_audit(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_audit", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "You cannot audit this document.", "forbidden")
    return doc


def _version_for_user(conn: Connection, user: Row, version_id: int) -> tuple[Row, Row]:
    version = conn.execute("SELECT * FROM document_versions WHERE id = ?", (version_id,)).fetchone()
    if version is None:
        raise AppError(404, "Version not found.", "version_not_found")
    doc = _document_for_action(conn, user, version["document_id"], "view")
    if version["status"] != "UP" and not perm.has_workflow_access(conn, user["id"], doc):
        raise AppError(403, "Access denied for this proposal.", "forbidden")
    return version, doc


def notify(
    conn: Connection,
    recipient_id: int,
    document_id: str | None,
    event_key: str,
    notification_type: str,
    title: str,
    body: str,
    url: str = "",
) -> None:
    if document_id is not None:
        recipient = perm.user_by_id(conn, recipient_id)
        doc = perm.document_by_id(conn, document_id)
        if recipient is None or doc is None or not perm.can_view_document(conn, recipient, doc):
            return
    conn.execute(
        """
        INSERT OR IGNORE INTO notifications(recipient_id, document_id, event_key, type, title, body, url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (recipient_id, document_id, event_key, notification_type, title, body, url, utcnow()),
    )


def list_reference_data(conn: Connection, user: Row) -> dict:
    is_admin_user = perm.can_administer(conn, user)
    if is_admin_user:
        teams = rows_to_dicts(conn.execute("SELECT * FROM teams ORDER BY name").fetchall())
        users = rows_to_dicts(conn.execute("SELECT id, name, email, active, created_at FROM users ORDER BY name").fetchall())
    else:
        teams = rows_to_dicts(
            conn.execute(
                """
                SELECT t.* FROM teams t
                JOIN team_memberships tm ON tm.team_id = t.id
                WHERE tm.user_id = ? AND tm.active = 1
                ORDER BY t.name
                """,
                (user["id"],),
            ).fetchall()
        )
        users = rows_to_dicts(
            conn.execute(
                """
                SELECT DISTINCT u.id, u.name, u.email, u.active, u.created_at
                FROM users u
                JOIN team_memberships tm_user ON tm_user.user_id = u.id AND tm_user.active = 1
                JOIN team_memberships tm_me ON tm_me.team_id = tm_user.team_id AND tm_me.user_id = ? AND tm_me.active = 1
                WHERE u.active = 1
                ORDER BY u.name
                """,
                (user["id"],),
            ).fetchall()
        )
    memberships = rows_to_dicts(conn.execute("SELECT * FROM team_memberships").fetchall())
    roles = rows_to_dicts(conn.execute("SELECT * FROM role_assignments WHERE active = 1").fetchall())
    return {
        "teams": teams,
        "users": users,
        "memberships": memberships,
        "roles": roles,
        "is_admin": is_admin_user,
    }


def _active_proposal(conn: Connection, document_id: str) -> Row | None:
    return conn.execute(
        """
        SELECT v.*, u.name AS author_name
        FROM document_versions v
        JOIN users u ON u.id = v.author_id
        WHERE v.document_id = ? AND v.status IN ('DRAFT','CHALLENGE')
        ORDER BY v.created_at DESC
        LIMIT 1
        """,
        (document_id,),
    ).fetchone()


def _active_audit(conn: Connection, document_id: str) -> Row | None:
    placeholders = ",".join("?" for _ in ACTIVE_AUDIT_STATUSES)
    return conn.execute(
        f"""
        SELECT a.*, u.name AS assignee_name
        FROM audits a
        LEFT JOIN users u ON u.id = a.assignee_id
        WHERE a.document_id = ? AND a.status IN ({placeholders})
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        (document_id, *ACTIVE_AUDIT_STATUSES),
    ).fetchone()


def _current_version(conn: Connection, doc: Row | dict) -> Row | None:
    if not doc["current_version_id"]:
        return None
    return conn.execute("SELECT * FROM document_versions WHERE id = ?", (doc["current_version_id"],)).fetchone()


def _has_active_overdue_flag(conn: Connection, document_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM flags WHERE document_id = ? AND active = 1 AND flag_type = 'AUDIT_OVERDUE' LIMIT 1",
            (document_id,),
        ).fetchone()
        is not None
    )


def _document_search_text(item: dict) -> str:
    parts = [
        item.get("id"),
        item.get("title"),
        item.get("description"),
        item.get("category"),
        item.get("team_name"),
        item.get("owner_name"),
        item.get("confidentiality"),
        item.get("source_location_type"),
        item.get("document_format"),
        " ".join(item.get("tags") or []),
    ]
    return _normalize(" ".join(str(part or "") for part in parts))


def _enrich_document_item(conn: Connection, row: Row) -> dict:
    item = row_to_dict(row)
    item["tags"] = _loads(row["tags_json"], [])
    current = _current_version(conn, row)
    proposal = _active_proposal(conn, row["id"])
    audit = _active_audit(conn, row["id"])
    active_flag = _has_active_overdue_flag(conn, row["id"])
    now = datetime.now(UTC)
    due = _date_or_none(row["next_audit_due_at"])
    overdue = bool(row["current_version_id"] and row["archived_at"] is None and ((due and due < now) or active_flag))
    upcoming = bool(due and now <= due <= now + timedelta(days=30))

    item["current_version"] = row_to_dict(current)
    item["current_major"] = current["major"] if current else None
    item["current_minor"] = current["minor"] if current else None
    item["active_proposal"] = row_to_dict(proposal)
    item["active_audit"] = row_to_dict(audit)
    item["has_active_proposal"] = proposal is not None
    item["has_active_audit"] = audit is not None
    item["has_overdue_flag"] = active_flag
    item["is_audit_overdue"] = overdue
    item["overdue_days"] = _overdue_days(row["next_audit_due_at"]) if overdue else 0

    if row["archived_at"] is not None:
        item["publication_state"] = "archived"
        item["publication_label"] = f"Archived · Last version {_version_text(current)}"
    elif current:
        item["publication_state"] = "published"
        item["publication_label"] = f"Published · {_version_text(current)}"
    elif proposal and proposal["status"] == "CHALLENGE":
        item["publication_state"] = "unpublished"
        item["publication_label"] = "In review · First publication"
    elif proposal:
        item["publication_state"] = "draft"
        item["publication_label"] = "Initial draft · Unpublished"
    else:
        item["publication_state"] = "unpublished"
        item["publication_label"] = "Unpublished"

    if proposal:
        item["proposal_state"] = proposal["status"].lower()
        state_word = "In review" if proposal["status"] == "CHALLENGE" else "Draft"
        if proposal["change_type"] == "NEW_VERSION" and proposal["status"] == "DRAFT":
            state_word = "In preparation"
        item["proposal_label"] = f"{_proposal_kind(proposal)} {_version_text(proposal, target=True)} · {state_word}"
    else:
        item["proposal_state"] = "none"
        item["proposal_label"] = "No active proposal"

    if row["archived_at"] is not None:
        item["audit_state"] = "archived"
        item["audit_label"] = "Audit stopped"
    elif audit and audit["status"] in ("TO_DO", "IN_PROGRESS"):
        item["audit_state"] = "in_progress"
        prefix = "Audit in progress" if audit["status"] == "IN_PROGRESS" else "Audit due"
        item["audit_label"] = f"{prefix} · {item['overdue_days']} d overdue" if overdue else prefix
    elif audit and audit["status"] == "AWAITING_OWNER_DECISION":
        item["audit_state"] = "decision"
        item["audit_label"] = "Validation required"
    elif audit:
        item["audit_state"] = "in_progress"
        item["audit_label"] = _status_word(audit["status"])
    elif overdue:
        item["audit_state"] = "overdue"
        item["audit_label"] = f"Audit overdue by {item['overdue_days']} days"
    elif upcoming:
        item["audit_state"] = "upcoming"
        item["audit_label"] = "Audit due soon"
    elif current:
        item["audit_state"] = "up_to_date"
        item["audit_label"] = "Audit up to date"
    else:
        item["audit_state"] = "not_planned"
        item["audit_label"] = "Audit not planned"

    source = current or proposal
    item["source_location_type"] = source["source_location_type"] if source else ""
    item["document_format"] = source["document_format"] if source else ""
    item["status_summary"] = {
        "publication": item["publication_label"],
        "proposal": item["proposal_label"],
        "audit": item["audit_label"],
    }
    return item


def list_documents(conn: Connection, user: Row, filters: dict[str, Any] | None = None) -> dict:
    filters = filters or {}
    search = (filters.get("search") or filters.get("q") or "").strip()
    normalized_search = _normalize(search)
    include_archived = str(filters.get("include_archived", "")).lower() in ("1", "true", "yes")
    page = _safe_int(filters.get("page"), 1, minimum=1)
    per_page = _safe_int(filters.get("per_page"), 12, minimum=5, maximum=50)
    sort = _safe_choice(str(filters.get("sort") or "relevance"), {"relevance", "title", "last_validation", "next_due"}, "relevance")

    rows = conn.execute(
        """
        SELECT d.*, t.name AS team_name, u.name AS owner_name
        FROM documents d
        JOIN teams t ON t.id = d.team_id
        JOIN users u ON u.id = d.owner_id
        ORDER BY d.title ASC
        """
    ).fetchall()

    visible = []
    for row in rows:
        if not perm.can_view_document(conn, user, row):
            continue
        item = _enrich_document_item(conn, row)
        if not include_archived and item["archived_at"] is not None:
            continue
        if filters.get("team_id") and str(item["team_id"]) != str(filters["team_id"]):
            continue
        if filters.get("owner_id") and str(item["owner_id"]) != str(filters["owner_id"]):
            continue
        if filters.get("category") and _normalize(item["category"]) != _normalize(str(filters["category"])):
            continue
        if filters.get("tag") and _normalize(str(filters["tag"])) not in {_normalize(tag) for tag in item["tags"]}:
            continue
        if filters.get("confidentiality") and item["confidentiality"] != filters["confidentiality"]:
            continue
        if filters.get("publication_state") and item["publication_state"] != filters["publication_state"]:
            continue
        if filters.get("proposal_state") and item["proposal_state"] != filters["proposal_state"]:
            continue
        if filters.get("audit_state") and item["audit_state"] != filters["audit_state"]:
            continue
        if filters.get("source_type") and item["source_location_type"] != filters["source_type"]:
            continue
        if filters.get("document_format") and item["document_format"] != filters["document_format"]:
            continue
        quick = filters.get("quick") or ""
        if quick == "mine" and item["owner_id"] != user["id"]:
            continue
        if quick == "audit" and item["audit_state"] not in ("overdue", "in_progress", "decision", "upcoming"):
            continue
        if quick == "preparation" and item["proposal_state"] == "none":
            continue
        if quick == "archives" and item["archived_at"] is None:
            continue
        if normalized_search and normalized_search not in _document_search_text(item):
            continue
        item["_relevance"] = 0
        if normalized_search:
            title = _normalize(item["title"])
            item["_relevance"] = (40 if title.startswith(normalized_search) else 0) + (20 if normalized_search in title else 0)
        visible.append(item)

    if sort == "title":
        visible.sort(key=lambda item: _normalize(item["title"]))
    elif sort == "last_validation":
        visible.sort(key=lambda item: item["last_validation_at"] or "", reverse=True)
    elif sort == "next_due":
        visible.sort(key=lambda item: item["next_audit_due_at"] or "9999")
    else:
        visible.sort(key=lambda item: (-item["_relevance"], _normalize(item["title"])))

    total = len(visible)
    start = (page - 1) * per_page
    items = [{k: v for k, v in item.items() if k != "_relevance"} for item in visible[start : start + per_page]]
    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "filters": {key: value for key, value in filters.items() if value not in ("", None)},
        "sort": sort,
    }


def _visible_documents(conn: Connection, user: Row, include_archived: bool = False) -> list[dict]:
    return list_documents(conn, user, {"per_page": 1000, "include_archived": "1" if include_archived else ""})["items"]


def _blocking_task_count(conn: Connection, document_id: str, version_id: int | None = None) -> int:
    params: list[Any] = [document_id]
    condition = ""
    if version_id:
        condition = "AND (version_id = ? OR version_id IS NULL)"
        params.append(version_id)
    return conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM tasks
        WHERE document_id = ? {condition}
          AND blocking = 1
          AND status NOT IN ('DONE','CANCELLED')
        """,
        params,
    ).fetchone()["c"]


def _work_items(conn: Connection, user: Row, kind: str | None = None) -> list[dict]:
    docs = _visible_documents(conn, user, include_archived=False)
    doc_by_id = {doc["id"]: doc for doc in docs}
    items: list[dict] = []

    for doc in docs:
        if kind in (None, "overdue_audits") and doc["is_audit_overdue"]:
            audit = doc["active_audit"]
            items.append(
                {
                    "key": f"audit-overdue:{doc['id']}",
                    "kind": "audit",
                    "action": "Perform audit",
                    "action_label": "Perform audit",
                    "document_id": doc["id"],
                    "document_title": doc["title"],
                    "version": doc["publication_label"],
                    "due_at": doc["next_audit_due_at"],
                    "overdue_days": doc["overdue_days"],
                    "responsible": doc["owner_name"],
                    "blocked": False,
                    "href": f"#/audits/{audit['id']}" if audit else f"#/documents/{doc['id']}?tab=audits",
                    "priority": 10_000 + doc["overdue_days"],
                }
            )
        proposal = doc["active_proposal"]
        if proposal and proposal["status"] == "CHALLENGE" and kind in (None, "validations"):
            blocker_count = _blocking_task_count(conn, doc["id"], proposal["id"])
            if doc["owner_id"] == user["id"] or conn.execute(
                "SELECT 1 FROM reviews WHERE version_id = ? AND reviewer_id = ? AND status = 'PENDING'",
                (proposal["id"], user["id"]),
            ).fetchone():
                items.append(
                    {
                        "key": f"proposal:{proposal['id']}",
                        "kind": "validation",
                        "action": "Review proposal",
                        "action_label": "Review proposal",
                        "document_id": doc["id"],
                        "document_title": doc["title"],
                        "version": doc["proposal_label"],
                        "due_at": proposal["submitted_at"] or proposal["created_at"],
                        "overdue_days": 0,
                        "responsible": doc["owner_name"],
                        "blocked": blocker_count > 0,
                        "block_reason": f"{blocker_count} open blocking intervention" if blocker_count else "",
                        "href": f"#/documents/{doc['id']}?tab=versions&version={proposal['id']}",
                        "priority": 6_000 + blocker_count * 100,
                    }
                )
        audit = doc["active_audit"]
        if audit and audit["status"] == "AWAITING_OWNER_DECISION" and doc["owner_id"] == user["id"] and kind in (None, "validations"):
            items.append(
                {
                    "key": f"audit-decision:{audit['id']}",
                    "kind": "audit",
                    "action": "Decide treatment",
                    "action_label": "Decide treatment",
                    "document_id": doc["id"],
                    "document_title": doc["title"],
                    "version": doc["publication_label"],
                    "due_at": audit["submitted_at"] or audit["due_at"],
                    "overdue_days": _overdue_days(audit["due_at"]),
                    "responsible": doc["owner_name"],
                    "blocked": False,
                    "href": f"#/audits/{audit['id']}",
                    "priority": 8_000 + _overdue_days(audit["due_at"]),
                }
            )

    if kind in (None, "tasks"):
        for row in conn.execute(
            """
            SELECT ta.*, d.title AS document_title, u.name AS assignee_name
            FROM tasks ta
            JOIN documents d ON d.id = ta.document_id
            LEFT JOIN users u ON u.id = ta.assignee_id
            WHERE ta.assignee_id = ? AND ta.status IN ('TODO','IN_PROGRESS')
            ORDER BY ta.created_at DESC
            """,
            (user["id"],),
        ).fetchall():
            if row["document_id"] not in doc_by_id:
                continue
            doc = doc_by_id[row["document_id"]]
            items.append(
                {
                    "key": f"task:{row['id']}",
                    "kind": "correction",
                    "action": "Resume remediation" if row["status"] == "IN_PROGRESS" else "Start task",
                    "action_label": "Resume remediation" if row["status"] == "IN_PROGRESS" else "Start",
                    "document_id": row["document_id"],
                    "document_title": row["document_title"],
                    "version": doc["proposal_label"] if row["version_id"] else doc["publication_label"],
                    "due_at": row["created_at"],
                    "overdue_days": 0,
                    "responsible": row["assignee_name"] or user["name"],
                    "blocked": bool(row["blocking"]),
                    "href": f"#/tasks?task={row['id']}",
                    "priority": 7_000 + (500 if row["blocking"] else 0),
                    "task_id": row["id"],
                }
            )

    if kind in (None, "issues"):
        for row in conn.execute(
            """
            SELECT i.*, d.title AS document_title, u.name AS author_name
            FROM issues i
            JOIN documents d ON d.id = i.document_id
            LEFT JOIN users u ON u.id = i.author_id
            WHERE i.status IN ('OPEN','UNDER_REVIEW','ACCEPTED')
            ORDER BY i.created_at DESC
            """
        ).fetchall():
            doc = doc_by_id.get(row["document_id"])
            if not doc or not perm.can_manage_document(conn, user, perm.document_by_id(conn, row["document_id"])):
                continue
            items.append(
                {
                    "key": f"issue:{row['id']}",
                    "kind": "issue",
                    "action": "Decide treatment",
                    "action_label": "Review issue",
                    "document_id": row["document_id"],
                    "document_title": row["document_title"],
                    "version": doc["publication_label"],
                    "due_at": row["created_at"],
                    "overdue_days": 0,
                    "responsible": doc["owner_name"],
                    "blocked": False,
                    "href": f"#/documents/{row['document_id']}?tab=issues&issue={row['id']}",
                    "priority": 5_500,
                    "issue_id": row["id"],
                }
            )

    unique = {}
    for item in items:
        unique.setdefault(item["key"], item)
    return sorted(unique.values(), key=lambda item: (-item["priority"], item["due_at"] or ""))


def list_work_items(conn: Connection, user: Row, kind: str | None = None) -> dict:
    allowed = {None, "", "overdue_audits", "validations", "tasks", "issues"}
    if kind not in allowed:
        raise AppError(400, "Invalid work filter.", "validation")
    items = _work_items(conn, user, kind or None)
    return {"items": items, "total": len(items), "kind": kind or "all"}


def dashboard(conn: Connection, user: Row, scope: str = "mine") -> dict:
    if scope not in ("mine", "team"):
        scope = "mine"
    docs = _visible_documents(conn, user, include_archived=False)
    work = _work_items(conn, user)
    counts = {
        "overdue_audits": len(_work_items(conn, user, "overdue_audits")),
        "validations": len(_work_items(conn, user, "validations")),
        "tasks": len(_work_items(conn, user, "tasks")),
        "issues": len(_work_items(conn, user, "issues")),
        "accessible_documents": len(docs),
    }
    now = datetime.now(UTC)
    upcoming = [
        doc
        for doc in docs
        if doc["next_audit_due_at"]
        and doc["archived_at"] is None
        and doc["audit_state"] in ("upcoming", "up_to_date")
        and (due := _date_or_none(doc["next_audit_due_at"])) is not None
        and now <= due <= now + timedelta(days=30)
    ]
    upcoming.sort(key=lambda doc: doc["next_audit_due_at"])
    recent_activity = rows_to_dicts(
        conn.execute(
            """
            SELECT l.*, d.title AS document_title, u.name AS actor_name
            FROM activity_logs l
            JOIN documents d ON d.id = l.document_id
            LEFT JOIN users u ON u.id = l.actor_id
            WHERE l.technical = 0
            ORDER BY l.created_at DESC
            LIMIT 80
            """
        ).fetchall()
    )
    visible_ids = {doc["id"] for doc in docs}
    recent_activity = [item for item in recent_activity if item["document_id"] in visible_ids][:8]
    notifications = list_notifications(conn, user, {"limit": 6})
    return {
        "scope": scope,
        "scope_label": "My work" if scope == "mine" else "My team",
        "counts": counts,
        "metrics": [
            {"key": "overdue_audits", "label": "Documents overdue for audit", "value": counts["overdue_audits"], "href": "#/work/overdue_audits"},
            {"key": "validations", "label": "Validations waiting for me", "value": counts["validations"], "href": "#/work/validations"},
            {"key": "tasks", "label": "My open tasks", "value": counts["tasks"], "href": "#/work/tasks"},
            {"key": "issues", "label": "Issues to decide", "value": counts["issues"], "href": "#/work/issues"},
        ],
        "accessible_document_count": counts["accessible_documents"],
        "priority_items": work[:12],
        "upcoming_deadlines": upcoming[:8],
        "recent_activity": recent_activity,
        "notifications": notifications,
        "unread_notifications": unread_notification_count(conn, user),
    }


def document_detail(conn: Connection, user: Row, document_id: str) -> dict:
    doc = _document_for_action(conn, user, document_id, "view")
    versions = []
    for row in conn.execute(
        """
        SELECT v.*, u.name AS author_name, val.name AS validator_name
        FROM document_versions v
        JOIN users u ON u.id = v.author_id
        LEFT JOIN users val ON val.id = v.validator_id
        WHERE v.document_id = ?
        ORDER BY v.created_at DESC
        """,
        (document_id,),
    ).fetchall():
        if row["status"] == "UP" or perm.has_workflow_access(conn, user["id"], doc):
            versions.append(row_to_dict(row))
    detail = _enrich_document_item(conn, doc)
    detail["tags"] = _loads(doc["tags_json"], [])
    detail["audit_checklist"] = _loads(doc["audit_checklist_json"], [])
    detail["team"] = row_to_dict(conn.execute("SELECT * FROM teams WHERE id = ?", (doc["team_id"],)).fetchone())
    detail["owner"] = row_to_dict(perm.user_by_id(conn, doc["owner_id"]))
    detail["auditor_team"] = row_to_dict(conn.execute("SELECT * FROM teams WHERE id = ?", (doc["auditor_team_id"],)).fetchone())
    detail["versions"] = versions
    detail["reviews"] = rows_to_dicts(
        conn.execute(
            """
            SELECT r.*, rv.target_major, rv.target_minor, rv.major, rv.minor, rv.change_type,
                   requester.name AS requester_name, reviewer.name AS reviewer_name
            FROM reviews r
            JOIN document_versions rv ON rv.id = r.version_id
            JOIN users requester ON requester.id = r.requester_id
            JOIN users reviewer ON reviewer.id = r.reviewer_id
            WHERE r.document_id = ?
            ORDER BY r.created_at DESC
            """,
            (document_id,),
        ).fetchall()
    )
    detail["audits"] = rows_to_dicts(
        conn.execute(
            """
            SELECT a.*, u.name AS assignee_name
            FROM audits a
            LEFT JOIN users u ON u.id = a.assignee_id
            WHERE a.document_id = ?
            ORDER BY a.created_at DESC
            """,
            (document_id,),
        ).fetchall()
    )
    detail["issues"] = rows_to_dicts(
        conn.execute(
            """
            SELECT i.*, u.name AS author_name
            FROM issues i
            LEFT JOIN users u ON u.id = i.author_id
            WHERE i.document_id = ?
            ORDER BY i.created_at DESC
            """,
            (document_id,),
        ).fetchall()
    )
    detail["tasks"] = rows_to_dicts(
        conn.execute(
            """
            SELECT t.*, u.name AS assignee_name
            FROM tasks t
            LEFT JOIN users u ON u.id = t.assignee_id
            WHERE t.document_id = ?
            ORDER BY t.created_at DESC
            """,
            (document_id,),
        ).fetchall()
    )
    detail["flags"] = rows_to_dicts(conn.execute("SELECT * FROM flags WHERE document_id = ? ORDER BY raised_at DESC", (document_id,)).fetchall())
    detail["activity"] = rows_to_dicts(
        conn.execute(
            """
            SELECT l.*, u.name AS actor_name
            FROM activity_logs l
            LEFT JOIN users u ON u.id = l.actor_id
            WHERE l.document_id = ? AND l.technical = 0
            ORDER BY l.created_at DESC
            LIMIT 60
            """,
            (document_id,),
        ).fetchall()
    )
    detail["permissions"] = {
        "can_view": True,
        "can_comment": perm.can_comment(conn, user, doc),
        "can_contribute": perm.can_contribute(conn, user, doc),
        "can_review": perm.can_review(conn, user, doc),
        "can_audit": perm.can_audit(conn, user, doc),
        "can_manage": perm.can_manage_document(conn, user, doc),
    }
    current_text = detail["publication_label"]
    proposal_text = detail["proposal_label"]
    if detail["current_version_id"] and detail["active_proposal"]:
        detail["summary"] = f"Version {_version_text(detail['current_version'])} is published. {proposal_text}."
    elif detail["current_version_id"]:
        detail["summary"] = f"{current_text}. No active editorial work."
    else:
        detail["summary"] = f"{current_text}. Complete the draft to prepare the first publication."
    detail["active_objects"] = {
        "proposal": detail["active_proposal"],
        "audit": detail["active_audit"],
        "blocking_tasks": [task for task in detail["tasks"] if task["blocking"] and task["status"] not in ("DONE", "CANCELLED")],
        "open_issues": [issue for issue in detail["issues"] if issue["status"] not in ("RESOLVED", "DISMISSED")],
    }
    return detail


def _required_publish_gaps(conn: Connection, doc: Row, version: Row) -> list[str]:
    gaps = []
    owner = perm.user_by_id(conn, doc["owner_id"])
    checklist = _loads(doc["audit_checklist_json"], [])
    if not version["title"].strip():
        gaps.append("version title")
    if owner is None or owner["active"] != 1:
        gaps.append("active owner")
    if not doc["team_id"]:
        gaps.append("responsible team")
    if doc["confidentiality"] not in ("INTERNE", "EQUIPE", "RESTREINT"):
        gaps.append("confidentiality")
    if not version["source_location_label"].strip():
        gaps.append("fictional source location")
    if not version["placeholder_ref"].strip():
        gaps.append("placeholder reference")
    if not version["document_format"].strip():
        gaps.append("document format")
    if doc["audit_frequency_value"] <= 0 or doc["audit_frequency_unit"] not in ("days", "months"):
        gaps.append("audit frequency")
    if not doc["auditor_role"].strip():
        gaps.append("auditor role")
    if not doc["audit_instructions"].strip() and not checklist:
        gaps.append("audit instructions or checklist")
    return gaps


def _copy_checklist(conn: Connection, audit_id: int, document_id: str, checklist_json: str) -> None:
    checklist = _loads(checklist_json, [])
    if not checklist:
        checklist = ["Check business relevance", "Check the owner", "Check the placeholder source"]
    for label in checklist:
        conn.execute(
            "INSERT INTO audit_checklist_items(audit_id, document_id, label) VALUES (?, ?, ?)",
            (audit_id, document_id, str(label)),
        )


def create_document(conn: Connection, user: Row, data: dict[str, Any]) -> dict:
    team_id = int(data.get("team_id") or 0)
    if not perm.can_create_document(conn, user, team_id):
        raise AppError(403, "You cannot create a document in this team.", "forbidden")
    title = (data.get("title") or "").strip()
    if not title:
        raise AppError(400, "The title is required.", "validation")
    confidentiality = data.get("confidentiality") or "EQUIPE"
    if confidentiality not in ("INTERNE", "EQUIPE", "RESTREINT"):
        raise AppError(400, "Invalid confidentiality.", "validation")
    owner_id = int(data.get("owner_id") or user["id"])
    owner = _active_user(conn, owner_id)
    if owner_id != user["id"] and not perm.can_administer(conn, user):
        raise AppError(403, "Only an administrator can create directly on behalf of another owner.", "forbidden")
    checklist = data.get("audit_checklist") or ["Business validity", "Owner and team", "Placeholder source"]
    document_id = data.get("id") or f"DOC-{uuid.uuid4().hex[:10].upper()}"
    now = utcnow()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO documents(
                id, title, description, category, tags_json, team_id, owner_id, confidentiality,
                audit_frequency_value, audit_frequency_unit, auditor_role, auditor_team_id,
                audit_instructions, audit_checklist_json, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                title,
                data.get("description") or "",
                data.get("category") or "",
                _dumps(data.get("tags") or []),
                team_id,
                owner_id,
                confidentiality,
                int(data.get("audit_frequency_value") or 12),
                data.get("audit_frequency_unit") or "months",
                data.get("auditor_role") or "AUDITOR",
                int(data.get("auditor_team_id") or team_id),
                data.get("audit_instructions") or "",
                _dumps(checklist),
                user["id"],
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO document_access_grants(document_id, user_id, can_view, can_comment, can_contribute, can_review, can_audit, can_manage, granted_by, reason, created_at)
            VALUES (?, ?, 1, 1, 1, 1, 1, 1, ?, ?, ?)
            """,
            (document_id, owner_id, user["id"], "Initial owner", now),
        )
        cursor = conn.execute(
            """
            INSERT INTO document_versions(
                document_id, target_major, target_minor, change_type, title, description,
                source_location_type, source_location_label, document_format, placeholder_ref,
                change_summary, author_id, status, created_at
            )
            VALUES (?, 1, 0, 'INITIAL', ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?)
            """,
            (
                document_id,
                title,
                data.get("version_description") or data.get("description") or "",
                data.get("source_location_type") or "SharePoint",
                data.get("source_location_label") or "",
                data.get("document_format") or "PDF",
                data.get("placeholder_ref") or "",
                data.get("change_summary") or "Initial creation",
                user["id"],
                now,
            ),
        )
        emit_log(conn, user["id"], "document_created", "document", document_id, document_id=document_id, changes={"owner_id": owner["id"]})
    return document_detail(conn, user, document_id)


VERSION_MUTABLE_FIELDS = {
    "title",
    "description",
    "source_location_type",
    "source_location_label",
    "document_format",
    "placeholder_ref",
    "change_summary",
}


def update_version(conn: Connection, user: Row, version_id: int, data: dict[str, Any]) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if version["status"] != "DRAFT":
        raise AppError(409, "Only drafts can be edited.", "conflict")
    if not perm.can_contribute(conn, user, doc):
        raise AppError(403, "You cannot edit this draft.", "forbidden")
    ignored_sensitive = set(data) - VERSION_MUTABLE_FIELDS - {"row_version"}
    updates = {key: data[key] for key in VERSION_MUTABLE_FIELDS if key in data}
    if not updates and not ignored_sensitive:
        return document_detail(conn, user, doc["id"])
    expected = data.get("row_version")
    with transaction(conn):
        current = conn.execute("SELECT row_version FROM document_versions WHERE id = ?", (version_id,)).fetchone()
        if expected is not None and int(expected) != current["row_version"]:
            raise AppError(409, "Edit conflict: the version has changed.", "conflict")
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values()) + [version_id]
            conn.execute(
                f"UPDATE document_versions SET {assignments}, row_version = row_version + 1 WHERE id = ?",
                values,
            )
        if ignored_sensitive:
            emit_log(
                conn,
                user["id"],
                "sensitive_fields_ignored",
                "document_version",
                version_id,
                document_id=doc["id"],
                changes={"ignored": sorted(ignored_sensitive)},
                technical=True,
            )
        emit_log(conn, user["id"], "version_updated", "document_version", version_id, document_id=doc["id"], changes=updates)
    return document_detail(conn, user, doc["id"])


def submit_version(conn: Connection, user: Row, version_id: int, reviewer_id: int | None = None) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if version["status"] != "DRAFT":
        raise AppError(409, "This proposal is not a draft.", "invalid_transition")
    if not perm.can_contribute(conn, user, doc):
        raise AppError(403, "You cannot submit this proposal.", "forbidden")
    reviewer_id = reviewer_id or doc["owner_id"]
    reviewer = _active_user(conn, reviewer_id)
    if not perm.can_view_document(conn, reviewer, doc):
        raise AppError(422, "The selected reviewer cannot access the document. Grant access explicitly before assigning them.", "access_required")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE document_versions SET status = 'CHALLENGE', submitted_at = ?, row_version = row_version + 1 WHERE id = ?",
            (now, version_id),
        )
        review_id = conn.execute(
            """
            INSERT INTO reviews(document_id, version_id, requester_id, reviewer_id, status, created_at)
            VALUES (?, ?, ?, ?, 'PENDING', ?)
            """,
            (doc["id"], version_id, user["id"], reviewer_id, now),
        ).lastrowid
        notify(
            conn,
            reviewer_id,
            doc["id"],
            f"review:{review_id}",
            "REVIEW_REQUEST",
            f"Proposal {_version_text({'target_major': version['target_major'], 'target_minor': version['target_minor']}, target=True)} ready for validation",
            f"{user['name']} submitted a proposal in {APP_NAME}.",
            f"#/documents/{doc['id']}?tab=versions&version={version_id}",
        )
        emit_log(conn, user["id"], "version_submitted", "document_version", version_id, document_id=doc["id"], changes={"reviewer_id": reviewer_id})
    return document_detail(conn, user, doc["id"])


def request_correction(conn: Connection, user: Row, version_id: int, comment: str) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if not comment.strip():
        raise AppError(400, "A comment is required to request a correction.", "validation")
    if version["status"] != "CHALLENGE":
        raise AppError(409, "The proposal is not in review.", "invalid_transition")
    if not perm.can_review(conn, user, doc):
        raise AppError(403, "You cannot request a correction.", "forbidden")
    review = conn.execute(
        "SELECT * FROM reviews WHERE version_id = ? AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
        (version_id,),
    ).fetchone()
    if review is None:
        raise AppError(409, "No active review.", "invalid_transition")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE document_versions SET status = 'DRAFT', row_version = row_version + 1 WHERE id = ?",
            (version_id,),
        )
        conn.execute(
            "UPDATE reviews SET status = 'CHANGES_REQUESTED', decided_at = ?, row_version = row_version + 1 WHERE id = ?",
            (now, review["id"]),
        )
        conn.execute(
            """
            INSERT INTO review_comments(review_id, document_id, version_id, author_id, body, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (review["id"], doc["id"], version_id, user["id"], comment, now),
        )
        notify(
            conn,
            version["author_id"],
            doc["id"],
            f"correction:{review['id']}:{now}",
            "CORRECTION_REQUEST",
            "Corrections requested",
            comment,
            f"#/documents/{doc['id']}?tab=versions&version={version_id}",
        )
        emit_log(conn, user["id"], "correction_requested", "document_version", version_id, document_id=doc["id"], reason=comment)
    return document_detail(conn, user, doc["id"])


def request_intervention(conn: Connection, user: Row, version_id: int, data: dict[str, Any]) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if version["status"] != "CHALLENGE":
        raise AppError(409, "The proposal must remain in review to request an intervention.", "invalid_transition")
    if not perm.can_review(conn, user, doc):
        raise AppError(403, "You cannot request an intervention.", "forbidden")
    assignee_id = int(data.get("assignee_id") or 0)
    assignee = _active_user(conn, assignee_id)
    if not perm.can_view_document(conn, assignee, doc):
        raise AppError(422, "The selected person cannot access the document. A manager must grant explicit access.", "access_required")
    title = (data.get("title") or "Intervention requested").strip()
    blocking = 0 if data.get("blocking") is False else 1
    now = utcnow()
    with transaction(conn):
        task_id = conn.execute(
            """
            INSERT INTO tasks(document_id, version_id, title, instructions, assignee_id, blocking, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'TODO', ?, ?)
            """,
            (doc["id"], version_id, title, data.get("instructions") or "", assignee_id, blocking, user["id"], now),
        ).lastrowid
        notify(
            conn,
            assignee_id,
            doc["id"],
            f"task:{task_id}",
            "TASK_ASSIGNED",
            "Task assigned",
            title,
            f"#/tasks?task={task_id}",
        )
        emit_log(conn, user["id"], "intervention_requested", "task", task_id, document_id=doc["id"], changes={"blocking": bool(blocking)})
    return document_detail(conn, user, doc["id"])


def approve_review(conn: Connection, user: Row, version_id: int, comment: str = "") -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if version["status"] != "CHALLENGE":
        raise AppError(409, "The proposal is not in review.", "invalid_transition")
    if not perm.can_review(conn, user, doc):
        raise AppError(403, "You cannot approve this review.", "forbidden")
    review = conn.execute(
        "SELECT * FROM reviews WHERE version_id = ? AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
        (version_id,),
    ).fetchone()
    if review is None:
        raise AppError(409, "No active review.", "invalid_transition")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE reviews SET status = 'APPROVED', decided_at = ?, row_version = row_version + 1 WHERE id = ?",
            (now, review["id"]),
        )
        if comment:
            conn.execute(
                """
                INSERT INTO review_comments(review_id, document_id, version_id, author_id, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (review["id"], doc["id"], version_id, user["id"], comment, now),
            )
        notify(
            conn,
            doc["owner_id"],
            doc["id"],
            f"review-approved:{review['id']}",
            "REVIEW_APPROVED",
            "Review approved",
            f"Proposal {version_id} received a favorable review.",
            f"#/documents/{doc['id']}?tab=versions&version={version_id}",
        )
        emit_log(conn, user["id"], "review_approved", "review", review["id"], document_id=doc["id"], reason=comment)
    return document_detail(conn, user, doc["id"])


def _open_blocking_tasks(conn: Connection, document_id: str, version_id: int | None = None) -> list[Row]:
    params: list[Any] = [document_id]
    condition = ""
    if version_id is not None:
        condition = "AND (version_id = ? OR version_id IS NULL)"
        params.append(version_id)
    return conn.execute(
        f"""
        SELECT * FROM tasks
        WHERE document_id = ? {condition}
          AND blocking = 1
          AND status NOT IN ('DONE','CANCELLED')
        """,
        params,
    ).fetchall()


def _event_type_for_change(change_type: str) -> str:
    if change_type == "INITIAL":
        return "INITIAL_PUBLICATION"
    if change_type == "EDIT":
        return "EDIT_PUBLICATION"
    return "NEW_VERSION_PUBLICATION"


def publish_version(conn: Connection, user: Row, version_id: int) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if not perm.can_manage_document(conn, user, doc):
        raise AppError(403, "Only the data owner or an explicit manager can publish.", "forbidden")
    if version["status"] != "CHALLENGE":
        raise AppError(409, "Only a proposal in review can be published.", "invalid_transition")
    gaps = _required_publish_gaps(conn, doc, version)
    if gaps:
        raise AppError(422, "Publication impossible: " + ", ".join(gaps) + ".", "missing_metadata")
    blockers = _open_blocking_tasks(conn, doc["id"], version_id)
    if blockers:
        raise AppError(409, "Publication blocked by an open blocking intervention.", "blocking_task")
    now = utcnow()
    with transaction(conn):
        locked_doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc["id"],)).fetchone()
        locked_version = conn.execute("SELECT * FROM document_versions WHERE id = ?", (version_id,)).fetchone()
        if locked_version["status"] != "CHALLENGE":
            raise AppError(409, "The proposal changed during publication.", "conflict")
        if locked_version["change_type"] == "INITIAL":
            if locked_doc["current_version_id"] is not None:
                raise AppError(409, "A version is already published.", "conflict")
            major, minor = 1, 0
        else:
            if locked_doc["current_version_id"] is None:
                raise AppError(409, "No current version for this change.", "conflict")
            if locked_version["based_on_version_id"] != locked_doc["current_version_id"]:
                raise AppError(409, "The starting version is no longer the current version.", "conflict")
            current = conn.execute("SELECT major, minor FROM document_versions WHERE id = ?", (locked_doc["current_version_id"],)).fetchone()
            if locked_version["change_type"] == "EDIT":
                major, minor = current["major"], current["minor"] + 1
            else:
                major, minor = current["major"] + 1, 0
        next_due = add_frequency(now, locked_doc["audit_frequency_value"], locked_doc["audit_frequency_unit"])
        conn.execute(
            """
            UPDATE document_versions
            SET status = 'UP', major = ?, minor = ?, published_at = ?, validator_id = ?, immutable_at = ?, row_version = row_version + 1
            WHERE id = ?
            """,
            (major, minor, now, user["id"], now, version_id),
        )
        conn.execute(
            """
            UPDATE documents
            SET current_version_id = ?, last_validation_at = ?, next_audit_due_at = ?, row_version = row_version + 1
            WHERE id = ?
            """,
            (version_id, now, next_due, doc["id"]),
        )
        conn.execute(
            """
            INSERT INTO validation_events(document_id, version_id, actor_id, event_type, previous_due_at, next_due_at, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc["id"], version_id, user["id"], _event_type_for_change(locked_version["change_type"]), locked_doc["next_audit_due_at"], next_due, locked_version["change_summary"], now),
        )
        if locked_version["intervention_plan_id"]:
            plan = conn.execute("SELECT * FROM intervention_plans WHERE id = ?", (locked_version["intervention_plan_id"],)).fetchone()
            conn.execute(
                "UPDATE intervention_plans SET status = 'CLOSED', closed_at = ?, version_id = ? WHERE id = ?",
                (now, version_id, plan["id"]),
            )
            conn.execute(
                """
                UPDATE issues
                SET status = 'RESOLVED', resolution = ?, resolved_at = ?, row_version = row_version + 1
                WHERE intervention_plan_id = ? AND status NOT IN ('RESOLVED','DISMISSED')
                """,
                (f"Fixed by publication {major}.{minor}", now, plan["id"]),
            )
            if plan["audit_id"]:
                conn.execute(
                    "UPDATE audits SET status = 'CLOSED', closed_at = ?, conclusion = CASE WHEN conclusion = '' THEN ? ELSE conclusion END, row_version = row_version + 1 WHERE id = ?",
                    (now, f"Closed by publication {major}.{minor}.", plan["audit_id"]),
                )
        conn.execute(
            "UPDATE flags SET active = 0, cleared_at = ? WHERE document_id = ? AND active = 1 AND flag_type = 'AUDIT_OVERDUE'",
            (now, doc["id"]),
        )
        notify(conn, locked_doc["owner_id"], doc["id"], f"published:{version_id}", "PUBLISHED", "Publication approved", f"Version {major}.{minor} published in {APP_NAME}.", f"#/documents/{doc['id']}?tab=versions")
        emit_log(conn, user["id"], "version_published", "document_version", version_id, document_id=doc["id"], changes={"version": f"{major}.{minor}"})
    return document_detail(conn, user, doc["id"])


def review_only_validation(conn: Connection, user: Row, document_id: str, data: dict[str, Any] | None = None) -> dict:
    data = data or {}
    doc = _document_for_action(conn, user, document_id, "manage")
    if doc["current_version_id"] is None:
        raise AppError(409, "A review-only validation requires a published version.", "invalid_transition")
    now = utcnow()
    next_due = add_frequency(now, doc["audit_frequency_value"], doc["audit_frequency_unit"])
    audit_id = data.get("audit_id")
    with transaction(conn):
        conn.execute(
            "UPDATE documents SET last_validation_at = ?, next_audit_due_at = ?, row_version = row_version + 1 WHERE id = ?",
            (now, next_due, document_id),
        )
        conn.execute(
            """
            INSERT INTO validation_events(document_id, version_id, audit_id, actor_id, event_type, previous_due_at, next_due_at, reason, created_at)
            VALUES (?, ?, ?, ?, 'REVIEW_ONLY', ?, ?, ?, ?)
            """,
            (document_id, doc["current_version_id"], audit_id, user["id"], doc["next_audit_due_at"], next_due, data.get("reason") or "Review validated without changes", now),
        )
        if audit_id:
            audit = conn.execute("SELECT * FROM audits WHERE id = ? AND document_id = ?", (audit_id, document_id)).fetchone()
            if audit is None:
                raise AppError(404, "Audit not found.", "audit_not_found")
            unresolved = conn.execute(
                """
                SELECT 1 FROM issues
                WHERE audit_id = ? AND status NOT IN ('RESOLVED','DISMISSED')
                LIMIT 1
                """,
                (audit_id,),
            ).fetchone()
            if unresolved:
                raise AppError(409, "Linked issues must receive an explicit resolution.", "unresolved_issues")
            conn.execute(
                "UPDATE audits SET status = 'CLOSED', closed_at = ?, conclusion = CASE WHEN conclusion = '' THEN ? ELSE conclusion END, row_version = row_version + 1 WHERE id = ?",
                (now, data.get("reason") or "Review-only validation approved.", audit_id),
            )
        conn.execute(
            "UPDATE flags SET active = 0, cleared_at = ? WHERE document_id = ? AND active = 1 AND flag_type = 'AUDIT_OVERDUE'",
            (now, document_id),
        )
        notify(conn, doc["owner_id"], document_id, f"review-only:{now}", "VALIDATION", "Review validated", "The version number remains unchanged.", f"#/documents/{document_id}")
        emit_log(conn, user["id"], "review_only_validated", "document", document_id, document_id=document_id, reason=data.get("reason") or "")
    return document_detail(conn, user, document_id)


def create_issue(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "view")
    if doc["current_version_id"] is None:
        raise AppError(409, "A user issue must target a published document.", "invalid_transition")
    if not perm.can_comment(conn, user, doc):
        raise AppError(403, "You cannot report a problem here.", "forbidden")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if not title or not description:
        raise AppError(400, "Title and description are required.", "validation")
    now = utcnow()
    with transaction(conn):
        issue_id = conn.execute(
            """
            INSERT INTO issues(document_id, version_id, audit_id, author_id, title, description, severity, suggestions, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                document_id,
                doc["current_version_id"],
                data.get("audit_id"),
                user["id"],
                title,
                description,
                data.get("severity") or "MOYENNE",
                data.get("suggestions") or "",
                now,
            ),
        ).lastrowid
        notify(conn, doc["owner_id"], document_id, f"issue:{issue_id}", "ISSUE_CREATED", "Issue to decide", title, f"#/documents/{document_id}?tab=issues&issue={issue_id}")
        emit_log(conn, user["id"], "issue_created", "issue", issue_id, document_id=document_id, changes={"severity": data.get("severity") or "MOYENNE"})
    return document_detail(conn, user, document_id)


def resolve_issue(conn: Connection, user: Row, issue_id: int, data: dict[str, Any]) -> dict:
    issue = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    if issue is None:
        raise AppError(404, "Issue not found.", "issue_not_found")
    doc = _document_for_action(conn, user, issue["document_id"], "manage")
    status = data.get("status") or "RESOLVED"
    if status not in ("RESOLVED", "DISMISSED"):
        raise AppError(400, "Invalid resolution.", "validation")
    reason = (data.get("resolution_reason") or "").strip()
    if status == "DISMISSED" and not reason:
        raise AppError(400, "A reason is required to dismiss an issue.", "validation")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            """
            UPDATE issues
            SET status = ?, resolution = ?, resolution_reason = ?, resolved_at = ?, row_version = row_version + 1
            WHERE id = ?
            """,
            (status, data.get("resolution") or status, reason, now, issue_id),
        )
        emit_log(conn, user["id"], "issue_resolved", "issue", issue_id, document_id=doc["id"], reason=reason, changes={"status": status})
    return document_detail(conn, user, doc["id"])


def add_issue_comment(conn: Connection, user: Row, issue_id: int, body: str) -> dict:
    issue = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    if issue is None:
        raise AppError(404, "Issue not found.", "issue_not_found")
    doc = _document_for_action(conn, user, issue["document_id"], "view")
    if not perm.can_comment(conn, user, doc):
        raise AppError(403, "Comments are not allowed.", "forbidden")
    if not body.strip():
        raise AppError(400, "The comment is required.", "validation")
    with transaction(conn):
        conn.execute(
            "INSERT INTO issue_comments(issue_id, document_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (issue_id, doc["id"], user["id"], body, utcnow()),
        )
        emit_log(conn, user["id"], "issue_commented", "issue", issue_id, document_id=doc["id"])
    return document_detail(conn, user, doc["id"])


def _create_audit_locked(conn: Connection, doc: Row, opened_by_id: int | None, due_at: str, manual: bool = False) -> int:
    now = utcnow()
    audit_id = conn.execute(
        """
        INSERT INTO audits(document_id, opened_by_id, status, due_at, created_at)
        VALUES (?, ?, 'TO_DO', ?, ?)
        """,
        (doc["id"], opened_by_id, due_at, now),
    ).lastrowid
    _copy_checklist(conn, audit_id, doc["id"], doc["audit_checklist_json"])
    if not manual:
        conn.execute(
            """
            INSERT OR IGNORE INTO flags(document_id, flag_type, reason, raised_at)
            VALUES (?, 'AUDIT_OVERDUE', ?, ?)
            """,
            (doc["id"], "Audit due date reached", now),
        )
    notify(
        conn,
        doc["owner_id"],
        doc["id"],
        f"audit-due:{doc['id']}:{due_at}",
        "AUDIT_DUE",
        "Audit due",
        f"The audit for {doc['title']} is due.",
        f"#/audits/{audit_id}",
    )
    emit_log(conn, opened_by_id, "audit_opened", "audit", audit_id, document_id=doc["id"], changes={"due_at": due_at, "manual": manual})
    return audit_id


def run_scheduler(conn: Connection) -> dict:
    now = utcnow()
    created = []
    flagged = []
    with transaction(conn):
        docs = conn.execute(
            """
            SELECT * FROM documents
            WHERE current_version_id IS NOT NULL
              AND archived_at IS NULL
              AND next_audit_due_at IS NOT NULL
              AND next_audit_due_at <= ?
            """,
            (now,),
        ).fetchall()
        for doc in docs:
            active = conn.execute(
                """
                SELECT 1 FROM audits
                WHERE document_id = ? AND status IN ('TO_DO','IN_PROGRESS','AWAITING_OWNER_DECISION','REMEDIATION_IN_PROGRESS','AWAITING_FINAL_VALIDATION')
                LIMIT 1
                """,
                (doc["id"],),
            ).fetchone()
            if active is None:
                try:
                    created.append(_create_audit_locked(conn, doc, None, doc["next_audit_due_at"]))
                except IntegrityError:
                    pass
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO flags(document_id, flag_type, reason, raised_at)
                    VALUES (?, 'AUDIT_OVERDUE', ?, ?)
                    """,
                    (doc["id"], "Audit due date exceeded", now),
                )
                flagged.append(doc["id"])
    return {"created_audits": created, "flagged_documents": flagged}


def open_manual_audit(conn: Connection, user: Row, document_id: str) -> dict:
    doc = _document_for_action(conn, user, document_id, "manage")
    if doc["current_version_id"] is None or doc["archived_at"] is not None:
        raise AppError(409, "An audit cannot be opened for a never-published or archived document.", "invalid_transition")
    with transaction(conn):
        try:
            _create_audit_locked(conn, doc, user["id"], utcnow(), manual=True)
        except IntegrityError as exc:
            raise AppError(409, "An audit is already active for this document.", "active_audit") from exc
    return document_detail(conn, user, document_id)


def claim_audit(conn: Connection, user: Row, audit_id: int) -> dict:
    audit = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    if audit is None:
        raise AppError(404, "Audit not found.", "audit_not_found")
    doc = _document_for_action(conn, user, audit["document_id"], "audit")
    if audit["status"] not in ("TO_DO", "IN_PROGRESS"):
        raise AppError(409, "This audit cannot be claimed.", "invalid_transition")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            """
            UPDATE audits
            SET assignee_id = ?, status = 'IN_PROGRESS', started_at = COALESCE(started_at, ?), row_version = row_version + 1
            WHERE id = ?
            """,
            (user["id"], now, audit_id),
        )
        emit_log(conn, user["id"], "audit_claimed", "audit", audit_id, document_id=doc["id"])
    return document_detail(conn, user, doc["id"])


def submit_audit(conn: Connection, user: Row, audit_id: int, data: dict[str, Any]) -> dict:
    audit = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    if audit is None:
        raise AppError(404, "Audit not found.", "audit_not_found")
    doc = _document_for_action(conn, user, audit["document_id"], "audit")
    if audit["status"] not in ("TO_DO", "IN_PROGRESS"):
        raise AppError(409, "This audit cannot be submitted.", "invalid_transition")
    conclusion = (data.get("conclusion") or "").strip()
    if not conclusion:
        raise AppError(400, "The audit report is required.", "validation")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE audits SET status = 'AWAITING_OWNER_DECISION', assignee_id = COALESCE(assignee_id, ?), submitted_at = ?, conclusion = ?, row_version = row_version + 1 WHERE id = ?",
            (user["id"], now, conclusion, audit_id),
        )
        for issue in data.get("issues") or []:
            if issue.get("title") and issue.get("description"):
                conn.execute(
                    """
                    INSERT INTO issues(document_id, version_id, audit_id, author_id, title, description, severity, suggestions, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'UNDER_REVIEW', ?)
                    """,
                    (
                        doc["id"],
                        doc["current_version_id"],
                        audit_id,
                        user["id"],
                        issue["title"],
                        issue["description"],
                        issue.get("severity") or "MOYENNE",
                        issue.get("suggestions") or "",
                        now,
                    ),
                )
        notify(conn, doc["owner_id"], doc["id"], f"audit-submitted:{audit_id}", "AUDIT_RESULT", "Decision required", conclusion, f"#/audits/{audit_id}")
        emit_log(conn, user["id"], "audit_submitted", "audit", audit_id, document_id=doc["id"], reason=conclusion)
    return document_detail(conn, user, doc["id"])


def decide_audit(conn: Connection, user: Row, audit_id: int, data: dict[str, Any]) -> dict:
    audit = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    if audit is None:
        raise AppError(404, "Audit not found.", "audit_not_found")
    doc = _document_for_action(conn, user, audit["document_id"], "manage")
    if audit["status"] != "AWAITING_OWNER_DECISION":
        raise AppError(409, "This audit is not waiting for an owner decision.", "invalid_transition")
    decision = data.get("decision_type")
    if decision not in ("REVIEW_ONLY", "EDIT", "NEW_VERSION", "ARCHIVE"):
        raise AppError(400, "Invalid decision.", "validation")
    justification = (data.get("justification") or "").strip()
    if not justification:
        raise AppError(400, "Justification is required.", "validation")
    if decision == "REVIEW_ONLY":
        open_issues = conn.execute(
            "SELECT * FROM issues WHERE audit_id = ? AND status NOT IN ('RESOLVED','DISMISSED')",
            (audit_id,),
        ).fetchall()
        if open_issues:
            raise AppError(409, "Resolve, dismiss, or explicitly accept issues before a review-only validation.", "unresolved_issues")
        return review_only_validation(conn, user, doc["id"], {"audit_id": audit_id, "reason": justification})
    if decision == "ARCHIVE":
        return archive_document(conn, user, doc["id"], {"reason": justification, "audit_id": audit_id})
    if doc["current_version_id"] is None:
        raise AppError(409, "A remediation requires a published version.", "invalid_transition")
    primary_id = int(data.get("primary_responsible_id") or user["id"])
    primary = _active_user(conn, primary_id)
    if not perm.can_view_document(conn, primary, doc):
        raise AppError(422, "The selected responsible person cannot access the document.", "access_required")
    reviewer_id = data.get("reviewer_id") or doc["owner_id"]
    reviewer = _active_user(conn, int(reviewer_id))
    if not perm.can_view_document(conn, reviewer, doc):
        raise AppError(422, "The selected reviewer cannot access the document.", "access_required")
    current = conn.execute("SELECT * FROM document_versions WHERE id = ?", (doc["current_version_id"],)).fetchone()
    now = utcnow()
    with transaction(conn):
        try:
            plan_id = conn.execute(
                """
                INSERT INTO intervention_plans(document_id, audit_id, decision_type, justification, primary_responsible_id, reviewer_id, target_date, resources_text, status, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'IN_PROGRESS', ?, ?)
                """,
                (
                    doc["id"],
                    audit_id,
                    decision,
                    justification,
                    primary_id,
                    int(reviewer_id),
                    data.get("target_date"),
                    data.get("resources_text") or "",
                    user["id"],
                    now,
                ),
            ).lastrowid
            target_major = current["major"] if decision == "EDIT" else current["major"] + 1
            target_minor = current["minor"] + 1 if decision == "EDIT" else 0
            version_id = conn.execute(
                """
                INSERT INTO document_versions(
                    document_id, target_major, target_minor, change_type, title, description,
                    source_location_type, source_location_label, document_format, placeholder_ref,
                    change_summary, author_id, status, created_at, based_on_version_id, intervention_plan_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?)
                """,
                (
                    doc["id"],
                    target_major,
                    target_minor,
                    decision,
                    current["title"],
                    current["description"],
                    current["source_location_type"],
                    current["source_location_label"],
                    current["document_format"],
                    current["placeholder_ref"],
                    justification,
                    primary_id,
                    now,
                    current["id"],
                    plan_id,
                ),
            ).lastrowid
            conn.execute("UPDATE intervention_plans SET version_id = ? WHERE id = ?", (version_id, plan_id))
            issue_ids = [int(issue_id) for issue_id in data.get("issue_ids") or []]
            if issue_ids:
                placeholders = ",".join("?" for _ in issue_ids)
                conn.execute(
                    f"UPDATE issues SET status = 'IN_PROGRESS', intervention_plan_id = ?, row_version = row_version + 1 WHERE document_id = ? AND id IN ({placeholders})",
                    [plan_id, doc["id"], *issue_ids],
                )
            for task in data.get("tasks") or [{"title": "Perform the intervention plan", "instructions": justification, "blocking": True, "assignee_id": primary_id}]:
                assignee_id = int(task.get("assignee_id") or primary_id)
                assignee = _active_user(conn, assignee_id)
                if not perm.can_view_document(conn, assignee, doc):
                    raise AppError(422, "A selected contributor cannot access the document.", "access_required")
                task_id = conn.execute(
                    """
                    INSERT INTO tasks(document_id, intervention_plan_id, audit_id, version_id, title, instructions, assignee_id, blocking, status, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'TODO', ?, ?)
                    """,
                    (
                        doc["id"],
                        plan_id,
                        audit_id,
                        version_id,
                        task.get("title") or "Intervention task",
                        task.get("instructions") or "",
                        assignee_id,
                        0 if task.get("blocking") is False else 1,
                        user["id"],
                        now,
                    ),
                ).lastrowid
                notify(conn, assignee_id, doc["id"], f"task:{task_id}", "TASK_ASSIGNED", "Task assigned", task.get("title") or "Intervention task", f"#/tasks?task={task_id}")
            conn.execute(
                "UPDATE audits SET status = 'REMEDIATION_IN_PROGRESS', decision = ?, owner_decision_reason = ?, row_version = row_version + 1 WHERE id = ?",
                (decision, justification, audit_id),
            )
            emit_log(conn, user["id"], "audit_decided", "audit", audit_id, document_id=doc["id"], changes={"decision": decision, "plan_id": plan_id, "version_id": version_id})
        except IntegrityError as exc:
            raise AppError(409, "An active proposal already exists for this document.", "active_proposal") from exc
    return document_detail(conn, user, doc["id"])


def create_proposal(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "contribute")
    if doc["current_version_id"] is None:
        raise AppError(409, "Use the initial draft until a version has been published.", "invalid_transition")
    change_type = data.get("change_type")
    if change_type not in ("EDIT", "NEW_VERSION"):
        raise AppError(400, "The change type must be EDIT or NEW_VERSION.", "validation")
    current = conn.execute("SELECT * FROM document_versions WHERE id = ?", (doc["current_version_id"],)).fetchone()
    now = utcnow()
    try:
        with transaction(conn):
            target_major = current["major"] if change_type == "EDIT" else current["major"] + 1
            target_minor = current["minor"] + 1 if change_type == "EDIT" else 0
            version_id = conn.execute(
                """
                INSERT INTO document_versions(
                    document_id, target_major, target_minor, change_type, title, description,
                    source_location_type, source_location_label, document_format, placeholder_ref,
                    change_summary, author_id, status, created_at, based_on_version_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)
                """,
                (
                    doc["id"],
                    target_major,
                    target_minor,
                    change_type,
                    data.get("title") or current["title"],
                    data.get("description") or current["description"],
                    data.get("source_location_type") or current["source_location_type"],
                    data.get("source_location_label") or current["source_location_label"],
                    data.get("document_format") or current["document_format"],
                    data.get("placeholder_ref") or current["placeholder_ref"],
                    data.get("change_summary") or "",
                    user["id"],
                    now,
                    current["id"],
                ),
            ).lastrowid
            emit_log(conn, user["id"], "proposal_created", "document_version", version_id, document_id=doc["id"], changes={"change_type": change_type})
    except IntegrityError as exc:
        raise AppError(409, "Only one active proposal is allowed per document.", "active_proposal") from exc
    return document_detail(conn, user, document_id)


def update_task_status(conn: Connection, user: Row, task_id: int, status: str, reason: str = "") -> dict:
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise AppError(404, "Task not found.", "task_not_found")
    doc = _document_for_action(conn, user, task["document_id"], "view")
    if task["assignee_id"] != user["id"] and not perm.can_manage_document(conn, user, doc):
        raise AppError(403, "You cannot update this task.", "forbidden")
    if status not in ("TODO", "IN_PROGRESS", "DONE", "CANCELLED"):
        raise AppError(400, "Invalid task status.", "validation")
    if status == "CANCELLED" and not reason.strip():
        raise AppError(400, "A reason is required to cancel a task.", "validation")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, cancel_reason = CASE WHEN ? = 'CANCELLED' THEN ? ELSE cancel_reason END,
                completed_at = CASE WHEN ? = 'DONE' THEN ? ELSE completed_at END,
                row_version = row_version + 1
            WHERE id = ?
            """,
            (status, status, reason, status, now, task_id),
        )
        emit_log(conn, user["id"], "task_updated", "task", task_id, document_id=doc["id"], reason=reason, changes={"status": status})
    return document_detail(conn, user, doc["id"])


def grant_access(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "manage")
    grantee_user_id = data.get("user_id")
    grantee_team_id = data.get("team_id")
    if not grantee_user_id and not grantee_team_id:
        raise AppError(400, "Specify a user or a team.", "validation")
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise AppError(400, "A reason is required.", "validation")
    with transaction(conn):
        grant_id = conn.execute(
            """
            INSERT INTO document_access_grants(
                document_id, user_id, team_id, can_view, can_comment, can_contribute, can_review, can_audit, can_manage, granted_by, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                grantee_user_id,
                grantee_team_id,
                1 if data.get("can_view", True) else 0,
                1 if data.get("can_comment") else 0,
                1 if data.get("can_contribute") else 0,
                1 if data.get("can_review") else 0,
                1 if data.get("can_audit") else 0,
                1 if data.get("can_manage") else 0,
                user["id"],
                reason,
                utcnow(),
            ),
        ).lastrowid
        emit_log(conn, user["id"], "access_granted", "document_access_grant", grant_id, document_id=document_id, reason=reason)
    return document_detail(conn, user, document_id)


def revoke_access(conn: Connection, user: Row, grant_id: int, reason: str) -> dict:
    grant = conn.execute("SELECT * FROM document_access_grants WHERE id = ?", (grant_id,)).fetchone()
    if grant is None:
        raise AppError(404, "Access grant not found.", "grant_not_found")
    _document_for_action(conn, user, grant["document_id"], "manage")
    if not reason.strip():
        raise AppError(400, "A reason is required.", "validation")
    with transaction(conn):
        conn.execute("UPDATE document_access_grants SET revoked_at = ?, row_version = row_version + 1 WHERE id = ?", (utcnow(), grant_id))
        emit_log(conn, user["id"], "access_revoked", "document_access_grant", grant_id, document_id=grant["document_id"], reason=reason)
    return document_detail(conn, user, grant["document_id"])


def transfer_owner(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "manage")
    new_owner_id = int(data.get("new_owner_id") or 0)
    new_owner = _active_user(conn, new_owner_id)
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise AppError(400, "A reason is required to transfer a document.", "validation")
    if not perm.can_view_document(conn, new_owner, doc):
        raise AppError(422, "The new owner must first receive explicit access.", "access_required")
    with transaction(conn):
        conn.execute("UPDATE documents SET owner_id = ?, row_version = row_version + 1 WHERE id = ?", (new_owner_id, document_id))
        conn.execute(
            """
            INSERT INTO document_access_grants(document_id, user_id, can_view, can_comment, can_contribute, can_review, can_audit, can_manage, granted_by, reason, created_at)
            VALUES (?, ?, 1, 1, 1, 1, 1, 1, ?, ?, ?)
            """,
            (document_id, new_owner_id, user["id"], "Owner transfer: " + reason, utcnow()),
        )
        conn.execute(
            "UPDATE tasks SET assignee_id = ? WHERE document_id = ? AND assignee_id = ? AND status IN ('TODO','IN_PROGRESS')",
            (new_owner_id, document_id, doc["owner_id"]),
        )
        emit_log(conn, user["id"], "owner_transferred", "document", document_id, document_id=document_id, reason=reason, changes={"from": doc["owner_id"], "to": new_owner_id})
    return document_detail(conn, user, document_id)


def archive_document(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "manage")
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise AppError(400, "An archive reason is required.", "validation")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE documents SET archived_at = ?, archive_reason = ?, row_version = row_version + 1 WHERE id = ?",
            (now, reason, document_id),
        )
        conn.execute(
            "UPDATE document_versions SET status = 'CANCELLED', cancelled_reason = ?, row_version = row_version + 1 WHERE document_id = ? AND status IN ('DRAFT','CHALLENGE')",
            ("Cancelled by archive: " + reason, document_id),
        )
        conn.execute(
            "UPDATE tasks SET status = 'CANCELLED', cancel_reason = ?, row_version = row_version + 1 WHERE document_id = ? AND status IN ('TODO','IN_PROGRESS')",
            ("Cancelled by archive: " + reason, document_id),
        )
        conn.execute(
            "UPDATE audits SET status = 'CLOSED', closed_at = ?, conclusion = ? WHERE document_id = ? AND status NOT IN ('CLOSED','CANCELLED')",
            (now, "Document archived: " + reason, document_id),
        )
        conn.execute(
            """
            UPDATE issues
            SET status = 'DISMISSED', resolution = 'Document archived', resolution_reason = ?, resolved_at = ?, row_version = row_version + 1
            WHERE document_id = ? AND status NOT IN ('RESOLVED','DISMISSED')
            """,
            (reason, now, document_id),
        )
        conn.execute(
            "UPDATE flags SET active = 0, cleared_at = ? WHERE document_id = ? AND active = 1",
            (now, document_id),
        )
        conn.execute(
            """
            INSERT INTO validation_events(document_id, version_id, audit_id, actor_id, event_type, previous_due_at, next_due_at, reason, created_at)
            VALUES (?, ?, ?, ?, 'ARCHIVE', ?, NULL, ?, ?)
            """,
            (document_id, doc["current_version_id"], data.get("audit_id"), user["id"], doc["next_audit_due_at"], reason, now),
        )
        emit_log(conn, user["id"], "document_archived", "document", document_id, document_id=document_id, reason=reason)
    return document_detail(conn, user, document_id)


def snooze_flag(conn: Connection, user: Row, flag_id: int, data: dict[str, Any]) -> dict:
    flag = conn.execute("SELECT * FROM flags WHERE id = ?", (flag_id,)).fetchone()
    if flag is None:
        raise AppError(404, "Flag not found.", "flag_not_found")
    _document_for_action(conn, user, flag["document_id"], "manage")
    reminder_at = (data.get("reminder_at") or "").strip()
    reason = (data.get("reason") or "").strip()
    if not reminder_at or not reason:
        raise AppError(400, "Reminder date and reason are required.", "validation")
    with transaction(conn):
        conn.execute(
            "UPDATE flags SET snoozed_until = ?, snooze_reason = ? WHERE id = ?",
            (reminder_at, reason, flag_id),
        )
        emit_log(conn, user["id"], "flag_snoozed", "flag", flag_id, document_id=flag["document_id"], reason=reason)
    return document_detail(conn, user, flag["document_id"])


def _notification_action(conn: Connection, item: dict, doc: Row | None) -> dict:
    if doc is None:
        return {"label": "Unavailable", "href": "", "state": "inaccessible"}
    notification_type = item["type"]
    if notification_type == "AUDIT_DUE":
        audit = _active_audit(conn, doc["id"])
        if audit:
            return {"label": "Open audit", "href": f"#/audits/{audit['id']}", "state": "open"}
    if notification_type == "AUDIT_RESULT":
        audit = _active_audit(conn, doc["id"])
        if audit and audit["status"] == "AWAITING_OWNER_DECISION":
            return {"label": "Decide treatment", "href": f"#/audits/{audit['id']}", "state": "open"}
        return {"label": "Already handled", "href": f"#/documents/{doc['id']}?tab=audits", "state": "done"}
    if notification_type in ("REVIEW_REQUEST", "REVIEW_APPROVED"):
        proposal = _active_proposal(conn, doc["id"])
        if proposal:
            return {"label": "Review proposal", "href": f"#/documents/{doc['id']}?tab=versions&version={proposal['id']}", "state": "open"}
        return {"label": "Already handled", "href": f"#/documents/{doc['id']}?tab=versions", "state": "done"}
    if notification_type in ("TASK_ASSIGNED", "CORRECTION_REQUEST"):
        return {"label": "Open tasks", "href": f"#/documents/{doc['id']}?tab=overview", "state": "open"}
    if notification_type == "ISSUE_CREATED":
        return {"label": "Review issue", "href": f"#/documents/{doc['id']}?tab=issues", "state": "open"}
    return {"label": "Open context", "href": f"#/documents/{doc['id']}", "state": "open"}


def _decorate_notification(conn: Connection, user: Row, row: Row) -> dict | None:
    item = row_to_dict(row)
    item["type_label"] = NOTIFICATION_LABELS.get(item["type"], item["type"])
    item["read"] = item["read_at"] is not None
    if item["document_id"]:
        doc = perm.document_by_id(conn, item["document_id"])
        if doc is None or not perm.can_view_document(conn, user, doc):
            return None
        item["document_title"] = doc["title"]
        item["title"] = f"{item['type_label']} — {doc['title']}"
        item["action"] = _notification_action(conn, item, doc)
        proposal = _active_proposal(conn, doc["id"])
        current = _current_version(conn, doc)
        item["version_label"] = _version_text(proposal, target=True) if proposal else _version_text(current)
    else:
        item["document_title"] = ""
        item["action"] = {"label": "View", "href": item["url"] or "#/notifications", "state": "open"}
        item["version_label"] = ""
    return item


def list_notifications(conn: Connection, user: Row, filters: dict[str, Any] | None = None) -> list[dict]:
    filters = filters or {}
    only_unread = str(filters.get("unread", "")).lower() in ("1", "true", "yes")
    type_filter = str(filters.get("type") or "")
    limit = _safe_int(filters.get("limit"), 100, minimum=1, maximum=200)
    rows = conn.execute(
        """
        SELECT * FROM notifications
        WHERE recipient_id = ?
        ORDER BY read_at IS NOT NULL, created_at DESC
        LIMIT 500
        """,
        (user["id"],),
    ).fetchall()
    visible = []
    for row in rows:
        item = _decorate_notification(conn, user, row)
        if item is None:
            continue
        if only_unread and item["read"]:
            continue
        if type_filter and item["type"] != type_filter:
            continue
        visible.append(item)
        if len(visible) >= limit:
            break
    return visible


def unread_notification_count(conn: Connection, user: Row) -> int:
    return len(list_notifications(conn, user, {"unread": "1", "limit": 200}))


def mark_notification_read(conn: Connection, user: Row, notification_id: int) -> dict:
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET read_at = COALESCE(read_at, ?) WHERE id = ? AND recipient_id = ?",
            (utcnow(), notification_id, user["id"]),
        )
    return {"ok": True}


def mark_notification_unread(conn: Connection, user: Row, notification_id: int) -> dict:
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET read_at = NULL WHERE id = ? AND recipient_id = ?",
            (notification_id, user["id"]),
        )
    return {"ok": True}


def mark_all_notifications_read(conn: Connection, user: Row) -> dict:
    now = utcnow()
    visible = list_notifications(conn, user, {"limit": 500})
    ids = [item["id"] for item in visible if not item["read"]]
    if not ids:
        return {"ok": True, "updated": 0}
    placeholders = ",".join("?" for _ in ids)
    with transaction(conn):
        conn.execute(
            f"UPDATE notifications SET read_at = COALESCE(read_at, ?) WHERE recipient_id = ? AND id IN ({placeholders})",
            [now, user["id"], *ids],
        )
    return {"ok": True, "updated": len(ids)}


def audit_detail(conn: Connection, user: Row, audit_id: int) -> dict:
    audit = conn.execute(
        """
        SELECT a.*, assignee.name AS assignee_name, opener.name AS opened_by_name
        FROM audits a
        LEFT JOIN users assignee ON assignee.id = a.assignee_id
        LEFT JOIN users opener ON opener.id = a.opened_by_id
        WHERE a.id = ?
        """,
        (audit_id,),
    ).fetchone()
    if audit is None:
        raise AppError(404, "Audit not found.", "not_found")
    try:
        doc = _document_for_action(conn, user, audit["document_id"], "view")
    except AppError as exc:
        if exc.status == 403:
            raise AppError(404, "Audit not found.", "not_found") from exc
        raise
    current = _current_version(conn, doc)
    detail = row_to_dict(audit)
    detail["document"] = _enrich_document_item(conn, doc)
    detail["version_examined"] = row_to_dict(current)
    detail["checklist"] = rows_to_dicts(conn.execute("SELECT * FROM audit_checklist_items WHERE audit_id = ? ORDER BY id", (audit_id,)).fetchall())
    detail["issues"] = rows_to_dicts(conn.execute("SELECT * FROM issues WHERE audit_id = ? ORDER BY created_at DESC", (audit_id,)).fetchall())
    detail["plan"] = row_to_dict(conn.execute("SELECT * FROM intervention_plans WHERE audit_id = ? ORDER BY created_at DESC LIMIT 1", (audit_id,)).fetchone())
    detail["tasks"] = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE audit_id = ? ORDER BY created_at DESC", (audit_id,)).fetchall())
    detail["proposal"] = (
        row_to_dict(
            conn.execute(
                "SELECT * FROM document_versions WHERE intervention_plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (detail["plan"]["id"],),
            ).fetchone()
        )
        if detail["plan"]
        else None
    )
    if audit["status"] in ("TO_DO", "IN_PROGRESS"):
        detail["next_action"] = "Perform audit"
    elif audit["status"] == "AWAITING_OWNER_DECISION":
        detail["next_action"] = "Decide treatment"
    elif audit["status"] == "REMEDIATION_IN_PROGRESS":
        detail["next_action"] = "Follow the intervention and proposal"
    else:
        detail["next_action"] = "Review the result"
    detail["progress"] = ["Review", "Owner decision", "Intervention if needed", "Validation", "Closure"]
    return detail


def list_tasks(conn: Connection, user: Row, filters: dict[str, Any] | None = None) -> dict:
    status_filter = str((filters or {}).get("status") or "open")
    rows = conn.execute(
        """
        SELECT t.*, d.title AS document_title, u.name AS assignee_name
        FROM tasks t
        JOIN documents d ON d.id = t.document_id
        LEFT JOIN users u ON u.id = t.assignee_id
        WHERE t.assignee_id = ?
        ORDER BY t.created_at DESC
        """,
        (user["id"],),
    ).fetchall()
    items = []
    for row in rows:
        doc = perm.document_by_id(conn, row["document_id"])
        if doc is None or not perm.can_view_document(conn, user, doc):
            continue
        if status_filter == "open" and row["status"] in ("DONE", "CANCELLED"):
            continue
        item = row_to_dict(row)
        item["document"] = _enrich_document_item(conn, doc)
        items.append(item)
    return {"items": items, "total": len(items)}


def list_audits(conn: Connection, user: Row, filters: dict[str, Any] | None = None) -> dict:
    state = str((filters or {}).get("state") or "")
    rows = conn.execute(
        """
        SELECT a.*, d.title AS document_title, u.name AS assignee_name
        FROM audits a
        JOIN documents d ON d.id = a.document_id
        LEFT JOIN users u ON u.id = a.assignee_id
        ORDER BY a.created_at DESC
        """
    ).fetchall()
    items = []
    for row in rows:
        doc = perm.document_by_id(conn, row["document_id"])
        if doc is None or not perm.can_view_document(conn, user, doc):
            continue
        if state == "active" and row["status"] not in ACTIVE_AUDIT_STATUSES:
            continue
        item = row_to_dict(row)
        item["document"] = _enrich_document_item(conn, doc)
        items.append(item)
    return {"items": items, "total": len(items)}


def admin_create_user(conn: Connection, user: Row, data: dict[str, Any], password_hash: str) -> dict:
    if not perm.can_administer(conn, user):
        raise AppError(403, "Administration is reserved for administrators.", "forbidden")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    if not name or not email:
        raise AppError(400, "Name and email are required.", "validation")
    with transaction(conn):
        user_id = conn.execute(
            "INSERT INTO users(name, email, password_hash, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (name, email, password_hash, utcnow()),
        ).lastrowid
        emit_log(conn, user["id"], "user_created", "user", user_id, reason="Administration", changes={"email": email})
    return {"id": user_id, "name": name, "email": email, "active": 1}
