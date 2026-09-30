const API = window.API_BASE || "http://localhost:8001";

const keycloak = new Keycloak({
  url: window.KEYCLOAK_URL || "http://localhost:8180",
  realm: window.KEYCLOAK_REALM || "idma",
  clientId: window.KEYCLOAK_CLIENT_ID || "idma-frontend",
});

const userName = document.getElementById("user-name");
const logoutBtn = document.getElementById("logout-btn");
const userOrg = document.getElementById("user-org");
const userGroups = document.getElementById("user-groups");
const catalogTree = document.getElementById("catalog-tree");
const resultEl = document.getElementById("result");
const grantForm = document.getElementById("grant-form");
const grantSub = document.getElementById("grant-sub");
const grantObj = document.getElementById("grant-obj");
const grantStatus = document.getElementById("grant-status");

const dagList = document.getElementById("dag-list");
const modelList = document.getElementById("model-list");
const modelInvokeForm = document.getElementById("model-invoke-form");
const modelInvokeSelect = document.getElementById("model-invoke-select");
const modelInvokeRows = document.getElementById("model-invoke-rows");
const modelInvokeResult = document.getElementById("model-invoke-result");
const airflowGrantForm = document.getElementById("airflow-grant-form");
const airflowGrantSub = document.getElementById("airflow-grant-sub");
const airflowGrantObj = document.getElementById("airflow-grant-obj");
const airflowGrantAct = document.getElementById("airflow-grant-act");
const airflowGrantStatus = document.getElementById("airflow-grant-status");

const storageBucketTag = document.getElementById("storage-bucket-tag");
const storageBucketName = document.getElementById("storage-bucket-name");
const storageFileList = document.getElementById("storage-file-list");
const storagePreview = document.getElementById("storage-preview");
const storageCrossCheckBtn = document.getElementById("storage-cross-check-btn");
const storageCrossCheckResult = document.getElementById("storage-cross-check-result");

const servicesList = document.getElementById("services-list");
const credentialsTable = document.getElementById("credentials-table");

const usersTableBody = document.querySelector("#users-table tbody");
const usersStatus = document.getElementById("users-status");
const userNewBtn = document.getElementById("user-new-btn");
const userForm = document.getElementById("user-form");
const userFormTitle = document.getElementById("user-form-title");
const userFormCancel = document.getElementById("user-form-cancel");
const userUsername = document.getElementById("user-username");
const userFirstName = document.getElementById("user-first-name");
const userLastName = document.getElementById("user-last-name");
const userEmail = document.getElementById("user-email");
const userPassword = document.getElementById("user-password");
const userTitle = document.getElementById("user-title");
const userOrgSelect = document.getElementById("user-org-input");
const userRegion = document.getElementById("user-region");
const userHr = document.getElementById("user-hr");
const userGroupsInput = document.getElementById("user-groups-input");

let currentUser = null;

// Same demo-only values as poc/docker-compose.yml / poc/k8s/01-secrets.yaml
// and poc/backend/app/seed.py's ORG_STORAGE. Trino and Airflow both log in
// via the same Keycloak realm as the frontend now — see the "Keycloak" row
// — rather than having their own separate credentials.
const DEFAULT_CREDENTIALS = [
  { service: "Postgres", user: "poc", pass: "pocpass", notes: "one instance, four databases: authz, demo, airflow, mlflow" },
  { service: "MinIO (console / admin)", user: "pocadmin", pass: "pocadminpass", notes: "full admin — MinIO Console login" },
  { service: "MinIO — org-001 (Acme Retail)", user: "org001svc", pass: "org001SecretKey123", notes: "scoped to org-001's bucket only — native MinIO IAM, no OPA involved" },
  { service: "MinIO — org-002 (Globex Logistics)", user: "org002svc", pass: "org002SecretKey456", notes: "scoped to org-002's bucket only" },
  { service: "Trino", user: null, pass: null, notes: "authenticates the caller's Keycloak JWT itself — Web UI via OAuth2 SSO, API/JDBC via a bearer token; trino.rego then decides what that caller can do" },
  { service: "Airflow", user: null, pass: null, notes: "logs in via the same Keycloak realm (FabAuthManager OAuth) — every Keycloak user is auto-provisioned on first login with the same flat role; Dag-level access is still decided by OPA, not this role" },
  { service: "OPA", user: null, pass: null, notes: "no authentication — internal service only" },
  { service: "MLflow", user: null, pass: null, notes: "no authentication — tracking server + Model Registry UI/API" },
  { service: "Keycloak", user: "alice / bob / carol / dave / erin", pass: "same as username", notes: "authentication service — realm \"idma\"; admin console login is admin/admin" },
];

