const state = {
  user: null,
  csrf: null,
  config: { app_name: "BigD", app_subtitle: "Document governance", app_tagline: "dream Big", app_title: "BigD, dream Big", brand_mark: "BD", logo_path: "/logo.svg" },
  reference: null,
  route: { name: "dashboard", params: {}, query: new URLSearchParams() },
  loading: false,
  error: "",
  notificationCount: 0,
  searchAbort: null,
  searchTimer: null,
};

const labels = {
  DRAFT: "Draft",
  CHALLENGE: "In review",
  UP: "Published",
  CANCELLED: "Cancelled",
  TO_DO: "To do",
  IN_PROGRESS: "In progress",
  AWAITING_OWNER_DECISION: "Owner decision",
  REMEDIATION_IN_PROGRESS: "Correction",
  AWAITING_FINAL_VALIDATION: "Final validation",
  CLOSED: "Closed",
  OPEN: "Open",
  UNDER_REVIEW: "To decide",
  ACCEPTED: "Accepted",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
  EDIT: "Minor edit",
  NEW_VERSION: "Major version",
  INITIAL: "Initial",
};

const confidentialityLabels = {
  INTERNE: "Internal",
  EQUIPE: "Team",
  RESTREINT: "Restricted",
};

const severityLabels = {
  BASSE: "Low",
  MOYENNE: "Medium",
  HAUTE: "High",
};

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function formData(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const item of form.querySelectorAll("input[type='checkbox']")) data[item.name] = item.checked;
  for (const item of form.querySelectorAll("[data-number='true']")) {
    if (data[item.name] !== "") data[item.name] = Number(data[item.name]);
  }
  return data;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.error?.message || `Error ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function boot() {
  try {
    state.config = await api("/api/config");
  } catch {
    // Public config has a local fallback.
  }
  try {
    const session = await api("/api/session");
    state.user = session.user;
    state.csrf = session.csrf_token;
    state.config = session.config || state.config;
    await loadReference();
    await loadNotificationCount();
    routeFromHash();
  } catch {
    renderLogin();
  }
}

async function loadReference() {
  state.reference = await api("/api/reference");
}

async function loadNotificationCount() {
  if (!state.user) return;
  const data = await api("/api/notifications?unread=1&limit=1");
  state.notificationCount = data.unread || 0;
}

function navigate(path) {
  location.hash = path.startsWith("#") ? path : `#/${path.replace(/^\/+/, "")}`;
}

function routeFromHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [pathPart, queryPart = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const query = new URLSearchParams(queryPart);
  const route = { name: parts[0] || "dashboard", params: {}, query };
  if (route.name === "documents" && parts[1] === "new") route.name = "new-document";
  else if (route.name === "documents" && parts[1]) route.params.documentId = decodeURIComponent(parts[1]);
  else if (route.name === "audits" && parts[1]) route.params.auditId = Number(parts[1]);
  else if (route.name === "work" && parts[1]) route.params.kind = parts[1];
  state.route = route;
  state.error = "";
  render();
}

window.addEventListener("hashchange", routeFromHash);

async function runAction(fn) {
  state.loading = true;
  state.error = "";
  render();
  try {
    await fn();
    await loadNotificationCount();
    routeFromHash();
  } catch (error) {
    state.error = error.message;
    render();
  } finally {
    state.loading = false;
  }
}

function renderLogin() {
  document.title = state.config.app_title;
  const root = document.querySelector("#app");
  root.replaceChildren(
    h("main", { class: "login" },
      h("form", {
        class: "login-box",
        onsubmit: async (event) => {
          event.preventDefault();
          const data = formData(event.currentTarget);
          try {
            const session = await api("/api/login", { method: "POST", body: data });
            state.user = session.user;
            state.csrf = session.csrf_token;
            state.config = session.config || state.config;
            await loadReference();
            await loadNotificationCount();
            navigate("dashboard");
            routeFromHash();
          } catch (error) {
            event.currentTarget.querySelector("[data-error]").textContent = error.message;
          }
        }
      },
        brand(),
        h("h1", {}, "Sign in"),
        h("p", { class: "muted" }, `${state.config.app_title} · prototype application with fictional data.`),
        h("label", {}, "Email", h("input", { name: "email", type: "email", required: true, autocomplete: "username" })),
        h("label", {}, "Password", h("input", { name: "password", type: "password", required: true, autocomplete: "current-password" })),
        h("div", { class: "notice error", "data-error": "" }),
        h("button", { class: "primary", type: "submit" }, "Sign in"),
        h("div", { id: "dev-users", class: "small muted" })
      )
    )
  );
  loadDevUsers();
}

