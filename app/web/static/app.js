const state = {
  user: null,
  csrf: null,
  view: "dashboard",
  documents: null,
  detail: null,
  reference: null,
  loading: false,
  error: "",
  filters: { search: "", confidentiality: "", include_archived: false },
};

const labels = {
  DRAFT: "Brouillon",
  CHALLENGE: "En revue",
  UP: "Publié",
  CANCELLED: "Annulé",
  TO_DO: "À faire",
  IN_PROGRESS: "En cours",
  AWAITING_OWNER_DECISION: "Décision owner",
  REMEDIATION_IN_PROGRESS: "Correction",
  AWAITING_FINAL_VALIDATION: "Validation finale",
  CLOSED: "Clôturé",
  OPEN: "Ouvert",
  UNDER_REVIEW: "À analyser",
  ACCEPTED: "Accepté",
  RESOLVED: "Résolu",
  DISMISSED: "Écarté",
  EDIT: "Edit mineur",
  NEW_VERSION: "Version majeure",
  INITIAL: "Initiale",
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
    const message = payload.error?.message || `Erreur ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function boot() {
  try {
    const session = await api("/api/session");
    state.user = session.user;
    state.csrf = session.csrf_token;
    await loadReference();
    routeFromHash();
  } catch {
    renderLogin();
  }
}

async function loadReference() {
  state.reference = await api("/api/reference");
}

function setError(message) {
  state.error = message || "";
  render();
}

async function runAction(fn) {
  state.loading = true;
  state.error = "";
  render();
  try {
    await fn();
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    render();
  }
}

function routeFromHash() {
  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  const docId = hash.get("document");
  if (docId) {
    state.view = "detail";
    loadDocument(docId);
  } else {
    render();
  }
}

window.addEventListener("hashchange", routeFromHash);

async function loadDocuments() {
  const params = new URLSearchParams();
  if (state.filters.search) params.set("search", state.filters.search);
  if (state.filters.confidentiality) params.set("confidentiality", state.filters.confidentiality);
  if (state.filters.include_archived) params.set("include_archived", "1");
  state.documents = await api(`/api/documents?${params.toString()}`);
}

async function loadDocument(id) {
  state.loading = true;
  state.error = "";
  render();
  try {
    state.detail = await api(`/api/documents/${encodeURIComponent(id)}`);
  } catch (error) {
    state.error = error.message;
    state.detail = null;
  } finally {
    state.loading = false;
    render();
  }
}

function renderLogin() {
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
            await loadReference();
            state.view = "dashboard";
            await loadDocuments();
            render();
          } catch (error) {
            event.currentTarget.querySelector("[data-error]").textContent = error.message;
          }
        }
      },
        h("div", { class: "brand" }, h("span", { class: "brand-mark" }, "DG"), h("span", {}, "Gouvernance documentaire")),
        h("h1", {}, "Connexion"),
        h("p", { class: "muted" }, "Application prototype SD Worx avec données fictives."),
        h("label", {}, "Email", h("input", { name: "email", type: "email", required: true, autocomplete: "username" })),
        h("label", {}, "Mot de passe", h("input", { name: "password", type: "password", required: true, autocomplete: "current-password" })),
        h("div", { class: "notice error", "data-error": "" }),
        h("button", { class: "primary", type: "submit" }, "Se connecter"),
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
    }}, h("option", { value: "" }, "Comptes de démonstration"));
    data.users.forEach(user => select.append(h("option", { value: user.email }, `${user.name} · ${user.email}`)));
    box.replaceChildren(h("label", {}, "Mode développement", select));
  } catch {
    // Le sélecteur est volontairement absent hors développement.
  }
}

function shell(content, title) {
  const nav = [
    ["dashboard", "Tableau de bord"],
    ["catalog", "Catalogue"],
    ["new", "Créer"],
    ["notifications", "Notifications"],
    ["admin", "Administration"],
  ];
  return h("div", { class: "shell" },
    h("aside", { class: "sidebar" },
      h("div", { class: "brand" }, h("span", { class: "brand-mark" }, "DG"), h("span", {}, "Gouvernance documentaire")),
      h("nav", { class: "nav" }, nav.map(([view, label]) => h("button", {
        class: state.view === view ? "active" : "",
        onclick: async () => {
          location.hash = "";
          state.view = view;
          state.detail = null;
          if (view === "catalog") await runAction(loadDocuments);
          else render();
        }
      }, label))),
      h("div", { class: "userbox" },
        h("strong", {}, state.user?.name || ""),
        h("span", {}, state.user?.email || ""),
        h("button", { class: "ghost", onclick: () => runAction(async () => { await api("/api/logout", { method: "POST" }); state.user = null; renderLogin(); }) }, "Déconnexion")
      )
    ),
    h("main", { class: "main" },
      h("div", { class: "topbar" }, h("h1", {}, title), state.loading ? h("span", { class: "badge blue" }, "Chargement") : null),
      state.error ? h("div", { class: "notice error" }, state.error) : null,
      content
    )
  );
}

function render() {
  if (!state.user) return renderLogin();
  const root = document.querySelector("#app");
  let content;
  let title = "Tableau de bord";
  if (state.view === "catalog") {
    title = "Catalogue";
    content = viewCatalog();
  } else if (state.view === "new") {
    title = "Créer un document";
    content = viewCreateDocument();
  } else if (state.view === "detail") {
    title = state.detail?.title || "Fiche document";
    content = viewDetail();
  } else if (state.view === "notifications") {
    title = "Notifications";
    content = viewNotifications();
  } else if (state.view === "admin") {
    title = "Administration";
    content = viewAdmin();
  } else {
    content = viewDashboard();
  }
  root.replaceChildren(shell(content, title));
}

function viewDashboard() {
  const box = h("div", { class: "grid" }, h("div", { class: "empty" }, "Chargement du tableau de bord"));
  runOnce("dashboard", async () => {
    const data = await api("/api/dashboard");
    box.replaceChildren(
      h("div", { class: "grid cols-3" },
        metric("Documents accessibles", data.document_count),
        metric("Audits en retard", data.overdue_count, "red"),
        metric("Tâches actives", data.active_tasks.length, "amber")
      ),
      section("Tâches assignées", listOrEmpty(data.active_tasks, taskCard)),
      section("Notifications récentes", listOrEmpty(data.notifications, notificationCard))
    );
  });
  return box;
}

function metric(label, value, color = "blue") {
  return h("div", { class: "panel" }, h("div", { class: `badge ${color}` }, label), h("div", { class: "metric-value" }, value));
}

const ran = new Set();
function runOnce(key, fn) {
  const runKey = `${key}:${Date.now()}`;
  if (ran.has(runKey)) return;
  ran.add(runKey);
  fn().catch(error => setError(error.message));
}

function viewCatalog() {
  const list = h("div", { class: "list" }, h("div", { class: "empty" }, "Aucun résultat chargé"));
  const refresh = async () => {
    await loadDocuments();
    list.replaceChildren(...(state.documents.items.length ? state.documents.items.map(docRow) : [h("div", { class: "empty" }, "Aucun document accessible")]));
  };
  setTimeout(() => refresh().catch(error => setError(error.message)), 0);
  return h("div", { class: "grid" },
    h("section", { class: "panel" },
      h("form", { class: "toolbar", onsubmit: (event) => { event.preventDefault(); Object.assign(state.filters, formData(event.currentTarget)); runAction(refresh); } },
        h("label", {}, "Recherche", h("input", { name: "search", value: state.filters.search || "", placeholder: "Titre, catégorie, tag, équipe" })),
        h("label", {}, "Confidentialité", h("select", { name: "confidentiality" },
          option("", "Toutes", state.filters.confidentiality),
          option("INTERNE", "Interne", state.filters.confidentiality),
          option("EQUIPE", "Équipe", state.filters.confidentiality),
          option("RESTREINT", "Restreint", state.filters.confidentiality)
        )),
        h("label", {}, "Archives", h("span", { class: "row-actions" }, h("input", { type: "checkbox", name: "include_archived", checked: state.filters.include_archived }), "Inclure")),
        h("button", { class: "primary" }, "Filtrer")
      )
    ),
    list
  );
}

function option(value, label, selected) {
  return h("option", { value, selected: selected === value }, label);
}

function docRow(doc) {
  const version = doc.current_version_id ? `${doc.current_major}.${doc.current_minor}` : "Non publié";
  return h("button", { class: "card doc-row", onclick: () => { location.hash = `document=${encodeURIComponent(doc.id)}`; } },
    h("div", {},
      h("strong", {}, doc.title),
      h("div", { class: "muted small" }, `${doc.id} · ${doc.team_name} · Owner ${doc.owner_name}`),
      h("div", { class: "badges" },
        h("span", { class: "badge blue" }, doc.confidentiality),
        h("span", { class: "badge green" }, `Version ${version}`),
        doc.has_overdue_flag ? h("span", { class: "badge red" }, "Audit en retard") : null,
        doc.has_active_proposal ? h("span", { class: "badge amber" }, "Proposition active") : null,
        doc.archived_at ? h("span", { class: "badge" }, "Archivé") : null
      )
    ),
    h("span", { class: "muted" }, "Ouvrir")
  );
}

function viewCreateDocument() {
  const teams = state.reference?.teams || [];
  const users = state.reference?.users || [];
  return h("section", { class: "panel" },
    h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => {
      data.tags = (data.tags || "").split(",").map(item => item.trim()).filter(Boolean);
      data.audit_checklist = (data.audit_checklist || "").split("\n").map(item => item.trim()).filter(Boolean);
      const detail = await api("/api/documents", { method: "POST", body: data });
      location.hash = `document=${encodeURIComponent(detail.id)}`;
    }) },
      field("Titre", "title", "text", true),
      selectField("Équipe", "team_id", teams.map(t => [t.id, t.name]), true, true),
      selectField("Owner", "owner_id", users.map(u => [u.id, u.name]), false, true),
      selectField("Confidentialité", "confidentiality", [["EQUIPE", "Équipe"], ["INTERNE", "Interne"], ["RESTREINT", "Restreint"]]),
      field("Catégorie", "category"),
      field("Tags séparés par virgule", "tags"),
      field("Fréquence", "audit_frequency_value", "number", true, true, "12"),
      selectField("Unité", "audit_frequency_unit", [["months", "Mois"], ["days", "Jours"]]),
      field("Source fictive", "source_location_label", "text", true),
      field("Référence placeholder", "placeholder_ref", "text", true),
      field("Format", "document_format", "text", true, false, "PDF"),
      textField("Description", "description"),
      textField("Consignes d'audit", "audit_instructions"),
      textField("Checklist d'audit", "audit_checklist"),
      h("div", { class: "full row-actions" }, h("button", { class: "primary" }, "Créer le brouillon"))
    )
  );
}

function field(label, name, type = "text", required = false, number = false, value = "") {
  return h("label", {}, label, h("input", { name, type, required, value, "data-number": number ? "true" : undefined }));
}

function textField(label, name, required = false) {
  return h("label", { class: "full" }, label, h("textarea", { name, required }));
}

function selectField(label, name, choices, required = false, number = false) {
  return h("label", {}, label, h("select", { name, required, "data-number": number ? "true" : undefined }, choices.map(([value, text]) => h("option", { value }, text))));
}

function runForm(event, handler) {
  event.preventDefault();
  const data = formData(event.currentTarget);
  runAction(() => handler(data));
}

function viewDetail() {
  if (!state.detail) return h("div", { class: "empty" }, state.loading ? "Chargement" : "Aucun document chargé");
  const doc = state.detail;
  return h("div", { class: "split" },
    h("div", { class: "grid" },
      documentHeader(doc),
      section("Métadonnées", metadata(doc)),
      section("Versions", listOrEmpty(doc.versions, versionCard)),
      section("Audits", listOrEmpty(doc.audits, auditCard)),
      section("Signalements", listOrEmpty(doc.issues, issueCard)),
      section("Historique", listOrEmpty(doc.activity, logCard))
    ),
    h("aside", { class: "grid" },
      actionsPanel(doc),
      section("Tâches", listOrEmpty(doc.tasks, taskCard)),
      issueForm(doc)
    )
  );
}

function documentHeader(doc) {
  return h("section", { class: "panel" },
    h("div", { class: "section-title" }, h("h2", {}, doc.title), h("button", { onclick: () => { state.view = "catalog"; location.hash = ""; render(); } }, "Retour")),
    h("p", { class: "muted" }, doc.description || "Sans description"),
    h("div", { class: "badges" },
      h("span", { class: "badge blue" }, `Confidentialité ${doc.confidentiality}`),
      h("span", { class: "badge green" }, doc.current_version_id ? `Version courante ${currentVersion(doc)}` : "Non publié"),
      doc.flags?.some(f => f.active) ? h("span", { class: "badge red" }, "Audit en retard") : null,
      doc.archived_at ? h("span", { class: "badge" }, "Archivé") : null
    )
  );
}

function currentVersion(doc) {
  const version = doc.versions.find(v => v.id === doc.current_version_id);
  return version ? `${version.major}.${version.minor}` : "inconnue";
}

function metadata(doc) {
  const items = [
    ["Identifiant", doc.id],
    ["Équipe", doc.team?.name],
    ["Owner", doc.owner?.name],
    ["Dernière validation", formatDate(doc.last_validation_at)],
    ["Prochaine échéance", formatDate(doc.next_audit_due_at)],
    ["Fréquence", `${doc.audit_frequency_value} ${doc.audit_frequency_unit === "months" ? "mois" : "jours"}`],
    ["Rôle auditeur", `${doc.auditor_role} · ${doc.auditor_team?.name || ""}`],
    ["Catégorie", doc.category || "-"],
  ];
  return h("dl", { class: "meta" }, items.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v || "-"))));
}

function versionCard(version) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" },
      h("strong", {}, `${version.title} · ${version.status === "UP" ? `${version.major}.${version.minor}` : `cible ${version.target_major}.${version.target_minor}`}`),
      h("span", { class: `badge ${version.status === "UP" ? "green" : version.status === "CHALLENGE" ? "amber" : "blue"}` }, labels[version.status] || version.status)
    ),
    h("div", { class: "muted small" }, `${labels[version.change_type] || version.change_type} · ${version.source_location_type} · ${version.document_format}`),
    h("p", {}, version.change_summary || "Aucun résumé"),
    version.status === "DRAFT" && state.detail.permissions.can_contribute ? draftForm(version) : null,
    version.status === "CHALLENGE" ? challengeActions(version) : null
  );
}

function draftForm(version) {
  return h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => {
    data.row_version = version.row_version;
    await api(`/api/versions/${version.id}/update`, { method: "POST", body: data });
    await loadDocument(state.detail.id);
  }) },
    h("input", { type: "hidden", name: "row_version", value: version.row_version }),
    fieldWithValue("Titre", "title", version.title),
    fieldWithValue("Source fictive", "source_location_label", version.source_location_label),
    fieldWithValue("Référence placeholder", "placeholder_ref", version.placeholder_ref),
    fieldWithValue("Format", "document_format", version.document_format),
    textareaWithValue("Résumé des changements", "change_summary", version.change_summary),
    h("div", { class: "full row-actions" },
      h("button", { class: "primary" }, "Enregistrer"),
      h("button", { type: "button", onclick: () => runAction(async () => { await api(`/api/versions/${version.id}/submit`, { method: "POST", body: {} }); await loadDocument(state.detail.id); }) }, "Soumettre")
    )
  );
}

function fieldWithValue(label, name, value) {
  return h("label", {}, label, h("input", { name, value: value || "" }));
}

function textareaWithValue(label, name, value) {
  return h("label", { class: "full" }, label, h("textarea", { name }, value || ""));
}

function challengeActions(version) {
  const canReview = state.detail.permissions.can_review;
  const canManage = state.detail.permissions.can_manage;
  return h("div", { class: "grid" },
    canReview ? h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => { await api(`/api/versions/${version.id}/request-correction`, { method: "POST", body: data }); await loadDocument(state.detail.id); }) },
      textField("Commentaire de correction", "comment", true),
      h("div", { class: "full row-actions" }, h("button", {}, "Demander correction"))
    ) : null,
    canReview ? h("div", { class: "row-actions" },
      h("button", { onclick: () => runAction(async () => { await api(`/api/versions/${version.id}/approve-review`, { method: "POST", body: { comment: "Avis favorable" } }); await loadDocument(state.detail.id); }) }, "Avis favorable")
    ) : null,
    canReview ? interventionForm(version) : null,
    canManage ? h("button", { class: "primary", onclick: () => runAction(async () => { await api(`/api/versions/${version.id}/publish`, { method: "POST", body: {} }); await loadDocument(state.detail.id); }) }, "Publier") : null
  );
}

function interventionForm(version) {
  const users = state.reference?.users || [];
  return h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => { await api(`/api/versions/${version.id}/request-intervention`, { method: "POST", body: data }); await loadDocument(state.detail.id); }) },
    field("Titre tâche", "title", "text", true),
    selectField("Assignée à", "assignee_id", users.map(u => [u.id, u.name]), true, true),
    h("label", {}, "Bloquante", h("span", { class: "row-actions" }, h("input", { type: "checkbox", name: "blocking", checked: true }), "Oui")),
    textField("Consignes", "instructions"),
    h("div", { class: "full row-actions" }, h("button", {}, "Demander intervention"))
  );
}

function auditCard(audit) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, `Audit #${audit.id}`), h("span", { class: "badge amber" }, labels[audit.status] || audit.status)),
    h("div", { class: "muted small" }, `Échéance ${formatDate(audit.due_at)}`),
    audit.conclusion ? h("p", {}, audit.conclusion) : null,
    audit.status === "TO_DO" || audit.status === "IN_PROGRESS" ? auditSubmitForm(audit) : null,
    audit.status === "AWAITING_OWNER_DECISION" && state.detail.permissions.can_manage ? auditDecisionForm(audit) : null
  );
}