// docker-compose default — every service is published on a host port.
// Kubernetes overrides window.SERVICES (see poc/k8s/07-frontend.yaml) with
// NodePorts, and marks OPA/Postgres internal-only since they're ClusterIP there.
const DEFAULT_SERVICES = [
  { name: "Frontend", port: 3000, path: "/", desc: "this UI" },
  { name: "Backend API", port: 8001, path: "/api/health", desc: "FastAPI" },
  { name: "Keycloak", port: 8180, path: "/admin/master/console/", desc: "authentication service — realm \"idma\"" },
  { name: "Trino", port: 8443, scheme: "https:", path: "/ui/", desc: "coordinator UI — click to log in via Keycloak SSO (self-signed cert, browser will warn once)" },
  { name: "Airflow", port: 8082, path: "/auth/login/keycloak?next=", desc: "webserver — click to log in via Keycloak SSO" },
  { name: "MinIO API", port: 9000, path: "/", desc: "S3 endpoint" },
  { name: "MinIO Console", port: 9001, path: "/", desc: "browser UI" },
  { name: "OPA", port: 8181, path: "/health", desc: "policy engine" },
  { name: "MLflow", port: 5000, path: "/", desc: "tracking server UI + Model Registry" },
  { name: "Postgres", port: 5433, desc: "authz · demo · airflow DBs", tcp: true },
];

async function api(path, opts = {}) {
  // Refresh the access token if it's within 30s of expiring — every call
  // through here carries a real Keycloak-issued Bearer token, the same
  // one the backend validates against Keycloak's own JWKS endpoint.
  try {
    await keycloak.updateToken(30);
  } catch (err) {
    keycloak.login();
    throw new Error("session expired — redirecting to login");
  }
  const headers = { ...(opts.headers || {}), Authorization: `Bearer ${keycloak.token}` };
  const res = await fetch(`${API}${path}`, { ...opts, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.getElementById("tab-catalog").hidden = btn.dataset.tab !== "catalog";
    document.getElementById("tab-airflow").hidden = btn.dataset.tab !== "airflow";
    document.getElementById("tab-models").hidden = btn.dataset.tab !== "models";
    document.getElementById("tab-storage").hidden = btn.dataset.tab !== "storage";
    document.getElementById("tab-users").hidden = btn.dataset.tab !== "users";
    document.getElementById("tab-architecture").hidden = btn.dataset.tab !== "architecture";
    document.getElementById("tab-services").hidden = btn.dataset.tab !== "services";
    document.getElementById("tab-credentials").hidden = btn.dataset.tab !== "credentials";
    if (btn.dataset.tab === "services") {
      renderServices();
    }
    if (btn.dataset.tab === "credentials") {
      renderCredentials();
    }
    if (btn.dataset.tab === "airflow") {
      loadAirflowDags();
      loadAirflowPolicies();
    }
    if (btn.dataset.tab === "models") {
      loadModels();
    }
    if (btn.dataset.tab === "storage") {
      loadStorageFiles();
      storageCrossCheckResult.innerHTML = "";
      storagePreview.innerHTML = `<p class="result-empty">Click a file on the left to preview it.</p>`;
    }
    if (btn.dataset.tab === "users") {
      closeUserForm();
      loadUsers();
    }
  });
});