async function loadDevUsers() {
  try {
    const data = await api("/api/dev/users");
    const box = document.querySelector("#dev-users");
    if (!box) return;
    const select = h("select", { onchange: (event) => {
      document.querySelector("[name='email']").value = event.target.value;
      document.querySelector("[name='password']").value = data.demo_password;
    }}, h("option", { value: "" }, "Demo accounts"));
    data.users.forEach(user => select.append(h("option", { value: user.email }, `${user.name} · ${user.email}`)));
    box.replaceChildren(h("label", {}, "Development mode", select));
  } catch {
    // Hidden outside development.
  }
}

function brand() {
  const mark = state.config.logo_path
    ? h("img", { class: "brand-logo", src: state.config.logo_path, alt: state.config.app_title })
    : h("span", { class: "brand-mark" }, state.config.brand_mark);
  return h("div", { class: "brand" }, mark, h("span", { class: "brand-title" }, state.config.app_title));
}

function render() {
  if (!state.user) return renderLogin();
  let title = "Dashboard";
  let content;
  if (state.route.name === "documents" && state.route.params.documentId) {
    title = "Document record";
    content = asyncPanel(loadDocumentDetail(state.route.params.documentId), viewDocumentDetail);
  } else if (state.route.name === "documents") {
    title = "Documents";
    content = asyncPanel(loadDocumentsFromRoute(), viewDocuments);
  } else if (state.route.name === "new-document") {
    title = "Create document";
    content = viewCreateDocument();
  } else if (state.route.name === "audits" && state.route.params.auditId) {
    title = "Audit detail";
    content = asyncPanel(api(`/api/audits/${state.route.params.auditId}`), viewAuditDetail);
  } else if (state.route.name === "audits") {
    title = "Audits";
    content = asyncPanel(api("/api/audits?state=active"), viewAudits);
  } else if (state.route.name === "tasks") {
    title = "My tasks";
    content = asyncPanel(api("/api/tasks?status=open"), viewTasks);
  } else if (state.route.name === "notifications") {
    title = "Notifications";
    content = asyncPanel(loadNotifications(), viewNotifications);
  } else if (state.route.name === "admin") {
    title = "Administration";
    content = viewAdmin();
  } else if (state.route.name === "work") {
    title = "Work list";
    content = asyncPanel(api(`/api/work-items?kind=${encodeURIComponent(state.route.params.kind || "")}`), viewWorkItems);
  } else {
    content = asyncPanel(api("/api/dashboard?scope=mine"), viewDashboard);
  }
  document.title = `${title} · ${state.config.app_name}`;
  document.querySelector("#app").replaceChildren(shell(content, title));
}

function asyncPanel(promise, renderer) {
  const box = h("div", { class: "grid" }, h("div", { class: "empty" }, "Loading"));
  promise.then(data => {
    box.replaceChildren(renderer(data));
  }).catch(error => {
    box.replaceChildren(h("div", { class: "notice error" }, error.message));
  });
  return box;
}

function shell(content, title) {
  const nav = [
    ["dashboard", "Dashboard", "dashboard"],
    ["documents", "Documents", "documents"],
    ["audits", "Audits", "audits"],
    ["tasks", "My tasks", "tasks"],
    ["notifications", "Notifications", "notifications"],
  ];
  if (state.reference?.is_admin) nav.push(["admin", "Administration", "admin"]);
  return h("div", { class: "shell" },
    h("aside", { class: "sidebar compact" },
      brand(),
      h("nav", { class: "nav" }, nav.map(([name, label, path]) => h("button", {
        class: state.route.name === name ? "active" : "",
        onclick: () => navigate(path)
      }, label))),
      h("div", { class: "userbox" },
        h("strong", {}, state.user.name),
        h("span", {}, state.user.email),
        h("button", { class: "ghost", onclick: () => runAction(async () => { await api("/api/logout", { method: "POST" }); state.user = null; renderLogin(); }) }, "Sign out")
      )
    ),
    h("main", { class: "main" },
      h("header", { class: "topbar app-header" },
        h("div", {}, h("div", { class: "breadcrumb" }, breadcrumb()), h("h1", {}, title)),
        globalSearch(),
        h("button", { class: "bell", title: "Notifications", onclick: () => navigate("notifications") }, `Notifications${state.notificationCount ? ` · ${state.notificationCount}` : ""}`)
      ),
      state.error ? h("div", { class: "notice error" }, state.error) : null,
      content
    )
  );
}

function breadcrumb() {
  if (state.route.name === "documents" && state.route.params.documentId) return "Documents -> Record";
  if (state.route.name === "audits" && state.route.params.auditId) return "Documents → Audit";
  if (state.route.name === "work") return "Dashboard -> List";
  return state.config.app_name;
}

function globalSearch() {
  const results = h("div", { class: "search-results", hidden: true });
  const input = h("input", {
    type: "search",
    placeholder: "Search documents",
    value: state.route.name === "documents" ? (state.route.query.get("search") || "") : "",
    oninput: (event) => {
      clearTimeout(state.searchTimer);
      const q = event.target.value.trim();
      state.searchTimer = setTimeout(() => searchSuggestions(q, results), 250);
    },
    onkeydown: (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        navigate(`documents?search=${encodeURIComponent(event.currentTarget.value.trim())}`);
      }
    }
  });
  return h("div", { class: "global-search" }, input, results);
}

