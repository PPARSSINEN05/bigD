CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE
);

CREATE TABLE team_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(user_id, team_id)
);

CREATE TABLE role_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('USER','CONTRIBUTOR','AUDITOR','REVIEWER','DATA_OWNER','ADMIN')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_by INTEGER REFERENCES users(id),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, team_id, role)
);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    team_id INTEGER NOT NULL REFERENCES teams(id),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    confidentiality TEXT NOT NULL DEFAULT 'EQUIPE' CHECK (confidentiality IN ('INTERNE','EQUIPE','RESTREINT')),
    audit_frequency_value INTEGER NOT NULL DEFAULT 12 CHECK (audit_frequency_value > 0),
    audit_frequency_unit TEXT NOT NULL DEFAULT 'months' CHECK (audit_frequency_unit IN ('days','months')),
    auditor_role TEXT NOT NULL DEFAULT 'AUDITOR',
    auditor_team_id INTEGER NOT NULL REFERENCES teams(id),
    audit_instructions TEXT NOT NULL DEFAULT '',
    audit_checklist_json TEXT NOT NULL DEFAULT '[]',
    current_version_id INTEGER,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    last_validation_at TEXT,
    next_audit_due_at TEXT,
    archived_at TEXT,
    archive_reason TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE document_access_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
    can_view INTEGER NOT NULL DEFAULT 1 CHECK (can_view IN (0, 1)),
    can_comment INTEGER NOT NULL DEFAULT 0 CHECK (can_comment IN (0, 1)),
    can_contribute INTEGER NOT NULL DEFAULT 0 CHECK (can_contribute IN (0, 1)),
    can_review INTEGER NOT NULL DEFAULT 0 CHECK (can_review IN (0, 1)),
    can_audit INTEGER NOT NULL DEFAULT 0 CHECK (can_audit IN (0, 1)),
    can_manage INTEGER NOT NULL DEFAULT 0 CHECK (can_manage IN (0, 1)),
    granted_by INTEGER NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    CHECK (user_id IS NOT NULL OR team_id IS NOT NULL)
);

CREATE TABLE document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    major INTEGER NOT NULL DEFAULT 0 CHECK (major >= 0),
    minor INTEGER NOT NULL DEFAULT 0 CHECK (minor >= 0),
    target_major INTEGER NOT NULL DEFAULT 1 CHECK (target_major >= 0),
    target_minor INTEGER NOT NULL DEFAULT 0 CHECK (target_minor >= 0),
    change_type TEXT NOT NULL DEFAULT 'INITIAL' CHECK (change_type IN ('INITIAL','EDIT','NEW_VERSION')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_location_type TEXT NOT NULL DEFAULT 'SharePoint',
    source_location_label TEXT NOT NULL DEFAULT '',
    document_format TEXT NOT NULL DEFAULT 'PDF',
    placeholder_ref TEXT NOT NULL DEFAULT '',
    change_summary TEXT NOT NULL DEFAULT '',
    author_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('DRAFT','CHALLENGE','UP','CANCELLED')),
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    published_at TEXT,
    validator_id INTEGER REFERENCES users(id),
    based_on_version_id INTEGER REFERENCES document_versions(id),
    intervention_plan_id INTEGER,
    cancelled_reason TEXT,
    immutable_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX ux_versions_published_number
ON document_versions(document_id, major, minor)
WHERE status = 'UP';

CREATE UNIQUE INDEX ux_versions_one_active_proposal
ON document_versions(document_id)
WHERE status IN ('DRAFT', 'CHALLENGE');

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    reviewer_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','CHANGES_REQUESTED','CANCELLED')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE review_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE validation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id INTEGER REFERENCES document_versions(id),
    audit_id INTEGER,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('INITIAL_PUBLICATION','REVIEW_ONLY','EDIT_PUBLICATION','NEW_VERSION_PUBLICATION','ARCHIVE')),
    previous_due_at TEXT,
    next_due_at TEXT,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    opened_by_id INTEGER REFERENCES users(id),
    assignee_id INTEGER REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('TO_DO','IN_PROGRESS','AWAITING_OWNER_DECISION','REMEDIATION_IN_PROGRESS','AWAITING_FINAL_VALIDATION','CLOSED','CANCELLED')),
    due_at TEXT NOT NULL,
    started_at TEXT,
    submitted_at TEXT,
    closed_at TEXT,
    cancelled_reason TEXT,
    conclusion TEXT NOT NULL DEFAULT '',
    decision TEXT,
    owner_decision_reason TEXT,
    reminder_at TEXT,
    reminder_reason TEXT,
    created_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX ux_audits_one_active
ON audits(document_id)
WHERE status IN ('TO_DO','IN_PROGRESS','AWAITING_OWNER_DECISION','REMEDIATION_IN_PROGRESS','AWAITING_FINAL_VALIDATION');

CREATE UNIQUE INDEX ux_audits_due
ON audits(document_id, due_at);

CREATE TABLE audit_checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    is_checked INTEGER NOT NULL DEFAULT 0 CHECK (is_checked IN (0, 1)),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id INTEGER REFERENCES document_versions(id),
    audit_id INTEGER REFERENCES audits(id),
    intervention_plan_id INTEGER,
    author_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MOYENNE',
    suggestions TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','UNDER_REVIEW','ACCEPTED','IN_PROGRESS','RESOLVED','DISMISSED')),
    resolution TEXT,
    resolution_reason TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE issue_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE intervention_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    audit_id INTEGER REFERENCES audits(id),
    version_id INTEGER REFERENCES document_versions(id),
    decision_type TEXT NOT NULL CHECK (decision_type IN ('REVIEW_ONLY','EDIT','NEW_VERSION','ARCHIVE')),
    justification TEXT NOT NULL,
    primary_responsible_id INTEGER REFERENCES users(id),
    reviewer_id INTEGER REFERENCES users(id),
    target_date TEXT,
    resources_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_PROGRESS','CLOSED','CANCELLED')),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    intervention_plan_id INTEGER REFERENCES intervention_plans(id),
    audit_id INTEGER REFERENCES audits(id),
    version_id INTEGER REFERENCES document_versions(id),
    title TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    assignee_id INTEGER REFERENCES users(id),
    blocking INTEGER NOT NULL DEFAULT 1 CHECK (blocking IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'TODO' CHECK (status IN ('TODO','IN_PROGRESS','DONE','CANCELLED')),
    cancel_reason TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    flag_type TEXT NOT NULL CHECK (flag_type IN ('AUDIT_OVERDUE')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    reason TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    cleared_at TEXT,
    snoozed_until TEXT,
    snooze_reason TEXT
);

CREATE UNIQUE INDEX ux_flags_one_active
ON flags(document_id, flag_type)
WHERE active = 1;

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(recipient_id, event_key)
);

CREATE TABLE activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    changes_json TEXT NOT NULL DEFAULT '{}',
    technical INTEGER NOT NULL DEFAULT 0 CHECK (technical IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX ix_documents_team ON documents(team_id);
CREATE INDEX ix_documents_owner ON documents(owner_id);
CREATE INDEX ix_documents_due ON documents(next_audit_due_at);
CREATE INDEX ix_document_versions_document ON document_versions(document_id);
CREATE INDEX ix_tasks_assignee ON tasks(assignee_id);
CREATE INDEX ix_notifications_recipient ON notifications(recipient_id, read_at);
CREATE INDEX ix_activity_document ON activity_logs(document_id, created_at);