// ---------------------------------------------------------------------
// Identity (shared across tabs) — comes from the Keycloak session, not a
// dropdown. `currentUser` is the token's own preferred_username claim;
// /api/users just supplies the label/org/groups to display for it.
// ---------------------------------------------------------------------
async function loadCurrentUser() {
  currentUser = keycloak.tokenParsed.preferred_username;
  userName.textContent = currentUser;
  const users = await api("/api/users");
  const u = users.find((x) => x.id === currentUser);
  if (u) {
    userGroups.textContent = u.groups.length ? `member of ${u.groups.join(", ")}` : "no group membership";
    userOrg.textContent = `${u.org_label} (${u.org})`;
  }
}

// ---------------------------------------------------------------------
// Data Catalog tab
// ---------------------------------------------------------------------
async function loadCatalog() {
  const tree = await api("/api/catalog");
  catalogTree.innerHTML = tree
    .map((schema) => {
      const rows = schema.tables
        .map(
          (t) => `
        <div class="table-row" data-resource="${t.resource}">
          <span class="name">${t.name}</span>
          <span class="status-dot ${t.allowed ? "allow" : "deny"}" title="${t.allowed ? "allowed" : "denied"}"></span>
        </div>`
        )
        .join("");
      return `
        <div class="schema-block">
          <div class="schema-head">
            <span>${schema.name} <span class="org-chip" title="belongs to ${schema.org_label}">${schema.org}</span></span>
            <span class="status-dot ${schema.allowed ? "allow" : "deny"}" title="${schema.allowed ? "schema-level allow" : "schema-level deny"}"></span>
          </div>
          ${rows}
        </div>`;
    })
    .join("");

  catalogTree.querySelectorAll(".table-row").forEach((row) => {
    row.addEventListener("click", () => runQuery(row.dataset.resource));
  });
}

async function runQuery(resource) {
  resultEl.innerHTML = `<p class="result-empty">Running…</p>`;
  try {
    const res = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resource }),
    });
    renderResult(res);
  } catch (err) {
    resultEl.innerHTML = `<div class="result-decision deny">✕ ${err.message}</div>`;
  }
}

function renderResult(res) {
  const req = res.request;
  const decisionLine = `enforce(sub="${req.sub}", obj="${req.obj}", act="${req.act}") → ${res.allowed ? "ALLOW" : "DENY"}`;

  if (!res.allowed) {
    resultEl.innerHTML = `
      <div class="result-decision deny">
        ${decisionLine}
        <div class="result-message">${res.message}</div>
      </div>`;
    return;
  }

  const cols = res.columns.map((c) => `<th>${c}</th>`).join("");
  const rows = res.rows
    .map((r) => `<tr>${r.map((v) => `<td>${v === null ? "<i>null</i>" : v}</td>`).join("")}</tr>`)
    .join("");

  resultEl.innerHTML = `
    <div class="result-decision allow">${decisionLine}</div>
    <div class="result-table-wrap">
      <table class="data-table">
        <thead><tr>${cols}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function loadPolicies() {
  const policies = await api("/api/policies");
  fillTable("policy-p", policies.p);
  fillTable("policy-g", policies.g);
  fillTable("policy-g2", policies.g2);
  fillTable("policy-org", policies.resource_org);
}

function fillTable(id, rows) {
  const tbody = document.querySelector(`#${id} tbody`);
  tbody.innerHTML = rows.map((r) => `<tr>${r.map((v) => `<td>${v}</td>`).join("")}</tr>`).join("") ||
    `<tr><td>&mdash; none &mdash;</td></tr>`;
}

async function loadPolicyOptions() {
  const opts = await api("/api/policy-options");
  grantSub.innerHTML = opts.subjects.map((s) => `<option value="${s}">${s}</option>`).join("");
  grantObj.innerHTML = opts.resources.map((r) => `<option value="${r}">${r}</option>`).join("");
}

async function refreshAll() {
  await Promise.all([loadCatalog(), loadPolicies()]);
}

// ---------------------------------------------------------------------
// Airflow Jobs tab
// ---------------------------------------------------------------------
async function loadAirflowDags() {
  const dags = await api("/api/airflow/dags");
  dagList.innerHTML = dags
    .map((dag) => {
      const chips = Object.entries(dag.permissions)
        .map(([action, allowed]) => `<span class="perm-chip ${allowed ? "allow" : "deny"}">${action}</span>`)
        .join("");
      return `
        <div class="dag-row">
          <div class="dag-head">
            <span class="name">${dag.label} <span class="org-chip" title="belongs to ${dag.org_label}">${dag.org}</span></span>
            <span class="dag-id mono">${dag.id}</span>
          </div>
          <p class="dag-desc">${dag.description}</p>
          <div class="perm-chips">${chips}</div>
        </div>`;
    })
    .join("");
}