async function searchSuggestions(q, results) {
  if (state.searchAbort) state.searchAbort.abort();
  if (!q) {
    results.hidden = true;
    results.replaceChildren();
    return;
  }
  state.searchAbort = new AbortController();
  try {
    const data = await api(`/api/documents?search=${encodeURIComponent(q)}&per_page=5`, { signal: state.searchAbort.signal });
    results.hidden = false;
    results.replaceChildren(
      ...(data.items.map(doc => h("button", { onclick: () => navigate(`documents/${encodeURIComponent(doc.id)}`) }, doc.title, h("span", { class: "muted small" }, doc.publication_label)))),
      h("button", { class: "primary", onclick: () => navigate(`documents?search=${encodeURIComponent(q)}`) }, `View all results (${data.total})`)
    );
  } catch (error) {
    if (error.name !== "AbortError") results.hidden = true;
  }
}

function viewDashboard(data) {
  return h("div", { class: "grid" },
    h("section", { class: "panel work-scope" },
      h("div", {}, h("strong", {}, data.scope_label), h("p", { class: "muted" }, "Server-side counters based on the documents you are allowed to access.")),
      h("button", { onclick: () => navigate("documents") }, `${data.accessible_document_count} accessible documents`)
    ),
    h("div", { class: "grid cols-4" }, data.metrics.map(metricCard)),
    section("Priority work", listOrEmpty(data.priority_items, priorityCard, "No priority actions right now.")),
    h("div", { class: "grid cols-3" },
      section("Upcoming deadlines", listOrEmpty(data.upcoming_deadlines, deadlineCard, "No deadlines in the next 30 days.")),
      section("Recent activity", listOrEmpty(data.recent_activity, activityCard, "No recent accessible activity.")),
      section("Latest notifications", listOrEmpty(data.notifications, notificationCard, "No notifications."))
    )
  );
}

