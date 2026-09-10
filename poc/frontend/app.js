const API = window.API_BASE || "http://localhost:8001";

const userSelect = document.getElementById("user-select");
const userOrg = document.getElementById("user-org");
const userGroups = document.getElementById("user-groups");
const catalogTree = document.getElementById("catalog-tree");
const resultEl = document.getElementById("result");
const grantForm = document.getElementById("grant-form");
const grantSub = document.getElementById("grant-sub");
const grantObj = document.getElementById("grant-obj");
const grantStatus = document.getElementById("grant-status");

const dagList = document.getElementById("dag-list");
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

let currentUser = null;

// Same demo-only values as poc/docker-compose.yml / poc/k8s/01-secrets.yaml
// and poc/backend/app/seed.py's ORG_STORAGE. Airflow is the one exception —
// SimpleAuthManager mints a fresh random password per user on every apiserver
// start, so there's no fixed value to show; window.AIRFLOW_LOG_CMD (k8s
// override in poc/k8s/07-frontend.yaml) says how to fetch the current one.
const DEFAULT_AIRFLOW_LOG_CMD = 'docker compose logs airflow-apiserver | grep "Password for user"';

const DEFAULT_CREDENTIALS = [
  { service: "Postgres", user: "poc", pass: "pocpass", notes: "one instance, three databases: authz, demo, airflow" },
  { service: "MinIO (console / admin)", user: "pocadmin", pass: "pocadminpass", notes: "full admin — MinIO Console login" },
  { service: "MinIO — org-001 (Acme Retail)", user: "org001svc", pass: "org001SecretKey123", notes: "scoped to org-001's bucket only — native MinIO IAM, no OPA involved" },
  { service: "MinIO — org-002 (Globex Logistics)", user: "org002svc", pass: "org002SecretKey456", notes: "scoped to org-002's bucket only" },
  { service: "Airflow", user: "alice", pass: null, notes: "USER · org-001 · grp_data_eng" },
  { service: "Airflow", user: "bob", pass: null, notes: "USER · org-001" },
  { service: "Airflow", user: "carol", pass: null, notes: "USER · org-001 · grp_hr" },
  { service: "Airflow", user: "dave", pass: null, notes: "VIEWER · org-001" },
  { service: "Airflow", user: "erin", pass: null, notes: "USER · org-002 · grp_data_eng (same group as alice, different org — isolation still holds)" },
  { service: "Trino", user: null, pass: null, notes: "no authentication — client sends user= directly; trino.rego is the only gate" },
  { service: "OPA", user: null, pass: null, notes: "no authentication — internal service only" },
];

// docker-compose default — every service is published on a host port.
// Kubernetes overrides window.SERVICES (see poc/k8s/07-frontend.yaml) with
// NodePorts, and marks OPA/Postgres internal-only since they're ClusterIP there.
const DEFAULT_SERVICES = [
  { name: "Frontend", port: 3000, path: "/", desc: "this UI" },
  { name: "Backend API", port: 8001, path: "/api/health", desc: "FastAPI" },
  { name: "Trino", port: 8080, path: "/ui/", desc: "coordinator UI" },
  { name: "Airflow", port: 8082, path: "/", desc: "webserver" },
  { name: "MinIO API", port: 9000, path: "/", desc: "S3 endpoint" },
  { name: "MinIO Console", port: 9001, path: "/", desc: "browser UI" },
  { name: "OPA", port: 8181, path: "/health", desc: "policy engine" },
  { name: "Postgres", port: 5433, desc: "authz · demo · airflow DBs", tcp: true },
];

async function api(path, opts) {
  const res = await fetch(`${API}${path}`, opts);
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
    document.getElementById("tab-storage").hidden = btn.dataset.tab !== "storage";
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
    if (btn.dataset.tab === "storage") {
      loadStorageFiles();
      storageCrossCheckResult.innerHTML = "";
      storagePreview.innerHTML = `<p class="result-empty">Click a file on the left to preview it.</p>`;
    }
  });
});

// ---------------------------------------------------------------------
// Users (shared across tabs)
// ---------------------------------------------------------------------
async function loadUsers() {
  const users = await api("/api/users");
  userSelect.innerHTML = users
    .map((u) => `<option value="${u.id}">${u.label} — ${u.title}</option>`)
    .join("");
  currentUser = users[0].id;
  userSelect.value = currentUser;
  renderUserGroups(users);
}

function renderUserGroups(users) {
  const u = users.find((x) => x.id === userSelect.value);
  userGroups.textContent = u.groups.length ? `member of ${u.groups.join(", ")}` : "no group membership";
  userOrg.textContent = `${u.org_label} (${u.org})`;
}

// ---------------------------------------------------------------------
// Data Catalog tab
// ---------------------------------------------------------------------
async function loadCatalog() {
  const tree = await api(`/api/catalog?user=${encodeURIComponent(currentUser)}`);
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
      body: JSON.stringify({ user: currentUser, resource }),
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
  const dags = await api(`/api/airflow/dags?user=${encodeURIComponent(currentUser)}`);
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
// Storage tab
// ---------------------------------------------------------------------
function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function loadStorageFiles() {
  const data = await api(`/api/storage/files?user=${encodeURIComponent(currentUser)}`);
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
      `/api/storage/file?user=${encodeURIComponent(currentUser)}&bucket=${encodeURIComponent(bucket)}&key=${encodeURIComponent(key)}`
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
    const res = await api(`/api/storage/cross-check?user=${encodeURIComponent(currentUser)}`);
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
      const url = `${svc.tcp ? "" : proto + "//"}${host}:${svc.port}${svc.tcp ? "" : svc.path || "/"}`;
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
  const logCmd = window.AIRFLOW_LOG_CMD || DEFAULT_AIRFLOW_LOG_CMD;

  // Live Airflow passwords, read by the backend straight out of the
  // running apiserver pod's own logs (see /api/airflow/credentials and
  // app/airflow_client.py) — falls back to log-fetch instructions when
  // that's unavailable (e.g. running under plain docker-compose).
  let livePasswords = {};
  try {
    const res = await api("/api/airflow/credentials");
    if (res.available) livePasswords = res.passwords || {};
  } catch (err) {
    // backend unreachable or endpoint missing — fall back silently
  }

  const rows = creds
    .map((c) => {
      const userCell = c.user ? `<code>${c.user}</code>` : `<span class="hint-inline">n/a</span>`;
      let passCell;
      if (c.pass) {
        passCell = `<code>${c.pass}</code>`;
      } else if (c.service === "Airflow" && livePasswords[c.user]) {
        passCell = `<code>${livePasswords[c.user]}</code>`;
      } else if (c.service === "Airflow") {
        passCell = `<span class="hint-inline">generated at startup — <code>${logCmd}</code></span>`;
      } else {
        passCell = `<span class="hint-inline">n/a</span>`;
      }
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
userSelect.addEventListener("change", async () => {
  currentUser = userSelect.value;
  const users = await api("/api/users");
  renderUserGroups(users);
  resultEl.innerHTML = `<p class="result-empty">Pick a table on the left to see what OPA decides.</p>`;
  await loadCatalog();
  if (!document.getElementById("tab-airflow").hidden) {
    await loadAirflowDags();
  }
  if (!document.getElementById("tab-storage").hidden) {
    await loadStorageFiles();
    storageCrossCheckResult.innerHTML = "";
    storagePreview.innerHTML = `<p class="result-empty">Click a file on the left to preview it.</p>`;
  }
});

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

(async function init() {
  await loadUsers();
  await loadPolicyOptions();
  await loadAirflowPolicyOptions();
  await refreshAll();
})();