function auditSubmitForm(audit) {
  return h("form", { class: "grid", onsubmit: (event) => runForm(event, async data => {
    data.issues = data.issue_title ? [{ title: data.issue_title, description: data.issue_description || data.issue_title, severity: "MOYENNE" }] : [];
    await api(`/api/audits/${audit.id}/submit`, { method: "POST", body: data });
    await loadDocument(state.detail.id);
  }) },
    textField("Compte rendu", "conclusion", true),
    field("Signalement associé", "issue_title"),
    textField("Description du signalement", "issue_description"),
    h("div", { class: "row-actions" },
      h("button", { type: "button", onclick: () => runAction(async () => { await api(`/api/audits/${audit.id}/claim`, { method: "POST", body: {} }); await loadDocument(state.detail.id); }) }, "Prendre en charge"),
      h("button", { class: "primary" }, "Soumettre audit")
    )
  );
}

function auditDecisionForm(audit) {
  const users = state.reference?.users || [];
  return h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => {
    data.tasks = [{ title: data.task_title || "Réaliser l'intervention", instructions: data.justification, assignee_id: data.primary_responsible_id, blocking: true }];
    await api(`/api/audits/${audit.id}/decide`, { method: "POST", body: data });
    await loadDocument(state.detail.id);
  }) },
    selectField("Décision", "decision_type", [["REVIEW_ONLY", "Review seule"], ["EDIT", "Edit mineur"], ["NEW_VERSION", "Version majeure"], ["ARCHIVE", "Archive"]]),
    selectField("Responsable", "primary_responsible_id", users.map(u => [u.id, u.name]), false, true),
    field("Tâche", "task_title"),
    textField("Justification", "justification", true),
    h("div", { class: "full row-actions" }, h("button", { class: "primary" }, "Enregistrer la décision"))
  );
}