function metricCard(metric) {
  const color = metric.key === "overdue_audits" ? "red" : metric.value ? "amber" : "green";
  return h("button", { class: "panel metric-card", onclick: () => navigate(metric.href.replace(/^#\//, "")) },
    h("span", { class: `badge ${color}` }, metric.label),
    h("strong", { class: "metric-value" }, metric.value),
    h("span", { class: "muted small" }, "Open list")
  );
}

function priorityCard(item) {
  return h("div", { class: "card priority-row" },
    h("div", {}, h("strong", {}, item.action), h("div", { class: "muted small" }, `${item.document_title} · ${item.version}`)),
    h("div", { class: "badges" },
      h("span", { class: `badge ${item.kind === "audit" && item.overdue_days ? "red" : "blue"}` }, item.kind),
      item.blocked ? h("span", { class: "badge red" }, item.block_reason || "Blocked") : null,
      item.due_at ? h("span", { class: "badge" }, item.overdue_days ? `${item.overdue_days} d overdue` : formatDate(item.due_at)) : null
    ),
    h("div", { class: "muted small" }, `Responsible: ${item.responsible || "-"}`),
    h("button", { class: "primary", onclick: () => navigate(item.href.replace(/^#\//, "")) }, item.action_label)
  );
}

function deadlineCard(doc) {
  return h("button", { class: "card doc-link", onclick: () => navigate(`documents/${encodeURIComponent(doc.id)}`) },
    h("strong", {}, doc.title),
    h("span", { class: "muted small" }, `${doc.audit_label} · ${formatDate(doc.next_audit_due_at)}`)
  );
}

function activityCard(item) {
  return h("button", { class: "card doc-link", onclick: () => navigate(`documents/${encodeURIComponent(item.document_id)}`) },
    h("strong", {}, item.action),
    h("span", { class: "muted small" }, `${item.document_title || ""} · ${item.actor_name || "System"} · ${formatDate(item.created_at)}`)
  );
}

function viewWorkItems(data) {
  return h("div", { class: "grid" },
    h("div", { class: "notice" }, `${data.total} item(s) in the same scope as the counter.`),
    listOrEmpty(data.items, priorityCard, "No items in this list.")
  );
}

async function loadDocumentsFromRoute() {
  const params = new URLSearchParams(state.route.query.toString());
  if (!params.get("per_page")) params.set("per_page", "12");
  return api(`/api/documents?${params.toString()}`);
}

function viewDocuments(data) {
  const q = state.route.query;
  return h("div", { class: "grid" },
    h("section", { class: "panel" },
      h("div", { class: "section-title" }, h("h2", {}, "Document explorer"), h("button", { class: "primary", onclick: () => navigate("documents/new") }, "Create document")),
      quickFilters(),
      h("form", { class: "toolbar", onsubmit: (event) => {
        event.preventDefault();
        const params = new URLSearchParams(formData(event.currentTarget));
        if (event.currentTarget.include_archived.checked) params.set("include_archived", "1");
        else params.delete("include_archived");
        navigate(`documents?${params.toString()}`);
      }},
        h("label", {}, "Search", h("input", { name: "search", value: q.get("search") || "", placeholder: "Title, ID, owner, tags" })),
        selectFieldWithValue("Team", "team_id", [["", "All"], ...(state.reference?.teams || []).map(t => [t.id, t.name])], q.get("team_id"), false, true),
        selectFieldWithValue("Owner", "owner_id", [["", "All"], ...(state.reference?.users || []).map(u => [u.id, u.name])], q.get("owner_id"), false, true),
        selectFieldWithValue("Confidentiality", "confidentiality", [["", "All"], ["INTERNE", "Internal"], ["EQUIPE", "Team"], ["RESTREINT", "Restricted"]], q.get("confidentiality")),
        selectFieldWithValue("Publication", "publication_state", [["", "All"], ["published", "Published"], ["draft", "Initial draft"], ["unpublished", "Unpublished"], ["archived", "Archived"]], q.get("publication_state")),
        selectFieldWithValue("Proposal", "proposal_state", [["", "All"], ["none", "None"], ["draft", "Draft"], ["challenge", "In review"]], q.get("proposal_state")),
        selectFieldWithValue("Audit", "audit_state", [["", "All"], ["up_to_date", "Up to date"], ["upcoming", "Upcoming"], ["in_progress", "In progress"], ["decision", "Validation pending"], ["overdue", "Overdue"]], q.get("audit_state")),
        selectFieldWithValue("Sort", "sort", [["relevance", "Relevance"], ["title", "Title"], ["last_validation", "Last validation"], ["next_due", "Next due date"]], q.get("sort") || "relevance"),
        h("label", {}, "Archives", h("span", { class: "row-actions" }, h("input", { type: "checkbox", name: "include_archived", checked: q.get("include_archived") === "1" }), "Include")),
        h("div", { class: "row-actions" }, h("button", { class: "primary" }, "Apply"), h("button", { type: "button", onclick: () => navigate("documents") }, "Reset"))
      ),
      h("div", { class: "active-filters" }, [...q.entries()].filter(([, value]) => value).map(([key, value]) => h("span", { class: "badge blue" }, `${key}: ${value}`)))
    ),
    h("section", { class: "panel" },
      h("div", { class: "section-title" }, h("h2", {}, `${data.total} authorized result(s)`)),
      data.items.length ? documentTable(data.items) : h("div", { class: "empty" }, "No accessible documents match these filters.")
    )
  );
}

function quickFilters() {
  return h("div", { class: "quick-filters" },
    h("button", { type: "button", onclick: () => navigate("documents") }, "All accessible documents"),
    h("button", { type: "button", onclick: () => navigate("documents?quick=mine") }, "My documents"),
    h("button", { type: "button", onclick: () => navigate("documents?quick=audit") }, "To audit"),
    h("button", { type: "button", onclick: () => navigate("documents?quick=preparation") }, "In preparation"),
    h("button", { type: "button", onclick: () => navigate("documents?quick=archives&include_archived=1") }, "Archives")
  );
}

function documentTable(items) {
  return h("div", { class: "doc-table" },
    h("div", { class: "doc-table-head" }, "Document", "Publication", "Proposal", "Audit", "Owner", "Due date", "Confidentiality"),
    ...items.map(doc => h("button", { class: "doc-table-row", onclick: () => navigate(`documents/${encodeURIComponent(doc.id)}`) },
      h("span", {}, h("strong", {}, doc.title), h("small", {}, doc.id)),
      statusBadge(doc.publication_label, doc.publication_state === "published" ? "green" : doc.publication_state === "archived" ? "grey" : "blue"),
      statusBadge(doc.proposal_label, doc.proposal_state === "none" ? "grey" : doc.proposal_state === "challenge" ? "blue" : "grey"),
      statusBadge(doc.audit_label, doc.audit_state === "overdue" ? "red" : doc.audit_state === "up_to_date" ? "green" : "amber"),
      h("span", {}, doc.owner_name),
      h("span", {}, formatDate(doc.next_audit_due_at)),
      statusBadge(confidentialityLabels[doc.confidentiality] || doc.confidentiality, "blue")
    ))
  );
}

function loadDocumentDetail(id) {
  return api(`/api/documents/${encodeURIComponent(id)}`);
}

function viewDocumentDetail(doc) {
  const tab = state.route.query.get("tab") || "overview";
  return h("div", { class: "grid" },
    h("section", { class: "panel detail-hero" },
      h("div", {}, h("div", { class: "breadcrumb" }, "Documents -> " + doc.title), h("h2", {}, doc.title), h("p", {}, doc.summary)),
      h("div", { class: "badges" },
        statusBadge(doc.publication_label, doc.publication_state === "published" ? "green" : doc.publication_state === "archived" ? "grey" : "blue"),
        statusBadge(doc.proposal_label, doc.proposal_state === "none" ? "grey" : "blue"),
        statusBadge(doc.audit_label, doc.audit_state === "overdue" ? "red" : doc.audit_state === "up_to_date" ? "green" : "amber"),
        statusBadge(confidentialityLabels[doc.confidentiality] || doc.confidentiality, "blue")
      ),
      primaryDocumentAction(doc)
    ),
    h("nav", { class: "tabs" }, ["overview", "versions", "audits", "issues", "history", ...(doc.permissions.can_manage ? ["access"] : [])].map(name => h("button", {
      class: tab === name ? "active" : "",
      onclick: () => navigate(`documents/${encodeURIComponent(doc.id)}?tab=${name}`)
    }, tabLabel(name)))),
    tabContent(doc, tab)
  );
}

function primaryDocumentAction(doc) {
  const proposal = doc.active_proposal;
  const audit = doc.active_audit;
  if (audit && audit.status === "AWAITING_OWNER_DECISION" && doc.permissions.can_manage) return h("button", { class: "primary", onclick: () => navigate(`audits/${audit.id}`) }, "Decide treatment");
  if (proposal && proposal.status === "CHALLENGE" && doc.permissions.can_review) return h("button", { class: "primary", onclick: () => navigate(`documents/${doc.id}?tab=versions&version=${proposal.id}`) }, "Review proposal");
  if (audit && ["TO_DO", "IN_PROGRESS"].includes(audit.status) && doc.permissions.can_audit) return h("button", { class: "primary", onclick: () => navigate(`audits/${audit.id}`) }, "Perform audit");
  if (doc.permissions.can_contribute && doc.current_version_id) return h("button", { onclick: () => navigate(`documents/${doc.id}?tab=versions`) }, "Prepare a change");
  return h("button", { onclick: () => navigate("documents") }, "Back to documents");
}

function tabLabel(name) {
  return { overview: "Overview", versions: "Versions", audits: "Audits", issues: "Issues", history: "History", access: "Access" }[name] || name;
}

function tabContent(doc, tab) {
  if (tab === "versions") return section("Versions", listOrEmpty(doc.versions, versionCard, "No versions."));
  if (tab === "audits") return section("Audits", listOrEmpty(doc.audits, auditCard, "No audits."));
  if (tab === "issues") return h("div", { class: "grid" }, section("Issues", listOrEmpty(doc.issues, issueCard, "No issues.")), issueForm(doc));
  if (tab === "history") return section("History", listOrEmpty(doc.activity, activityCard, "No accessible history."));
  if (tab === "access") return actionsPanel(doc);
  return h("div", { class: "split" },
    h("div", { class: "grid" },
      section("Metadata", metadata(doc)),
      section("Linked active objects", activeObjects(doc))
    ),
    h("aside", { class: "grid" }, actionsPanel(doc), section("Tasks", listOrEmpty(doc.tasks, taskCard, "No tasks.")))
  );
}

function metadata(doc) {
  const items = [
    ["Identifier", doc.id],
    ["Owner", doc.owner?.name],
    ["Team", doc.team?.name],
    ["Last validation", formatDate(doc.last_validation_at)],
    ["Next due date", formatDate(doc.next_audit_due_at)],
    ["Source", doc.source_location_type || "-"],
    ["Format", doc.document_format || "-"],
    ["Category", doc.category || "-"],
  ];
  return h("dl", { class: "meta" }, items.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v || "-"))));
}

function activeObjects(doc) {
  return h("div", { class: "list" },
    doc.active_objects?.audit ? auditCard(doc.active_objects.audit) : h("div", { class: "empty" }, "No active audit."),
    doc.active_objects?.proposal ? versionCard(doc.active_objects.proposal) : h("div", { class: "empty" }, "No active proposal."),
    ...((doc.active_objects?.open_issues || []).slice(0, 3).map(issueCard)),
    ...((doc.active_objects?.blocking_tasks || []).slice(0, 3).map(taskCard))
  );
}

function versionCard(version) {
  const number = version.status === "UP" ? `v${version.major}.${version.minor}` : `target v${version.target_major}.${version.target_minor}`;
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, `${version.title || "Version"} · ${number}`), statusBadge(labels[version.status] || version.status, version.status === "UP" ? "green" : version.status === "CHALLENGE" ? "blue" : "grey")),
    h("div", { class: "muted small" }, `${labels[version.change_type] || version.change_type} · ${version.source_location_type || ""} · ${version.document_format || ""}`),
    h("p", {}, version.change_summary || "No summary")
  );
}

function auditCard(audit) {
  return h("button", { class: "card doc-link", onclick: () => navigate(`audits/${audit.id}`) },
    h("div", { class: "section-title" }, h("strong", {}, `Audit #${audit.id}`), statusBadge(labels[audit.status] || audit.status, audit.status === "CLOSED" ? "green" : "amber")),
    h("span", { class: "muted small" }, `Due ${formatDate(audit.due_at)} · ${audit.assignee_name || "Unassigned"}`)
  );
}

function issueCard(issue) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, issue.title), statusBadge(labels[issue.status] || issue.status, issue.status === "RESOLVED" ? "green" : "amber")),
    h("p", {}, issue.description),
    h("div", { class: "muted small" }, `${severityLabels[issue.severity] || issue.severity} · ${issue.author_name || ""} · ${formatDate(issue.created_at)}`)
  );
}

