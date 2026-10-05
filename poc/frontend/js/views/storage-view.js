// Storage page — an S3-console-style browser over MinIO. No OPA here at all:
// bucket names and objects come live from MinIO using the acting user's
// org credential, and isolation is that credential's own IAM policy.
import { api, withQuery } from "../api-client.js";
import { currentUser, state } from "../state.js";
import { formatBytes, formatDate, html, render } from "../dom.js";
import { icon } from "../icons.js";
import { emptyState, errorState, orgBadge, pageHeader, skeleton } from "../ui-parts.js";

let headEl, browserEl, previewEl, isolationEl;
let data = null; // /api/storage/files response
let bucket = null;
let openKey = null;
let cross = null; // last cross-check result or { error }

// Minimal CSV line parser — the seed CSVs are simple, but a quoted comma
// shouldn't silently shift columns either. Multi-line quoted fields aren't
// supported (none of the seed files use them).
function parseCsvLine(line) {
  const out = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted && c === '"' && line[i + 1] === '"') {
      cur += '"'; // escaped quote inside a quoted field
      i++;
    } else if (c === '"') {
      quoted = !quoted;
    } else if (c === "," && !quoted) {
      out.push(cur);
      cur = "";
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out;
}

const parseCsv = (text) => text.trim().split(/\r?\n/).map(parseCsvLine);

function renderHead() {
  render(headEl, pageHeader({
    eyebrow: "Explore · MinIO",
    title: "Storage",
    lede: html`Files visible to <strong>${currentUser().label}</strong>'s organization. Buckets are listed live by MinIO for that org's own credential. This page makes no OPA call: isolation here is MinIO IAM.`,
    actions: html`<span class="badge badge-amber">${icon("key")}MinIO IAM · no OPA</span>`,
  }));
}

function renderBrowser() {
  if (!data) return render(browserEl, skeleton(5));
  const buckets = Object.keys(data.buckets);
  if (!buckets.length) return render(browserEl, emptyState({ iconName: "bucket", title: "No buckets visible", body: "MinIO returned no buckets for this org's credential." }));
  const files = data.buckets[bucket] || [];
  render(browserEl, html`
    <div class="pane-head">
      <div class="pane-title">${icon("bucket")}<span>${bucket}</span>${orgBadge(data.org)}</div>
      ${buckets.length > 1
        ? html`<div class="segmented">${buckets.map((b) => html`<button type="button" class="segmented-btn${b === bucket ? " is-active" : ""}" data-bucket="${b}">${b}</button>`)}</div>`
        : html`<span class="muted small">from MinIO <code>ListBuckets</code></span>`}
    </div>
    ${files.length
      ? html`<div class="grid-wrap"><table class="list-table file-table">
          <thead><tr><th>Name</th><th class="num">Size</th><th>Modified</th></tr></thead>
          <tbody>${files.map((f) => {
            const slash = f.key.lastIndexOf("/");
            return html`<tr class="file-row${f.key === openKey ? " is-active" : ""}" data-key="${f.key}" tabindex="0">
              <td><span class="file-name">${icon("file")}${slash >= 0 ? html`<span class="muted">${f.key.slice(0, slash + 1)}</span>` : ""}${f.key.slice(slash + 1)}</span></td>
              <td class="num">${formatBytes(f.size)}</td>
              <td class="muted">${formatDate(f.last_modified)}</td>
            </tr>`;
          })}</tbody>
        </table></div>`
      : emptyState({ iconName: "file", title: "This bucket is empty" })}`);
}

async function openFile(key) {
  openKey = key;
  renderBrowser();
  render(previewEl, html`<div class="pane-head"><div class="pane-title">${icon("file")}<span>${key}</span></div></div>${skeleton(6)}`);
  try {
    const res = await api(withQuery("/api/storage/file", { user: state.userId, bucket, key }));
    const body = key.endsWith(".csv") ? csvTable(parseCsv(res.content)) : html`<pre class="file-text">${res.content}</pre>`;
    render(previewEl, html`<div class="pane-head"><div class="pane-title">${icon("file")}<span>${key}</span></div><span class="muted small">read with ${data.org}'s credential</span></div>${body}`);
  } catch (err) {
    render(previewEl, errorState(err));
  }
}