async function loadAirflowPolicies() {
  const policies = await api("/api/policies");
  const dagPolicies = policies.p.filter(([, obj]) => obj.startsWith("airflow.dag."));
  const dagGroups = policies.g; // group membership isn't resource-scoped, show as-is
  const dagOrgs = policies.resource_org.filter(([resource]) => resource.startsWith("airflow.dag."));
  fillTable("airflow-policy-p", dagPolicies);
  fillTable("airflow-policy-g", dagGroups);
  fillTable("airflow-policy-org", dagOrgs);
}

async function loadAirflowPolicyOptions() {
  const opts = await api("/api/airflow/policy-options");
  airflowGrantSub.innerHTML = opts.subjects.map((s) => `<option value="${s}">${s}</option>`).join("");
  airflowGrantObj.innerHTML = opts.resources.map((r) => `<option value="${r}">${r}</option>`).join("");
  airflowGrantAct.innerHTML = opts.actions.map((a) => `<option value="${a}">${a}</option>`).join("");
}

// ---------------------------------------------------------------------
// Models tab
// ---------------------------------------------------------------------
function formatTimestamp(ms) {
  return ms ? new Date(ms).toLocaleString() : "—";
}

async function loadModels() {
  const models = await api("/api/models");

  modelList.innerHTML =
    models
      .map((model) => {
        const versionRows = model.versions
          .map((v) => {
            const metrics = Object.entries(v.metrics)
              .map(([k, val]) => `<span class="perm-chip allow">${k}=${Number(val).toFixed(4)}</span>`)
              .join("");
            const params = Object.entries(v.params)
              .map(([k, val]) => `<span class="perm-chip deny">${k}=${val}</span>`)
              .join("");
            return `
          <div class="model-version-row ${v.deployed ? "deployed" : ""}" data-model="${model.name}" data-version="${v.version}">
            <div class="dag-head">
              <span class="name">v${v.version} <span class="mono hint-inline">${v.run_id.slice(0, 8)}</span></span>
              ${v.deployed ? '<span class="org-chip" title="currently serving /invoke">DEPLOYED</span>' : ""}
            </div>
            <p class="dag-desc">trained ${formatTimestamp(v.created_at)}</p>
            <div class="perm-chips">${metrics}${params}</div>
            <div class="grant-actions model-version-actions">
              <button type="button" class="btn ${v.deployed ? "btn-deny" : "btn-allow"} btn-sm" data-action="deploy" ${v.deployed ? "disabled" : ""}>
                ${v.deployed ? "Deployed" : "Deploy"}
              </button>
            </div>
          </div>`;
          })
          .join("");
        return `
        <div class="model-block">
          <div class="schema-head">
            <span>${model.name}</span>
            <span class="hint-inline">${model.versions.length} version(s)${model.deployed_version ? `, v${model.deployed_version} deployed` : ", none deployed"}</span>
          </div>
          ${versionRows}
        </div>`;
      })
      .join("") ||
    `<p class="result-empty">No models trained yet — run the <code>train_sample_model</code> Dag on the Airflow Jobs tab.</p>`;

  modelList.querySelectorAll("[data-action='deploy']").forEach((btn) => {
    btn.addEventListener("click", () => deployModelVersion(btn.closest(".model-version-row").dataset.model, btn.closest(".model-version-row").dataset.version));
  });

  modelInvokeSelect.innerHTML =
    models.map((m) => `<option value="${m.name}">${m.name}${m.deployed_version ? ` (v${m.deployed_version})` : " (not deployed)"}</option>`).join("") ||
    `<option value="">— no models —</option>`;
}