function taskCard(task) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, task.title), statusBadge(task.blocking ? "Blocking" : "Non-blocking", task.blocking ? "red" : "blue")),
    h("div", { class: "muted small" }, `${labels[task.status] || task.status} · ${task.assignee_name || task.document_title || ""}`),
    h("p", {}, task.instructions || ""),
    !["DONE", "CANCELLED"].includes(task.status) ? h("div", { class: "row-actions" },
      h("button", { onclick: () => runAction(async () => api(`/api/tasks/${task.id}/status`, { method: "POST", body: { status: "IN_PROGRESS" } })) }, "Start"),
      h("button", { onclick: () => runAction(async () => api(`/api/tasks/${task.id}/status`, { method: "POST", body: { status: "DONE" } })) }, "Complete")
    ) : null
  );
}

function actionsPanel(doc) {
  return h("section", { class: "panel" },
    h("div", { class: "section-title" }, h("h3", {}, "Available actions")),
    h("div", { class: "grid" },
      doc.permissions.can_contribute && doc.current_version_id && !doc.active_proposal ? proposalForm(doc) : null,
      doc.permissions.can_manage ? h("button", { onclick: () => runAction(async () => api(`/api/documents/${doc.id}/audits`, { method: "POST", body: {} })) }, "Open manual audit") : null,
      doc.permissions.can_manage && doc.current_version_id ? h("button", { onclick: () => runAction(async () => api(`/api/documents/${doc.id}/review-only`, { method: "POST", body: { reason: "Review without changes" } })) }, "Validate review only") : null,
      doc.permissions.can_manage ? archiveForm(doc) : null
    )
  );
}