function issueCard(issue) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, issue.title), h("span", { class: "badge" }, labels[issue.status] || issue.status)),
    h("p", {}, issue.description),
    h("div", { class: "muted small" }, `${issue.severity} · ${formatDate(issue.created_at)}`),
    state.detail.permissions.can_manage && !["RESOLVED", "DISMISSED"].includes(issue.status) ? h("div", { class: "row-actions" },
      h("button", { onclick: () => runAction(async () => { await api(`/api/issues/${issue.id}/resolve`, { method: "POST", body: { status: "RESOLVED", resolution: "Résolu manuellement" } }); await loadDocument(state.detail.id); }) }, "Résoudre"),
      h("button", { onclick: () => runAction(async () => { await api(`/api/issues/${issue.id}/resolve`, { method: "POST", body: { status: "DISMISSED", resolution_reason: "Risque accepté par le owner" } }); await loadDocument(state.detail.id); }) }, "Écarter")
    ) : null
  );
}

function taskCard(task) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, task.title), h("span", { class: `badge ${task.blocking ? "red" : "blue"}` }, task.blocking ? "Bloquante" : "Non bloquante")),
    h("div", { class: "muted small" }, `${labels[task.status] || task.status} · ${task.document_title || task.document_id || ""}`),
    h("p", {}, task.instructions || ""),
    !["DONE", "CANCELLED"].includes(task.status) ? h("div", { class: "row-actions" },
      h("button", { onclick: () => runAction(async () => { await api(`/api/tasks/${task.id}/status`, { method: "POST", body: { status: "IN_PROGRESS" } }); if (state.detail) await loadDocument(state.detail.id); }) }, "Démarrer"),
      h("button", { onclick: () => runAction(async () => { await api(`/api/tasks/${task.id}/status`, { method: "POST", body: { status: "DONE" } }); if (state.detail) await loadDocument(state.detail.id); }) }, "Terminer")
    ) : null
  );
}