async function deployModelVersion(name, version) {
  try {
    await api(`/api/models/${encodeURIComponent(name)}/versions/${encodeURIComponent(version)}/deploy`, { method: "POST" });
    await loadModels();
  } catch (err) {
    modelInvokeResult.innerHTML = `<div class="result-decision deny">✕ ${err.message}</div>`;
  }
}

// ---------------------------------------------------------------------
// Storage tab
// ---------------------------------------------------------------------
function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function loadStorageFiles() {
  const data = await api("/api/storage/files");
  const bucketNames = Object.keys(data.buckets);
  storageBucketTag.textContent = data.org;
  storageBucketTag.title = `belongs to ${data.org_label}`;
  // Every bucket name below came straight from MinIO's own ListBuckets
  // response (see /api/storage/files) — nothing here is a config lookup.
  storageBucketName.textContent = bucketNames.join(", ") || "(none)";

  storageFileList.innerHTML =
    bucketNames
      .map((bucket) => {
        const files = data.buckets[bucket];
        const rows =
          files
            .map(
              (f) => `
          <div class="file-row" data-bucket="${bucket}" data-key="${f.key}">
            <span class="name">${f.key}</span>
            <span class="size">${formatSize(f.size)}</span>
          </div>`
            )
            .join("") || `<p class="result-empty">No files in this bucket.</p>`;
        return `
          <div class="bucket-group">
            <h3 class="bucket-heading">${bucket} <span class="hint-inline">(from MinIO's own ListBuckets response)</span></h3>
            ${rows}
          </div>`;
      })
      .join("") || `<p class="result-empty">No buckets visible to this org's credentials.</p>`;

  storageFileList.querySelectorAll(".file-row").forEach((row) => {
    row.addEventListener("click", () => previewStorageFile(row.dataset.bucket, row.dataset.key));
  });
}

