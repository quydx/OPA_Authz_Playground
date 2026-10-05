// Catalog page — a Unity-Catalog-style explorer: schema/table tree on the
// left (coloured by the backend's OPA check), table detail on the right.
// Opening a table runs the fixed SELECT through Trino as the acting user;
// switching users re-runs it, so the same table can be compared side by side.
import { api, withQuery } from "../api-client.js";
import { currentUser, state } from "../state.js";
import { html, render } from "../dom.js";
import { icon } from "../icons.js";
import { errorState, pageHeader, skeleton } from "../ui-parts.js";
import { openGrantDrawer } from "./grant-drawer.js";
import { renderAccessTab, renderDataTab, renderOverview, renderTableHeader } from "./catalog-table-detail.js";

let headEl, treeEl, detailEl;
let tree = null;
let selected = null; // "schema.table" — survives user switches
let activeTab = "data";
let lastQuery = null; // { result } | { error } for the open table
let filter = "";
let queryToken = 0;
const collapsed = new Set();

function nodeFor(resource) {
  for (const schema of tree || []) {
    const table = schema.tables.find((t) => t.resource === resource);
    if (table) return { schema, table };
  }
  return null;
}

function renderHead() {
  render(headEl, pageHeader({
    eyebrow: "Explore · Trino",
    title: "Catalog",
    lede: html`What <strong>${currentUser().label}</strong> can read in the <code>postgres</code> catalog. Green and locked marks come from OPA; opening a table queries it through Trino, which checks OPA again on its own.`,
    actions: html`<button type="button" class="btn btn-secondary" data-grant="">${icon("plus")}Grant access</button>`,
  }));
}

function renderTree() {
  if (!tree) return render(treeEl, skeleton(8));
  const groups = tree.map((schema) => {
    const tables = schema.tables.filter((t) => !filter || t.resource.toLowerCase().includes(filter));
    if (!tables.length) return "";
    const open = Boolean(filter) || !collapsed.has(schema.name);
    const readable = schema.tables.filter((t) => t.allowed).length;
    return html`
      <div class="tree-group">
        <button type="button" class="tree-node tree-schema" data-toggle="${schema.name}" aria-expanded="${String(open)}">
          ${icon(open ? "chevron-down" : "chevron-right", "tree-caret")}${icon("layers")}
          <span class="tree-label">${schema.name}</span>
          <span class="tree-org" title="${schema.org_label}">${schema.org}</span>
          <span class="tree-count" title="${readable} of ${schema.tables.length} tables readable">${readable}/${schema.tables.length}</span>
        </button>
        ${open
          ? html`<div class="tree-children">${tables.map(
              (t) => html`<button type="button" class="tree-node tree-table${t.allowed ? "" : " is-denied"}${t.resource === selected ? " is-active" : ""}"
                data-select="${t.resource}" title="${t.allowed ? "OPA allows select" : "OPA denies select"}">
                ${icon("table")}<span class="tree-label">${t.name}</span>${icon(t.allowed ? "check" : "lock", t.allowed ? "tone-allow" : "tone-muted")}
              </button>`
            )}</div>`
          : ""}
      </div>`;
  });
  render(treeEl, groups.some(Boolean) ? groups : html`<p class="tree-empty">No tables match “${filter}”.</p>`);
}

function renderDetail() {
  const node = selected && nodeFor(selected);
  if (!node) return render(detailEl, renderOverview(tree));
  const body = !lastQuery ? skeleton(7) : activeTab === "access" ? renderAccessTab(node.table) : renderDataTab(node.table, lastQuery);
  render(detailEl, html`${renderTableHeader(node.schema, node.table, activeTab)}<div class="detail-body">${body}</div>`);
}

async function openTable(resource) {
  selected = resource;
  lastQuery = null;
  renderTree();
  renderDetail();
  const token = ++queryToken;
  let outcome;
  try {
    outcome = { result: await api("/api/query", { method: "POST", body: { user: state.userId, resource } }) };
  } catch (error) {
    outcome = { error };
  }
  if (token !== queryToken) return; // another table (or user) was picked meanwhile
  lastQuery = outcome;
  renderDetail();
}

async function loadTree() {
  const user = state.userId;
  let next;
  try {
    next = await api(withQuery("/api/catalog", { user }));
  } catch (err) {
    tree = null;
    render(treeEl, errorState(err));
    return false;
  }
  if (user !== state.userId) return false; // user switched mid-flight; the newer refresh wins
  tree = next;
  renderTree();
  return true;
}

function onClick(e) {
  const t = e.target.closest("[data-select],[data-toggle],[data-tab],[data-back],[data-grant]");
  if (!t) return;
  const d = t.dataset;
  if (d.select) {
    activeTab = "data";
    openTable(d.select);
    // Stacked layout: the detail pane sits below the tree, so bring it into view.
    if (matchMedia("(max-width: 1080px)").matches) detailEl.scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (d.toggle) {
    collapsed.has(d.toggle) ? collapsed.delete(d.toggle) : collapsed.add(d.toggle);
    renderTree();
  } else if (d.tab) {
    activeTab = d.tab;
    renderDetail();
  } else if ("back" in d) {
    selected = null;
    queryToken++;
    renderTree();
    renderDetail();
  } else if ("grant" in d) {
    openGrantDrawer({ kind: "data", obj: d.grant || selected || undefined });
  }
}

export default {
  mount(root) {
    render(root, html`
      <div data-head></div>
      <div class="split split-catalog">
        <aside class="pane tree-pane" aria-label="Catalog tree">
          <div class="pane-head">
            <div class="pane-title">${icon("database")}<span>postgres</span><span class="muted">catalog</span></div>
            <label class="search">${icon("search")}<input type="search" placeholder="Filter tables" aria-label="Filter tables" data-filter /></label>
          </div>
          <div class="tree" data-tree></div>
        </aside>
        <section class="pane detail-pane" data-detail aria-live="polite"></section>
      </div>`);
    headEl = root.querySelector("[data-head]");
    treeEl = root.querySelector("[data-tree]");
    detailEl = root.querySelector("[data-detail]");
    root.querySelector("[data-filter]").addEventListener("input", (e) => {
      filter = e.target.value.trim().toLowerCase();
      renderTree();
    });
    root.addEventListener("click", onClick);
    renderTree();
  },
  async refresh() {
    renderHead();
    if (!(await loadTree())) return;
    if (selected && nodeFor(selected)) await openTable(selected);
    else renderDetail();
  },
};