function actionsPanel(doc) {
  const users = state.reference?.users || [];
  return h("section", { class: "panel" },
    h("div", { class: "section-title" }, h("h3", {}, "Actions accessibles")),
    h("div", { class: "grid" },
      doc.permissions.can_contribute && doc.current_version_id ? h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => { await api(`/api/documents/${doc.id}/proposals`, { method: "POST", body: data }); await loadDocument(doc.id); }) },
        selectField("Type", "change_type", [["EDIT", "Edit 1.x"], ["NEW_VERSION", "Majeure x.0"]]),
        field("Résumé", "change_summary"),
        h("div", { class: "full row-actions" }, h("button", {}, "Créer proposition"))
      ) : null,
      doc.permissions.can_manage ? h("button", { onclick: () => runAction(async () => { await api(`/api/documents/${doc.id}/audits`, { method: "POST", body: {} }); await loadDocument(doc.id); }) }, "Ouvrir audit manuel") : null,
      doc.permissions.can_manage && doc.current_version_id ? h("button", { onclick: () => runAction(async () => { await api(`/api/documents/${doc.id}/review-only`, { method: "POST", body: { reason: "Review sans modification" } }); await loadDocument(doc.id); }) }, "Valider review seule") : null,
      doc.permissions.can_manage ? h("form", { class: "grid", onsubmit: (event) => runForm(event, async data => { await api(`/api/documents/${doc.id}/archive`, { method: "POST", body: data }); await loadDocument(doc.id); }) },
        field("Motif d'archivage", "reason", "text", true),
        h("button", { class: "danger" }, "Archiver")
      ) : null,
      doc.permissions.can_manage ? h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => { await api(`/api/documents/${doc.id}/grant-access`, { method: "POST", body: data }); await loadDocument(doc.id); }) },
        selectField("Utilisateur", "user_id", users.map(u => [u.id, u.name]), true, true),
        h("label", {}, "Droits", h("span", { class: "row-actions" },
          h("input", { type: "checkbox", name: "can_contribute" }), "Contribuer",
          h("input", { type: "checkbox", name: "can_review" }), "Review",
          h("input", { type: "checkbox", name: "can_manage" }), "Gérer"
        )),
        field("Motif", "reason", "text", true),
        h("div", { class: "full row-actions" }, h("button", {}, "Accorder accès"))
      ) : null
    )
  );
}