async function previewStorageFile(bucket, key) {
  storagePreview.innerHTML = `<p class="result-empty">Loading…</p>`;
  try {
    const res = await api(
      `/api/storage/file?bucket=${encodeURIComponent(bucket)}&key=${encodeURIComponent(key)}`
    );
    storagePreview.innerHTML = `<div class="file-preview">${escapeHtml(res.content)}</div>`;
  } catch (err) {
    storagePreview.innerHTML = `<div class="result-decision deny">✕ ${err.message}</div>`;
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function runStorageCrossCheck() {
  storageCrossCheckResult.innerHTML = `<p class="result-empty">Trying…</p>`;
  try {
    const res = await api("/api/storage/cross-check");
    const cls = res.denied ? "denied" : "leaked";
    const line = res.denied
      ? `✓ DENIED — ${res.acting_org}'s credentials could not reach bucket "${res.bucket}" (discovered via MinIO's root ListBuckets, not owned by ${res.acting_org})`
      : `✕ NOT DENIED — this should not happen`;
    storageCrossCheckResult.innerHTML = `
      <div class="cross-check-result ${cls}">
        ${line}
        <div class="result-message">${res.code ? `${res.code}: ` : ""}${res.message}</div>
      </div>`;
  } catch (err) {
    storageCrossCheckResult.innerHTML = `<div class="result-decision deny">✕ ${err.message}</div>`;
  }
}

// ---------------------------------------------------------------------
// Users tab
// ---------------------------------------------------------------------
let editingUsername = null;

async function loadUsers() {
  const [users, orgs] = await Promise.all([api("/api/users"), api("/api/organizations")]);
  userOrgSelect.innerHTML = orgs.map((o) => `<option value="${o.id}">${o.label} (${o.id})</option>`).join("");

  usersTableBody.innerHTML =
    users
      .map(
        (u) => `
      <tr data-username="${u.id}">
        <td class="mono">${u.id}</td>
        <td>${u.label}</td>
        <td>${u.title || "—"}</td>
        <td><span class="org-chip" title="${u.org_label}">${u.org}</span></td>
        <td>${u.region}</td>
        <td>${u.hr ? "yes" : "no"}</td>
        <td>${u.groups.join(", ") || "—"}</td>
        <td class="users-row-actions">
          <button type="button" class="btn btn-allow btn-sm" data-action="edit">Edit</button>
          <button type="button" class="btn btn-deny btn-sm" data-action="delete">Delete</button>
        </td>
      </tr>`
      )
      .join("") || `<tr><td colspan="8">No users yet.</td></tr>`;

  usersTableBody.querySelectorAll("[data-action='edit']").forEach((btn) => {
    btn.addEventListener("click", () => openEditUser(btn.closest("tr").dataset.username, users));
  });
  usersTableBody.querySelectorAll("[data-action='delete']").forEach((btn) => {
    btn.addEventListener("click", () => deleteUser(btn.closest("tr").dataset.username));
  });
}

function openCreateUser() {
  editingUsername = null;
  userForm.reset();
  userFormTitle.textContent = "New user";
  userUsername.disabled = false;
  userPassword.required = true;
  userPassword.placeholder = "";
  userForm.hidden = false;
  userUsername.focus();
}

function openEditUser(username, users) {
  const u = users.find((x) => x.id === username);
  if (!u) return;
  editingUsername = username;
  userForm.reset();
  userFormTitle.textContent = `Edit ${username}`;
  userUsername.value = username;
  userUsername.disabled = true;
  userPassword.required = false;
  userPassword.placeholder = "leave blank to keep the current password";
  const [first, ...rest] = u.label.split(" ");
  userFirstName.value = first || username;
  userLastName.value = rest.join(" ");
  userTitle.value = u.title;
  userOrgSelect.value = u.org;
  userRegion.value = u.region;
  userHr.checked = u.hr;
  userGroupsInput.value = u.groups.join(", ");
  userForm.hidden = false;
  userFirstName.focus();
}

function closeUserForm() {
  userForm.hidden = true;
  userForm.reset();
  editingUsername = null;
}

async function deleteUser(username) {
  if (!confirm(`Delete ${username}? This removes their Keycloak account and every policy grant naming them directly.`)) {
    return;
  }
  usersStatus.textContent = `Deleting ${username}…`;
  try {
    await api(`/api/users/${encodeURIComponent(username)}`, { method: "DELETE" });
    usersStatus.textContent = `Deleted ${username}.`;
    await Promise.all([loadUsers(), loadPolicyOptions(), loadAirflowPolicyOptions()]);
  } catch (err) {
    usersStatus.textContent = `Error: ${err.message}`;
  }
}

// ---------------------------------------------------------------------
// Services tab
// ---------------------------------------------------------------------
function renderServices() {
  const services = window.SERVICES || DEFAULT_SERVICES;
  const host = window.location.hostname;
  const proto = window.location.protocol;

  servicesList.innerHTML = services
    .map((svc) => {
      if (!svc.port) {
        return `
          <div class="file-row service-row">
            <span class="name">${svc.name}</span>
            <span class="size">${svc.desc || ""} — internal only, not reachable from a browser</span>
          </div>`;
      }
      const url = `${svc.tcp ? "" : (svc.scheme || proto) + "//"}${host}:${svc.port}${svc.tcp ? "" : svc.path || "/"}`;
      const link = svc.tcp
        ? `<span class="name">${url}</span>`
        : `<a class="name" href="${url}" target="_blank" rel="noopener">${url}</a>`;
      return `
        <div class="file-row service-row">
          <span><strong>${svc.name}</strong> ${link}</span>
          <span class="size">${svc.desc || ""}</span>
        </div>`;
    })
    .join("");
}

// ---------------------------------------------------------------------
// Credentials tab
// ---------------------------------------------------------------------
async function renderCredentials() {
  const creds = window.CREDENTIALS || DEFAULT_CREDENTIALS;

  const rows = creds
    .map((c) => {
      const userCell = c.user ? `<code>${c.user}</code>` : `<span class="hint-inline">n/a</span>`;
      const passCell = c.pass ? `<code>${c.pass}</code>` : `<span class="hint-inline">n/a</span>`;
      return `<tr><td>${c.service}</td><td>${userCell}</td><td>${passCell}</td><td>${c.notes}</td></tr>`;
    })
    .join("");

  credentialsTable.innerHTML = `
    <div class="result-table-wrap">
      <table class="data-table">
        <thead><tr><th>Service</th><th>User</th><th>Password</th><th>Notes</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------
logoutBtn.addEventListener("click", () => keycloak.logout());

storageCrossCheckBtn.addEventListener("click", runStorageCrossCheck);

grantForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const action = e.submitter.dataset.action;
  const endpoint = action === "grant" ? "/api/policies/grant" : "/api/policies/revoke";
  const sub = grantSub.value;
  const obj = grantObj.value;
  try {
    await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sub, obj, act: "select" }),
    });
    grantStatus.textContent = `${action === "grant" ? "Granted" : "Revoked"}: p(${sub}, ${obj}, select). Click a table again to see it take effect.`;
    await refreshAll();
  } catch (err) {
    grantStatus.textContent = `Error: ${err.message}`;
  }
});

airflowGrantForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const action = e.submitter.dataset.action;
  const endpoint = action === "grant" ? "/api/policies/grant" : "/api/policies/revoke";
  const sub = airflowGrantSub.value;
  const obj = airflowGrantObj.value;
  const act = airflowGrantAct.value;
  try {
    await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sub, obj, act }),
    });
    airflowGrantStatus.textContent = `${action === "grant" ? "Granted" : "Revoked"}: p(${sub}, ${obj}, ${act}). Updated below — no restart needed.`;
    await Promise.all([loadAirflowDags(), loadAirflowPolicies()]);
  } catch (err) {
    airflowGrantStatus.textContent = `Error: ${err.message}`;
  }
});

modelInvokeForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = modelInvokeSelect.value;
  if (!name) return;
  let rows;
  try {
    rows = JSON.parse(modelInvokeRows.value);
  } catch (err) {
    modelInvokeResult.innerHTML = `<div class="result-decision deny">✕ Feature rows must be valid JSON, e.g. [[5.1, 3.5, 1.4, 0.2]]</div>`;
    return;
  }
  modelInvokeResult.innerHTML = `<p class="result-empty">Running…</p>`;
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/invoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    modelInvokeResult.innerHTML = `
      <div class="result-decision allow">${res.model} v${res.version}</div>
      <pre class="file-preview">${JSON.stringify(res.predictions)}</pre>`;
  } catch (err) {
    modelInvokeResult.innerHTML = `<div class="result-decision deny">✕ ${err.message}</div>`;
  }
});

userNewBtn.addEventListener("click", () => {
  if (userForm.hidden) {
    openCreateUser();
  } else {
    closeUserForm();
  }
});

userFormCancel.addEventListener("click", closeUserForm);

userForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const groups = userGroupsInput.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const fields = {
    first_name: userFirstName.value.trim(),
    last_name: userLastName.value.trim(),
    title: userTitle.value.trim(),
    org: userOrgSelect.value,
    region: userRegion.value.trim(),
    hr: userHr.checked,
    groups,
  };
  if (userPassword.value) fields.password = userPassword.value;

  try {
    if (editingUsername) {
      // Blank email here just means "leave it as whatever Keycloak already
      // has" — GET /api/users never returns it, so there's nothing to
      // prefill the field with in the first place.
      if (userEmail.value.trim()) fields.email = userEmail.value.trim();
      await api(`/api/users/${encodeURIComponent(editingUsername)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      usersStatus.textContent = `Updated ${editingUsername}.`;
    } else {
      const username = userUsername.value.trim();
      fields.email = userEmail.value.trim();
      await api("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, ...fields }),
      });
      usersStatus.textContent = `Created ${username} — they can log in to Keycloak with the password you set.`;
    }
    closeUserForm();
    await Promise.all([loadUsers(), loadPolicyOptions(), loadAirflowPolicyOptions()]);
  } catch (err) {
    usersStatus.textContent = `Error: ${err.message}`;
  }
});

(async function init() {
  // login-required: keycloak.init() itself redirects the browser to
  // Keycloak's own login page if there's no valid session yet, so
  // nothing below runs until a real Keycloak user has authenticated.
  const authenticated = await keycloak.init({ onLoad: "login-required", pkceMethod: "S256" });
  if (!authenticated) return;

  await loadCurrentUser();
  await loadPolicyOptions();
  await loadAirflowPolicyOptions();
  await refreshAll();
})();