function proposalForm(doc) {
  return h("form", { class: "form-grid", onsubmit: event => runForm(event, async data => api(`/api/documents/${doc.id}/proposals`, { method: "POST", body: data })) },
    selectField("Type", "change_type", [["EDIT", "Edit 1.x"], ["NEW_VERSION", "Major x.0"]]),
    field("Summary", "change_summary"),
    h("div", { class: "full row-actions" }, h("button", {}, "Create proposal"))
  );
}

function archiveForm(doc) {
  return h("form", { class: "grid", onsubmit: event => runForm(event, async data => api(`/api/documents/${doc.id}/archive`, { method: "POST", body: data })) },
    field("Archive reason", "reason", "text", true),
    h("button", { class: "danger" }, "Archive")
  );
}

function issueForm(doc) {
  if (!doc.permissions.can_comment || !doc.current_version_id) return h("div", { class: "empty" }, "Issue reporting unavailable.");
  return section("New issue", h("form", { class: "grid", onsubmit: event => runForm(event, async data => api(`/api/documents/${doc.id}/issues`, { method: "POST", body: data })) },
    field("Title", "title", "text", true),
    textField("Description", "description", true),
    selectField("Severity", "severity", [["BASSE", "Low"], ["MOYENNE", "Medium"], ["HAUTE", "High"]]),
    h("button", {}, "Report")
  ));
}

function viewAuditDetail(audit) {
  return h("div", { class: "grid" },
    h("section", { class: "panel detail-hero" },
      h("div", { class: "breadcrumb" }, `Documents -> ${audit.document.title} -> Audit #${audit.id}`),
      h("h2", {}, audit.document.title),
      h("p", {}, audit.next_action),
      h("div", { class: "badges" }, statusBadge(labels[audit.status] || audit.status, audit.status === "CLOSED" ? "green" : "amber"), statusBadge(audit.document.publication_label, "green"), statusBadge(audit.document.audit_label, audit.document.audit_state === "overdue" ? "red" : "blue"))
    ),
    h("div", { class: "progress" }, audit.progress.map(step => h("span", {}, step))),
    h("div", { class: "split" },
      h("div", { class: "grid" },
        section("Checklist", listOrEmpty(audit.checklist, item => h("div", { class: "card" }, h("strong", {}, item.label), h("span", { class: "muted small" }, item.is_checked ? "Checked" : "To check")), "No checklist.")),
        section("Linked issues", listOrEmpty(audit.issues, issueCard, "No issues.")),
        section("Tasks and intervention", listOrEmpty(audit.tasks, taskCard, "No tasks."))
      ),
      h("aside", { class: "grid" },
        section("Owner decision", h("p", {}, audit.owner_decision_reason || "No decision recorded.")),
        audit.status === "TO_DO" || audit.status === "IN_PROGRESS" ? auditSubmitForm(audit) : null,
        audit.status === "AWAITING_OWNER_DECISION" ? auditDecisionForm(audit) : null
      )
    )
  );
}