function issueForm(doc) {
  if (!doc.permissions.can_comment || !doc.current_version_id) return h("section", { class: "panel" }, h("div", { class: "empty" }, "Signalement indisponible"));
  return h("section", { class: "panel" },
    h("div", { class: "section-title" }, h("h3", {}, "Nouveau signalement")),
    h("form", { class: "grid", onsubmit: (event) => runForm(event, async data => { await api(`/api/documents/${doc.id}/issues`, { method: "POST", body: data }); await loadDocument(doc.id); }) },
      field("Titre", "title", "text", true),
      textField("Description", "description", true),
      selectField("Importance", "severity", [["BASSE", "Basse"], ["MOYENNE", "Moyenne"], ["HAUTE", "Haute"]]),
      h("button", {}, "Signaler")
    )
  );
}

function viewNotifications() {
  const box = h("div", { class: "list" }, h("div", { class: "empty" }, "Chargement"));
  setTimeout(async () => {
    try {
      const data = await api("/api/notifications");
      box.replaceChildren(...(data.items.length ? data.items.map(notificationCard) : [h("div", { class: "empty" }, "Aucune notification")]));
    } catch (error) {
      setError(error.message);
    }
  }, 0);
  return box;
}

function notificationCard(item) {
  return h("div", { class: "card" },
    h("div", { class: "section-title" }, h("strong", {}, item.title), h("span", { class: "badge" }, item.type)),
    h("p", {}, item.body),
    h("div", { class: "row-actions" },
      h("span", { class: "muted small" }, formatDate(item.created_at)),
      !item.read_at ? h("button", { onclick: () => runAction(async () => { await api(`/api/notifications/${item.id}/read`, { method: "POST", body: {} }); state.view = "notifications"; render(); }) }, "Marquer lue") : h("span", { class: "badge green" }, "Lue")
    )
  );
}