const csvTable = ([head = [], ...rows]) => html`<div class="grid-wrap"><table class="grid">
  <thead><tr>${head.map((h) => html`<th>${h}</th>`)}</tr></thead>
  <tbody>${rows.map((r) => html`<tr>${r.map((v) => html`<td>${v}</td>`)}</tr>`)}</tbody></table></div>`;

function renderPreviewEmpty() {
  render(previewEl, emptyState({ iconName: "eye", title: "Nothing open", body: "Pick a file to preview its contents." }));
}

function crossResult() {
  if (!cross) return "";
  if (cross.error) return html`<div class="callout callout-deny">${icon("alert")}<div>${cross.error.message}</div></div>`;
  if (cross.denied === null) return html`<div class="callout callout-info">${icon("info")}<div>${cross.message}</div></div>`;
  if (cross.denied) {
    return html`<div class="callout callout-allow">${icon("check")}<div><strong>Isolation held.</strong> MinIO refused <code>${cross.acting_org}</code>'s credential on bucket <code>${cross.bucket}</code>.<p class="mono-wrap">${cross.code}: ${cross.message}</p></div></div>`;
  }
  return html`<div class="callout callout-deny">${icon("x")}<div><strong>Isolation broken.</strong> ${cross.message}</div></div>`;
}

function renderIsolation() {
  render(isolationEl, html`
    <div class="isolation-text">
      <p class="eyebrow">Cross-org isolation test</p>
      <p>Uses <code>${data?.org || currentUser().org}</code>'s own credential to list a bucket owned by another organization. The expected answer is MinIO's own <code>AccessDenied</code>, not a check in this backend.</p>
    </div>
    <button type="button" class="btn btn-secondary" data-cross>${icon("play")}Try the other org's bucket</button>
    <div class="isolation-result" aria-live="polite">${crossResult()}</div>`);
}

async function runCrossCheck() {
  cross = null;
  render(isolationEl.querySelector(".isolation-result"), skeleton(2));
  try {
    cross = await api(withQuery("/api/storage/cross-check", { user: state.userId }));
  } catch (error) {
    cross = { error };
  }
  renderIsolation();
}

export default {
  mount(root) {
    render(root, html`
      <div data-head></div>
      <div class="split split-storage">
        <section class="pane" data-browser></section>
        <section class="pane preview-pane" data-preview></section>
      </div>
      <section class="pane isolation" data-isolation></section>`);
    headEl = root.querySelector("[data-head]");
    browserEl = root.querySelector("[data-browser]");
    previewEl = root.querySelector("[data-preview]");
    isolationEl = root.querySelector("[data-isolation]");
    root.addEventListener("click", (e) => {
      const t = e.target.closest("[data-key],[data-bucket],[data-cross]");
      if (!t) return;
      if (t.dataset.key) openFile(t.dataset.key);
      else if (t.dataset.bucket) {
        bucket = t.dataset.bucket;
        openKey = null;
        renderBrowser();
        renderPreviewEmpty();
      } else runCrossCheck();
    });
    root.addEventListener("keydown", (e) => {
      const row = e.target.closest?.("[data-key]");
      if (row && e.key === "Enter") openFile(row.dataset.key);
    });
  },
  async refresh() {
    const prevOrg = data?.org;
    renderHead();
    if (!data) {
      // First load (or a previous failure): show placeholders straight away —
      // MinIO being unreachable takes the backend ~12s to report.
      renderBrowser();
      renderPreviewEmpty();
      renderIsolation();
    }
    const user = state.userId;
    let next;
    try {
      next = await api(withQuery("/api/storage/files", { user }));
    } catch (err) {
      data = null;
      render(browserEl, errorState(err));
      renderPreviewEmpty();
      return renderIsolation();
    }
    if (user !== state.userId) return; // user switched mid-flight; the newer refresh wins
    data = next;
    if (data.org !== prevOrg || !(bucket in data.buckets)) {
      // Different org → different credential → nothing from before applies.
      bucket = Object.keys(data.buckets)[0] || null;
      openKey = null;
      cross = null;
      renderPreviewEmpty();
    }
    renderBrowser();
    renderIsolation();
  },
};