function auditSubmitForm(audit) {
  return section("Submit audit", h("form", { class: "grid", onsubmit: event => runForm(event, async data => {
    data.issues = data.issue_title ? [{ title: data.issue_title, description: data.issue_description || data.issue_title, severity: "MOYENNE" }] : [];
    await api(`/api/audits/${audit.id}/submit`, { method: "POST", body: data });
  }) },
    textField("Report", "conclusion", true),
    field("Linked issue", "issue_title"),
    textField("Issue description", "issue_description"),
    h("div", { class: "row-actions" }, h("button", { type: "button", onclick: () => runAction(async () => api(`/api/audits/${audit.id}/claim`, { method: "POST", body: {} })) }, "Claim"), h("button", { class: "primary" }, "Submit audit"))
  ));
}

function auditDecisionForm(audit) {
  const users = state.reference?.users || [];
  return section("Owner decision", h("form", { class: "form-grid", onsubmit: event => runForm(event, async data => {
    data.tasks = [{ title: data.task_title || "Perform the intervention", instructions: data.justification, assignee_id: data.primary_responsible_id, blocking: true }];
    await api(`/api/audits/${audit.id}/decide`, { method: "POST", body: data });
  }) },
    selectField("Decision", "decision_type", [["REVIEW_ONLY", "Review only"], ["EDIT", "Minor edit"], ["NEW_VERSION", "Major version"], ["ARCHIVE", "Archive"]]),
    selectField("Responsible", "primary_responsible_id", users.map(u => [u.id, u.name]), false, true),
    field("Task", "task_title"),
    textField("Justification", "justification", true),
    h("div", { class: "full row-actions" }, h("button", { class: "primary" }, "Save decision"))
  ));
}

function viewAudits(data) {
  return h("div", { class: "grid" }, h("div", { class: "notice" }, `${data.total} accessible audit(s)`), listOrEmpty(data.items, auditCard, "No accessible active audits."));
}

function viewTasks(data) {
  return h("div", { class: "grid" }, h("div", { class: "notice" }, `${data.total} open task(s)`), listOrEmpty(data.items, taskCard, "No open tasks."));
}

async function loadNotifications() {
  const params = new URLSearchParams(state.route.query.toString());
  return api(`/api/notifications?${params.toString()}`);
}

function viewNotifications(data) {
  const q = state.route.query;
  return h("div", { class: "grid" },
    h("section", { class: "panel" },
      h("div", { class: "section-title" }, h("h2", {}, `${data.total} notification(s)`), h("button", { onclick: () => runAction(async () => api("/api/notifications/mark-all-read", { method: "POST", body: {} })) }, "Mark all read")),
      h("div", { class: "row-actions" },
        h("button", { class: !q.get("unread") ? "primary" : "", onclick: () => navigate("notifications") }, "All"),
        h("button", { class: q.get("unread") ? "primary" : "", onclick: () => navigate("notifications?unread=1") }, "Unread")
      )
    ),
    listOrEmpty(data.items, notificationCard, "No notifications in this filter.")
  );
}