function viewAdmin() {
  const ref = state.reference;
  if (!ref) return h("div", { class: "empty" }, "Référentiel non chargé");
  return h("div", { class: "grid" },
    section("Utilisateurs", h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Nom"), h("th", {}, "Email"), h("th", {}, "Actif"))),
      h("tbody", {}, ref.users.map(user => h("tr", {}, h("td", {}, user.name), h("td", {}, user.email), h("td", {}, user.active ? "Oui" : "Non"))))
    )),
    section("Équipes", h("div", { class: "badges" }, ref.teams.map(team => h("span", { class: "badge blue" }, team.name)))),
    ref.is_admin ? section("Créer un utilisateur", h("form", { class: "form-grid", onsubmit: (event) => runForm(event, async data => { await api("/api/admin/users", { method: "POST", body: data }); await loadReference(); render(); }) },
      field("Nom", "name", "text", true),
      field("Email", "email", "email", true),
      field("Mot de passe initial", "password", "text", true, false, "ChangeMe123!"),
      h("div", { class: "full row-actions" }, h("button", { class: "primary" }, "Créer"))
    )) : h("div", { class: "notice" }, "Administration en lecture seule pour ce compte.")
  );
}

function section(title, content) {
  return h("section", { class: "panel" }, h("div", { class: "section-title" }, h("h2", {}, title)), content);
}

function listOrEmpty(items, mapper) {
  if (!items || !items.length) return h("div", { class: "empty" }, "Aucun élément");
  return h("div", { class: "list" }, items.map(mapper));
}

function logCard(log) {
  return h("div", { class: "card" },
    h("strong", {}, log.action),
    h("div", { class: "muted small" }, `${log.actor_name || "Système"} · ${formatDate(log.created_at)}`),
    log.reason ? h("p", {}, log.reason) : null
  );
}

function formatDate(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("fr-BE", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Brussels" }).format(new Date(value));
  } catch {
    return value;
  }
}

boot();
