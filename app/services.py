from __future__ import annotations

import json
import uuid
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, IntegrityError, Row
from typing import Any

from . import permissions as perm
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
        raise AppError(400, "Unité de fréquence inconnue.", "invalid_frequency")
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
        raise AppError(404, "Utilisateur introuvable ou inactif.", "user_not_found")
    return user


def _document_for_action(conn: Connection, user: Row, document_id: str, action: str = "view") -> Row:
    doc = perm.document_by_id(conn, document_id)
    if doc is None:
        raise AppError(404, "Document introuvable.", "document_not_found")
    if action == "view" and not perm.can_view_document(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_view", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "Accès refusé à ce document.", "forbidden")
    if action == "manage" and not perm.can_manage_document(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_manage", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "Action réservée au data owner ou à un gestionnaire explicitement autorisé.", "forbidden")
    if action == "contribute" and not perm.can_contribute(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_contribute", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "Vous ne pouvez pas contribuer à ce document.", "forbidden")
    if action == "review" and not perm.can_review(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_review", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "Vous ne pouvez pas reviewer ce document.", "forbidden")
    if action == "audit" and not perm.can_audit(conn, user, doc):
        emit_log(conn, user["id"], "forbidden_audit", "document", document_id, document_id=document_id, technical=True)
        raise AppError(403, "Vous ne pouvez pas auditer ce document.", "forbidden")
    return doc


def _version_for_user(conn: Connection, user: Row, version_id: int) -> tuple[Row, Row]:
    version = conn.execute("SELECT * FROM document_versions WHERE id = ?", (version_id,)).fetchone()
    if version is None:
        raise AppError(404, "Version introuvable.", "version_not_found")
    doc = _document_for_action(conn, user, version["document_id"], "view")
    if version["status"] != "UP" and not perm.has_workflow_access(conn, user["id"], doc):
        raise AppError(403, "Accès refusé à cette proposition.", "forbidden")
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
    teams = rows_to_dicts(conn.execute("SELECT * FROM teams ORDER BY name").fetchall())
    users = rows_to_dicts(
        conn.execute("SELECT id, name, email, active, created_at FROM users ORDER BY name").fetchall()
    )
    memberships = rows_to_dicts(conn.execute("SELECT * FROM team_memberships").fetchall())
    roles = rows_to_dicts(conn.execute("SELECT * FROM role_assignments WHERE active = 1").fetchall())
    return {
        "teams": teams,
        "users": users,
        "memberships": memberships,
        "roles": roles,
        "is_admin": perm.can_administer(conn, user),
    }


def list_documents(conn: Connection, user: Row, filters: dict[str, Any] | None = None) -> dict:
    filters = filters or {}
    search = (filters.get("search") or "").strip().lower()
    confidentiality = filters.get("confidentiality") or ""
    include_archived = str(filters.get("include_archived", "")).lower() in ("1", "true", "yes")
    page = max(1, int(filters.get("page") or 1))
    per_page = min(50, max(5, int(filters.get("per_page") or 10)))
    rows = conn.execute(
        """
        SELECT d.*, t.name AS team_name, u.name AS owner_name,
               cv.major AS current_major, cv.minor AS current_minor,
               EXISTS(SELECT 1 FROM flags f WHERE f.document_id = d.id AND f.active = 1 AND f.flag_type = 'AUDIT_OVERDUE') AS has_overdue_flag,
               EXISTS(SELECT 1 FROM document_versions v WHERE v.document_id = d.id AND v.status IN ('DRAFT','CHALLENGE')) AS has_active_proposal,
               EXISTS(SELECT 1 FROM audits a WHERE a.document_id = d.id AND a.status NOT IN ('CLOSED','CANCELLED')) AS has_active_audit
        FROM documents d
        JOIN teams t ON t.id = d.team_id
        JOIN users u ON u.id = d.owner_id
        LEFT JOIN document_versions cv ON cv.id = d.current_version_id
        ORDER BY COALESCE(d.next_audit_due_at, d.created_at) ASC, d.title ASC
        """
    ).fetchall()
    visible = []
    for doc in rows:
        if not include_archived and doc["archived_at"] is not None:
            continue
        if confidentiality and doc["confidentiality"] != confidentiality:
            continue
        if search:
            haystack = " ".join(
                [
                    doc["id"],
                    doc["title"],
                    doc["description"],
                    doc["category"],
                    doc["tags_json"],
                    doc["team_name"],
                    doc["owner_name"],
                ]
            ).lower()
            if search not in haystack:
                continue
        if perm.can_view_document(conn, user, doc):
            item = row_to_dict(doc)
            item["tags"] = _loads(doc["tags_json"], [])
            visible.append(item)
    total = len(visible)
    start = (page - 1) * per_page
    return {"items": visible[start : start + per_page], "page": page, "per_page": per_page, "total": total}


def dashboard(conn: Connection, user: Row) -> dict:
    docs = list_documents(conn, user, {"per_page": 1000})["items"]
    doc_ids = {doc["id"] for doc in docs}
    tasks = []
    for row in conn.execute(
        """
        SELECT ta.*, d.title AS document_title
        FROM tasks ta JOIN documents d ON d.id = ta.document_id
        WHERE ta.assignee_id = ? AND ta.status IN ('TODO','IN_PROGRESS')
        ORDER BY ta.created_at DESC
        """,
        (user["id"],),
    ).fetchall():
        if row["document_id"] in doc_ids:
            tasks.append(row_to_dict(row))
    notifications = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM notifications
            WHERE recipient_id = ?
            ORDER BY read_at IS NOT NULL, created_at DESC
            LIMIT 20
            """,
            (user["id"],),
        ).fetchall()
    )
    return {
        "document_count": len(docs),
        "overdue_count": sum(1 for doc in docs if doc["has_overdue_flag"]),
        "active_tasks": tasks,
        "notifications": notifications,
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
    detail = row_to_dict(doc)
    detail["tags"] = _loads(doc["tags_json"], [])
    detail["audit_checklist"] = _loads(doc["audit_checklist_json"], [])
    detail["team"] = row_to_dict(conn.execute("SELECT * FROM teams WHERE id = ?", (doc["team_id"],)).fetchone())
    detail["owner"] = row_to_dict(perm.user_by_id(conn, doc["owner_id"]))
    detail["auditor_team"] = row_to_dict(conn.execute("SELECT * FROM teams WHERE id = ?", (doc["auditor_team_id"],)).fetchone())
    detail["versions"] = versions
    detail["reviews"] = rows_to_dicts(conn.execute("SELECT * FROM reviews WHERE document_id = ? ORDER BY created_at DESC", (document_id,)).fetchall())
    detail["audits"] = rows_to_dicts(conn.execute("SELECT * FROM audits WHERE document_id = ? ORDER BY created_at DESC", (document_id,)).fetchall())
    detail["issues"] = rows_to_dicts(conn.execute("SELECT * FROM issues WHERE document_id = ? ORDER BY created_at DESC", (document_id,)).fetchall())
    detail["tasks"] = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE document_id = ? ORDER BY created_at DESC", (document_id,)).fetchall())
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
    return detail


def _required_publish_gaps(conn: Connection, doc: Row, version: Row) -> list[str]:
    gaps = []
    owner = perm.user_by_id(conn, doc["owner_id"])
    checklist = _loads(doc["audit_checklist_json"], [])
    if not version["title"].strip():
        gaps.append("titre de version")
    if owner is None or owner["active"] != 1:
        gaps.append("owner actif")
    if not doc["team_id"]:
        gaps.append("équipe responsable")
    if doc["confidentiality"] not in ("INTERNE", "EQUIPE", "RESTREINT"):
        gaps.append("confidentialité")
    if not version["source_location_label"].strip():
        gaps.append("emplacement de source fictif")
    if not version["placeholder_ref"].strip():
        gaps.append("référence placeholder")
    if not version["document_format"].strip():
        gaps.append("format du document")
    if doc["audit_frequency_value"] <= 0 or doc["audit_frequency_unit"] not in ("days", "months"):
        gaps.append("fréquence d'audit")
    if not doc["auditor_role"].strip():
        gaps.append("rôle auditeur")
    if not doc["audit_instructions"].strip() and not checklist:
        gaps.append("consignes ou checklist d'audit")
    return gaps


def _copy_checklist(conn: Connection, audit_id: int, document_id: str, checklist_json: str) -> None:
    checklist = _loads(checklist_json, [])
    if not checklist:
        checklist = ["Vérifier la pertinence", "Vérifier le propriétaire", "Vérifier la source placeholder"]
    for label in checklist:
        conn.execute(
            "INSERT INTO audit_checklist_items(audit_id, document_id, label) VALUES (?, ?, ?)",
            (audit_id, document_id, str(label)),
        )


def create_document(conn: Connection, user: Row, data: dict[str, Any]) -> dict:
    team_id = int(data.get("team_id") or 0)
    if not perm.can_create_document(conn, user, team_id):
        raise AppError(403, "Vous ne pouvez pas créer de document dans cette équipe.", "forbidden")
    title = (data.get("title") or "").strip()
    if not title:
        raise AppError(400, "Le titre est obligatoire.", "validation")
    confidentiality = data.get("confidentiality") or "EQUIPE"
    if confidentiality not in ("INTERNE", "EQUIPE", "RESTREINT"):
        raise AppError(400, "Confidentialité invalide.", "validation")
    owner_id = int(data.get("owner_id") or user["id"])
    owner = _active_user(conn, owner_id)
    if owner_id != user["id"] and not perm.can_administer(conn, user):
        raise AppError(403, "Seul un administrateur peut créer directement au nom d'un autre owner.", "forbidden")
    checklist = data.get("audit_checklist") or ["Validité métier", "Owner et équipe", "Source placeholder"]
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
            (document_id, owner_id, user["id"], "Owner initial", now),
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
                data.get("change_summary") or "Création initiale",
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
        raise AppError(409, "Seul un brouillon peut être modifié.", "conflict")
    if not perm.can_contribute(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas modifier ce brouillon.", "forbidden")
    ignored_sensitive = set(data) - VERSION_MUTABLE_FIELDS - {"row_version"}
    updates = {key: data[key] for key in VERSION_MUTABLE_FIELDS if key in data}
    if not updates and not ignored_sensitive:
        return document_detail(conn, user, doc["id"])
    expected = data.get("row_version")
    with transaction(conn):
        current = conn.execute("SELECT row_version FROM document_versions WHERE id = ?", (version_id,)).fetchone()
        if expected is not None and int(expected) != current["row_version"]:
            raise AppError(409, "Conflit de modification : la version a changé.", "conflict")
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
        raise AppError(409, "Cette proposition n'est pas en brouillon.", "invalid_transition")
    if not perm.can_contribute(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas soumettre cette proposition.", "forbidden")
    reviewer_id = reviewer_id or doc["owner_id"]
    reviewer = _active_user(conn, reviewer_id)
    if not perm.can_view_document(conn, reviewer, doc):
        raise AppError(422, "Le reviewer choisi n'a pas accès au document. Accordez-lui explicitement l'accès avant de l'attribuer.", "access_required")
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
            "Demande de review",
            f"{user['name']} a soumis une proposition pour {doc['title']}.",
            f"/#document={doc['id']}",
        )
        emit_log(conn, user["id"], "version_submitted", "document_version", version_id, document_id=doc["id"], changes={"reviewer_id": reviewer_id})
    return document_detail(conn, user, doc["id"])


def request_correction(conn: Connection, user: Row, version_id: int, comment: str) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if not comment.strip():
        raise AppError(400, "Un commentaire est obligatoire pour demander une correction.", "validation")
    if version["status"] != "CHALLENGE":
        raise AppError(409, "La proposition n'est pas en revue.", "invalid_transition")
    if not perm.can_review(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas demander de correction.", "forbidden")
    review = conn.execute(
        "SELECT * FROM reviews WHERE version_id = ? AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
        (version_id,),
    ).fetchone()
    if review is None:
        raise AppError(409, "Aucune review active.", "invalid_transition")
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
            "Corrections demandées",
            comment,
            f"/#document={doc['id']}",
        )
        emit_log(conn, user["id"], "correction_requested", "document_version", version_id, document_id=doc["id"], reason=comment)
    return document_detail(conn, user, doc["id"])


def request_intervention(conn: Connection, user: Row, version_id: int, data: dict[str, Any]) -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if version["status"] != "CHALLENGE":
        raise AppError(409, "La proposition doit rester en revue pour demander une intervention.", "invalid_transition")
    if not perm.can_review(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas demander d'intervention.", "forbidden")
    assignee_id = int(data.get("assignee_id") or 0)
    assignee = _active_user(conn, assignee_id)
    if not perm.can_view_document(conn, assignee, doc):
        raise AppError(422, "La personne choisie n'a pas accès au document. Un gestionnaire doit lui accorder l'accès explicitement.", "access_required")
    title = (data.get("title") or "Intervention demandée").strip()
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
            "Tâche attribuée",
            title,
            f"/#document={doc['id']}",
        )
        emit_log(conn, user["id"], "intervention_requested", "task", task_id, document_id=doc["id"], changes={"blocking": bool(blocking)})
    return document_detail(conn, user, doc["id"])


def approve_review(conn: Connection, user: Row, version_id: int, comment: str = "") -> dict:
    version, doc = _version_for_user(conn, user, version_id)
    if version["status"] != "CHALLENGE":
        raise AppError(409, "La proposition n'est pas en revue.", "invalid_transition")
    if not perm.can_review(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas approuver cette review.", "forbidden")
    review = conn.execute(
        "SELECT * FROM reviews WHERE version_id = ? AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
        (version_id,),
    ).fetchone()
    if review is None:
        raise AppError(409, "Aucune review active.", "invalid_transition")
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
            "Avis de review favorable",
            f"La proposition {version_id} a reçu un avis favorable.",
            f"/#document={doc['id']}",
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
        raise AppError(403, "Seul le data owner ou un gestionnaire explicite peut publier.", "forbidden")
    if version["status"] != "CHALLENGE":
        raise AppError(409, "Seule une proposition en revue peut être publiée.", "invalid_transition")
    gaps = _required_publish_gaps(conn, doc, version)
    if gaps:
        raise AppError(422, "Publication impossible : " + ", ".join(gaps) + ".", "missing_metadata")
    blockers = _open_blocking_tasks(conn, doc["id"], version_id)
    if blockers:
        raise AppError(409, "Publication bloquée par une intervention bloquante ouverte.", "blocking_task")
    now = utcnow()
    with transaction(conn):
        locked_doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc["id"],)).fetchone()
        locked_version = conn.execute("SELECT * FROM document_versions WHERE id = ?", (version_id,)).fetchone()
        if locked_version["status"] != "CHALLENGE":
            raise AppError(409, "La proposition a changé pendant la publication.", "conflict")
        if locked_version["change_type"] == "INITIAL":
            if locked_doc["current_version_id"] is not None:
                raise AppError(409, "Une version est déjà publiée.", "conflict")
            major, minor = 1, 0
        else:
            if locked_doc["current_version_id"] is None:
                raise AppError(409, "Aucune version courante pour cette modification.", "conflict")
            if locked_version["based_on_version_id"] != locked_doc["current_version_id"]:
                raise AppError(409, "La version de départ n'est plus la version courante.", "conflict")
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
                (f"Corrigé par publication {major}.{minor}", now, plan["id"]),
            )
            if plan["audit_id"]:
                conn.execute(
                    "UPDATE audits SET status = 'CLOSED', closed_at = ?, conclusion = CASE WHEN conclusion = '' THEN ? ELSE conclusion END, row_version = row_version + 1 WHERE id = ?",
                    (now, f"Clôturé par publication {major}.{minor}.", plan["audit_id"]),
                )
        conn.execute(
            "UPDATE flags SET active = 0, cleared_at = ? WHERE document_id = ? AND active = 1 AND flag_type = 'AUDIT_OVERDUE'",
            (now, doc["id"]),
        )
        notify(conn, locked_doc["owner_id"], doc["id"], f"published:{version_id}", "PUBLISHED", "Publication validée", f"Version {major}.{minor} publiée.", f"/#document={doc['id']}")
        emit_log(conn, user["id"], "version_published", "document_version", version_id, document_id=doc["id"], changes={"version": f"{major}.{minor}"})
    return document_detail(conn, user, doc["id"])


def review_only_validation(conn: Connection, user: Row, document_id: str, data: dict[str, Any] | None = None) -> dict:
    data = data or {}
    doc = _document_for_action(conn, user, document_id, "manage")
    if doc["current_version_id"] is None:
        raise AppError(409, "Une review seule exige une version publiée.", "invalid_transition")
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
            (document_id, doc["current_version_id"], audit_id, user["id"], doc["next_audit_due_at"], next_due, data.get("reason") or "Review validée sans modification", now),
        )
        if audit_id:
            audit = conn.execute("SELECT * FROM audits WHERE id = ? AND document_id = ?", (audit_id, document_id)).fetchone()
            if audit is None:
                raise AppError(404, "Audit introuvable.", "audit_not_found")
            unresolved = conn.execute(
                """
                SELECT 1 FROM issues
                WHERE audit_id = ? AND status NOT IN ('RESOLVED','DISMISSED')
                LIMIT 1
                """,
                (audit_id,),
            ).fetchone()
            if unresolved:
                raise AppError(409, "Les signalements liés doivent recevoir une résolution explicite.", "unresolved_issues")
            conn.execute(
                "UPDATE audits SET status = 'CLOSED', closed_at = ?, conclusion = CASE WHEN conclusion = '' THEN ? ELSE conclusion END, row_version = row_version + 1 WHERE id = ?",
                (now, data.get("reason") or "Review seule validée.", audit_id),
            )
        conn.execute(
            "UPDATE flags SET active = 0, cleared_at = ? WHERE document_id = ? AND active = 1 AND flag_type = 'AUDIT_OVERDUE'",
            (now, document_id),
        )
        notify(conn, doc["owner_id"], document_id, f"review-only:{now}", "VALIDATION", "Review validée", "Le numéro de version reste inchangé.", f"/#document={document_id}")
        emit_log(conn, user["id"], "review_only_validated", "document", document_id, document_id=document_id, reason=data.get("reason") or "")
    return document_detail(conn, user, document_id)


def create_issue(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "view")
    if doc["current_version_id"] is None:
        raise AppError(409, "Un signalement utilisateur porte sur un document publié.", "invalid_transition")
    if not perm.can_comment(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas signaler de problème ici.", "forbidden")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if not title or not description:
        raise AppError(400, "Titre et description sont obligatoires.", "validation")
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
        notify(conn, doc["owner_id"], document_id, f"issue:{issue_id}", "ISSUE_CREATED", "Nouveau signalement", title, f"/#document={document_id}")
        emit_log(conn, user["id"], "issue_created", "issue", issue_id, document_id=document_id, changes={"severity": data.get("severity") or "MOYENNE"})
    return document_detail(conn, user, document_id)


def resolve_issue(conn: Connection, user: Row, issue_id: int, data: dict[str, Any]) -> dict:
    issue = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    if issue is None:
        raise AppError(404, "Signalement introuvable.", "issue_not_found")
    doc = _document_for_action(conn, user, issue["document_id"], "manage")
    status = data.get("status") or "RESOLVED"
    if status not in ("RESOLVED", "DISMISSED"):
        raise AppError(400, "Résolution invalide.", "validation")
    reason = (data.get("resolution_reason") or "").strip()
    if status == "DISMISSED" and not reason:
        raise AppError(400, "Un motif est obligatoire pour écarter un signalement.", "validation")
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
        raise AppError(404, "Signalement introuvable.", "issue_not_found")
    doc = _document_for_action(conn, user, issue["document_id"], "view")
    if not perm.can_comment(conn, user, doc):
        raise AppError(403, "Commentaire interdit.", "forbidden")
    if not body.strip():
        raise AppError(400, "Le commentaire est obligatoire.", "validation")
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
            (doc["id"], "Échéance d'audit atteinte", now),
        )
    notify(
        conn,
        doc["owner_id"],
        doc["id"],
        f"audit-due:{doc['id']}:{due_at}",
        "AUDIT_DUE",
        "Audit à traiter",
        f"L'audit de {doc['title']} est arrivé à échéance.",
        f"/#document={doc['id']}",
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
                    (doc["id"], "Échéance d'audit dépassée", now),
                )
                flagged.append(doc["id"])
    return {"created_audits": created, "flagged_documents": flagged}


def open_manual_audit(conn: Connection, user: Row, document_id: str) -> dict:
    doc = _document_for_action(conn, user, document_id, "manage")
    if doc["current_version_id"] is None or doc["archived_at"] is not None:
        raise AppError(409, "Audit impossible sur un document jamais publié ou archivé.", "invalid_transition")
    with transaction(conn):
        try:
            _create_audit_locked(conn, doc, user["id"], utcnow(), manual=True)
        except IntegrityError as exc:
            raise AppError(409, "Un audit est déjà actif pour ce document.", "active_audit") from exc
    return document_detail(conn, user, document_id)


def claim_audit(conn: Connection, user: Row, audit_id: int) -> dict:
    audit = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    if audit is None:
        raise AppError(404, "Audit introuvable.", "audit_not_found")
    doc = _document_for_action(conn, user, audit["document_id"], "audit")
    if audit["status"] not in ("TO_DO", "IN_PROGRESS"):
        raise AppError(409, "Cet audit n'est pas prenable en charge.", "invalid_transition")
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
        raise AppError(404, "Audit introuvable.", "audit_not_found")
    doc = _document_for_action(conn, user, audit["document_id"], "audit")
    if audit["status"] not in ("TO_DO", "IN_PROGRESS"):
        raise AppError(409, "Audit non soumettable.", "invalid_transition")
    conclusion = (data.get("conclusion") or "").strip()
    if not conclusion:
        raise AppError(400, "Le compte rendu d'audit est obligatoire.", "validation")
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
        notify(conn, doc["owner_id"], doc["id"], f"audit-submitted:{audit_id}", "AUDIT_RESULT", "Résultat d'audit à décider", conclusion, f"/#document={doc['id']}")
        emit_log(conn, user["id"], "audit_submitted", "audit", audit_id, document_id=doc["id"], reason=conclusion)
    return document_detail(conn, user, doc["id"])


def decide_audit(conn: Connection, user: Row, audit_id: int, data: dict[str, Any]) -> dict:
    audit = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    if audit is None:
        raise AppError(404, "Audit introuvable.", "audit_not_found")
    doc = _document_for_action(conn, user, audit["document_id"], "manage")
    if audit["status"] != "AWAITING_OWNER_DECISION":
        raise AppError(409, "Cet audit n'attend pas une décision owner.", "invalid_transition")
    decision = data.get("decision_type")
    if decision not in ("REVIEW_ONLY", "EDIT", "NEW_VERSION", "ARCHIVE"):
        raise AppError(400, "Décision invalide.", "validation")
    justification = (data.get("justification") or "").strip()
    if not justification:
        raise AppError(400, "La justification est obligatoire.", "validation")
    if decision == "REVIEW_ONLY":
        open_issues = conn.execute(
            "SELECT * FROM issues WHERE audit_id = ? AND status NOT IN ('RESOLVED','DISMISSED')",
            (audit_id,),
        ).fetchall()
        if open_issues:
            raise AppError(409, "Résolvez, écartez ou acceptez explicitement les signalements avant une review seule.", "unresolved_issues")
        return review_only_validation(conn, user, doc["id"], {"audit_id": audit_id, "reason": justification})
    if decision == "ARCHIVE":
        return archive_document(conn, user, doc["id"], {"reason": justification, "audit_id": audit_id})
    if doc["current_version_id"] is None:
        raise AppError(409, "Une correction exige une version publiée.", "invalid_transition")
    primary_id = int(data.get("primary_responsible_id") or user["id"])
    primary = _active_user(conn, primary_id)
    if not perm.can_view_document(conn, primary, doc):
        raise AppError(422, "Le responsable choisi n'a pas accès au document.", "access_required")
    reviewer_id = data.get("reviewer_id") or doc["owner_id"]
    reviewer = _active_user(conn, int(reviewer_id))
    if not perm.can_view_document(conn, reviewer, doc):
        raise AppError(422, "Le reviewer choisi n'a pas accès au document.", "access_required")
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
            for task in data.get("tasks") or [{"title": "Réaliser le plan d'intervention", "instructions": justification, "blocking": True, "assignee_id": primary_id}]:
                assignee_id = int(task.get("assignee_id") or primary_id)
                assignee = _active_user(conn, assignee_id)
                if not perm.can_view_document(conn, assignee, doc):
                    raise AppError(422, "Un intervenant choisi n'a pas accès au document.", "access_required")
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
                        task.get("title") or "Tâche d'intervention",
                        task.get("instructions") or "",
                        assignee_id,
                        0 if task.get("blocking") is False else 1,
                        user["id"],
                        now,
                    ),
                ).lastrowid
                notify(conn, assignee_id, doc["id"], f"task:{task_id}", "TASK_ASSIGNED", "Tâche attribuée", task.get("title") or "Tâche d'intervention", f"/#document={doc['id']}")
            conn.execute(
                "UPDATE audits SET status = 'REMEDIATION_IN_PROGRESS', decision = ?, owner_decision_reason = ?, row_version = row_version + 1 WHERE id = ?",
                (decision, justification, audit_id),
            )
            emit_log(conn, user["id"], "audit_decided", "audit", audit_id, document_id=doc["id"], changes={"decision": decision, "plan_id": plan_id, "version_id": version_id})
        except IntegrityError as exc:
            raise AppError(409, "Une proposition active existe déjà pour ce document.", "active_proposal") from exc
    return document_detail(conn, user, doc["id"])


def create_proposal(conn: Connection, user: Row, document_id: str, data: dict[str, Any]) -> dict:
    doc = _document_for_action(conn, user, document_id, "contribute")
    if doc["current_version_id"] is None:
        raise AppError(409, "Utilisez le brouillon initial tant qu'aucune version n'est publiée.", "invalid_transition")
    change_type = data.get("change_type")
    if change_type not in ("EDIT", "NEW_VERSION"):
        raise AppError(400, "Le type de changement doit être EDIT ou NEW_VERSION.", "validation")
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
        raise AppError(409, "Une seule proposition active est autorisée par document.", "active_proposal") from exc
    return document_detail(conn, user, document_id)


def update_task_status(conn: Connection, user: Row, task_id: int, status: str, reason: str = "") -> dict:
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise AppError(404, "Tâche introuvable.", "task_not_found")
    doc = _document_for_action(conn, user, task["document_id"], "view")
    if task["assignee_id"] != user["id"] and not perm.can_manage_document(conn, user, doc):
        raise AppError(403, "Vous ne pouvez pas mettre à jour cette tâche.", "forbidden")
    if status not in ("TODO", "IN_PROGRESS", "DONE", "CANCELLED"):
        raise AppError(400, "Statut de tâche invalide.", "validation")
    if status == "CANCELLED" and not reason.strip():
        raise AppError(400, "Un motif est obligatoire pour annuler une tâche.", "validation")
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
        raise AppError(400, "Indiquez un utilisateur ou une équipe.", "validation")
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise AppError(400, "Un motif est obligatoire.", "validation")
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
        raise AppError(404, "Accès introuvable.", "grant_not_found")
    _document_for_action(conn, user, grant["document_id"], "manage")
    if not reason.strip():
        raise AppError(400, "Un motif est obligatoire.", "validation")
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
        raise AppError(400, "Un motif est obligatoire pour transférer un document.", "validation")
    if not perm.can_view_document(conn, new_owner, doc):
        raise AppError(422, "Le nouveau owner doit d'abord recevoir un accès explicite.", "access_required")
    with transaction(conn):
        conn.execute("UPDATE documents SET owner_id = ?, row_version = row_version + 1 WHERE id = ?", (new_owner_id, document_id))
        conn.execute(
            """
            INSERT INTO document_access_grants(document_id, user_id, can_view, can_comment, can_contribute, can_review, can_audit, can_manage, granted_by, reason, created_at)
            VALUES (?, ?, 1, 1, 1, 1, 1, 1, ?, ?, ?)
            """,
            (document_id, new_owner_id, user["id"], "Transfert owner: " + reason, utcnow()),
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
        raise AppError(400, "Un motif d'archivage est obligatoire.", "validation")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE documents SET archived_at = ?, archive_reason = ?, row_version = row_version + 1 WHERE id = ?",
            (now, reason, document_id),
        )
        conn.execute(
            "UPDATE document_versions SET status = 'CANCELLED', cancelled_reason = ?, row_version = row_version + 1 WHERE document_id = ? AND status IN ('DRAFT','CHALLENGE')",
            ("Annulé par archivage: " + reason, document_id),
        )
        conn.execute(
            "UPDATE tasks SET status = 'CANCELLED', cancel_reason = ?, row_version = row_version + 1 WHERE document_id = ? AND status IN ('TODO','IN_PROGRESS')",
            ("Annulé par archivage: " + reason, document_id),
        )
        conn.execute(
            "UPDATE audits SET status = 'CLOSED', closed_at = ?, conclusion = ? WHERE document_id = ? AND status NOT IN ('CLOSED','CANCELLED')",
            (now, "Document archivé: " + reason, document_id),
        )
        conn.execute(
            """
            UPDATE issues
            SET status = 'DISMISSED', resolution = 'Document archivé', resolution_reason = ?, resolved_at = ?, row_version = row_version + 1
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
        raise AppError(404, "Flag introuvable.", "flag_not_found")
    _document_for_action(conn, user, flag["document_id"], "manage")
    reminder_at = (data.get("reminder_at") or "").strip()
    reason = (data.get("reason") or "").strip()
    if not reminder_at or not reason:
        raise AppError(400, "Date de rappel et motif sont obligatoires.", "validation")
    with transaction(conn):
        conn.execute(
            "UPDATE flags SET snoozed_until = ?, snooze_reason = ? WHERE id = ?",
            (reminder_at, reason, flag_id),
        )
        emit_log(conn, user["id"], "flag_snoozed", "flag", flag_id, document_id=flag["document_id"], reason=reason)
    return document_detail(conn, user, flag["document_id"])


def list_notifications(conn: Connection, user: Row) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM notifications WHERE recipient_id = ? ORDER BY read_at IS NOT NULL, created_at DESC",
        (user["id"],),
    ).fetchall()
    return rows_to_dicts(rows)


def mark_notification_read(conn: Connection, user: Row, notification_id: int) -> dict:
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET read_at = COALESCE(read_at, ?) WHERE id = ? AND recipient_id = ?",
            (utcnow(), notification_id, user["id"]),
        )
    return {"ok": True}


def admin_create_user(conn: Connection, user: Row, data: dict[str, Any], password_hash: str) -> dict:
    if not perm.can_administer(conn, user):
        raise AppError(403, "Administration réservée aux administrateurs.", "forbidden")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    if not name or not email:
        raise AppError(400, "Nom et email sont obligatoires.", "validation")
    with transaction(conn):
        user_id = conn.execute(
            "INSERT INTO users(name, email, password_hash, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (name, email, password_hash, utcnow()),
        ).lastrowid
        emit_log(conn, user["id"], "user_created", "user", user_id, reason="Administration", changes={"email": email})
    return {"id": user_id, "name": name, "email": email, "active": 1}