function notificationCard(item) {
  return h("div", { class: "card notification-card" },
    h("div", { class: "section-title" }, h("strong", {}, item.title), statusBadge(item.read ? "Read" : "Unread", item.read ? "grey" : "blue")),
    h("p", {}, item.body),
    h("div", { class: "muted small" }, `${item.type_label} · ${item.version_label || ""} · ${formatDate(item.created_at)}`),
    h("div", { class: "row-actions" },
      item.action?.href ? h("button", { class: "primary", onclick: () => navigate(item.action.href.replace(/^#\//, "")) }, item.action.label) : null,
      item.read ? h("button", { onclick: () => runAction(async () => api(`/api/notifications/${item.id}/unread`, { method: "POST", body: {} })) }, "Mark unread") : h("button", { onclick: () => runAction(async () => api(`/api/notifications/${item.id}/read`, { method: "POST", body: {} })) }, "Mark read")
    )
  );
}

function viewCreateDocument() {
  const teams = state.reference?.teams || [];
  const users = state.reference?.users || [];
  return h("form", { class: "grid", onsubmit: event => runForm(event, async data => {
    data.tags = (data.tags || "").split(",").map(item => item.trim()).filter(Boolean);
    data.audit_checklist = (data.audit_checklist || "").split("\n").map(item => item.trim()).filter(Boolean);
    const detail = await api("/api/documents", { method: "POST", body: data });
    navigate(`documents/${encodeURIComponent(detail.id)}`);
  }) },
    formSection("Identification", [
      field("Required title", "title", "text", true),
      textField("Description", "description"),
      field("Category", "category"),
      tagsField()
    ]),
    formSection("Responsibility and access", [
      selectField("Responsible team", "team_id", teams.map(t => [t.id, t.name]), true, true),
      selectField("Owner", "owner_id", users.map(u => [u.id, u.name]), false, true),
      selectField("Confidentiality", "confidentiality", [["EQUIPE", "Team · members and explicit access"], ["INTERNE", "Internal · authenticated organization"], ["RESTREINT", "Restricted · explicit access only"]])
    ]),
    formSection("Document source", [
      field("Fictional source location", "source_location_label", "text", true),
      field("Placeholder reference", "placeholder_ref", "text", true),
      field("Format", "document_format", "text", true, false, "PDF")
    ]),
    formSection("Audit policy", [
      field("Frequency", "audit_frequency_value", "number", true, true, "12"),
      selectField("Unit", "audit_frequency_unit", [["months", "Months"], ["days", "Days"]]),
      textField("Audit instructions", "audit_instructions"),
      textField("Audit checklist", "audit_checklist")
    ]),
    h("div", { class: "row-actions" }, h("button", { class: "primary" }, "Create draft"), h("button", { type: "button", onclick: () => navigate("documents") }, "Cancel"))
  );
}

function formSection(title, controls) {
  return h("section", { class: "panel form-section" }, h("h2", {}, title), h("div", { class: "form-grid" }, controls));
}

function tagsField() {
  return h("label", {}, "Tags", h("input", { name: "tags", placeholder: "payroll, audit, procedure", oninput: event => {
    const preview = event.currentTarget.parentElement.querySelector(".tag-preview");
    const tags = event.currentTarget.value.split(",").map(tag => tag.trim()).filter(Boolean);
    preview.replaceChildren(...tags.map(tag => h("span", { class: "badge blue" }, tag)));
  }}), h("span", { class: "tag-preview badges" }));
}

function viewAdmin() {
  const ref = state.reference;
  return h("div", { class: "grid" },
    section("BigD Administration", h("p", { class: "muted" }, "Users, teams, and application permissions.")),
    section("Users", h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Email"), h("th", {}, "Active"))),
      h("tbody", {}, (ref?.users || []).map(user => h("tr", {}, h("td", {}, user.name), h("td", {}, user.email), h("td", {}, user.active ? "Yes" : "No"))))
    )),
    section("Teams", h("div", { class: "badges" }, (ref?.teams || []).map(team => h("span", { class: "badge blue" }, team.name)))),
    ref?.is_admin ? section("Create user", h("form", { class: "form-grid", onsubmit: event => runForm(event, async data => { await api("/api/admin/users", { method: "POST", body: data }); await loadReference(); }) },
      field("Name", "name", "text", true),
      field("Email", "email", "email", true),
      field("Initial password", "password", "text", true, false, "ChangeMe123!"),
      h("div", { class: "full row-actions" }, h("button", { class: "primary" }, "Create"))
    )) : h("div", { class: "notice" }, "Administration is read-only for this account.")
  );
}

function runForm(event, handler) {
  event.preventDefault();
  const data = formData(event.currentTarget);
  runAction(() => handler(data));
}

function field(label, name, type = "text", required = false, number = false, value = "") {
  return h("label", {}, label, h("input", { name, type, required, value, "data-number": number ? "true" : undefined }));
}

function textField(label, name, required = false) {
  return h("label", { class: "full" }, label, h("textarea", { name, required }));
}

function selectField(label, name, choices, required = false, number = false) {
  return selectFieldWithValue(label, name, choices, "", required, number);
}

function selectFieldWithValue(label, name, choices, selected = "", required = false, number = false) {
  return h("label", {}, label, h("select", { name, required, "data-number": number ? "true" : undefined }, choices.map(([value, text]) => h("option", { value, selected: String(selected || "") === String(value) }, text))));
}

function section(title, content) {
  return h("section", { class: "panel" }, h("div", { class: "section-title" }, h("h2", {}, title)), content);
}

function listOrEmpty(items, mapper, emptyText = "No items.") {
  if (!items || !items.length) return h("div", { class: "empty" }, emptyText);
  return h("div", { class: "list" }, items.map(mapper));
}

function statusBadge(text, tone = "grey") {
  return h("span", { class: `badge ${tone}` }, text || "-");
}

function formatDate(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Brussels" }).format(new Date(value));
  } catch {
    return value;
  }
}

boot();
