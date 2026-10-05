// Access policies page — the authz database as a governance console:
// grants (p) with search, scope filter and inline revoke (+ undo), plus
// read-only tabs for groups, the resource tree and organization tags.
import { changePolicy, loadPolicies, orgOf, state } from "../state.js";
import { html, render, toast } from "../dom.js";
import { icon } from "../icons.js";
import { emptyState, orgBadge, pageHeader, principalChip, resourceChip, resourceKind } from "../ui-parts.js";
import { openGrantDrawer } from "./grant-drawer.js";
import { groupsPanel, orgsPanel, treePanel } from "./policies-panels.js";

let tabsEl, bodyEl;
let tab = "grants";
let scope = "all"; // all | data | jobs
let query = "";

const TABS = [
  { id: "grants", label: "Grants", count: () => state.policies.p.length },
  { id: "groups", label: "Groups", count: () => new Set(state.policies.g.map(([, g]) => g)).size },
  { id: "tree", label: "Resource tree", count: () => state.policies.g2.length },
  { id: "orgs", label: "Organizations", count: () => Object.keys(state.orgs).length },
];
const SCOPES = { all: "All", data: "Data", jobs: "Jobs" };

function renderTabs() {
  render(tabsEl, TABS.map(
    (t) => html`<button type="button" role="tab" class="tab${t.id === tab ? " is-active" : ""}" aria-selected="${String(t.id === tab)}" data-tab="${t.id}">
      ${t.label}<span class="tab-count">${t.count()}</span></button>`
  ));
}

function matches([sub, obj, act]) {
  const inScope = scope === "all" || (scope === "jobs") === (resourceKind(obj) === "dag");
  return inScope && (!query || `${sub} ${obj} ${act}`.toLowerCase().includes(query));
}

function grantsPanel() {
  const rows = state.policies.p.map((p, i) => ({ p, i })).filter(({ p }) => matches(p));
  return html`
    <div class="toolbar">
      <label class="search">${icon("search")}<input type="search" placeholder="Search principal, resource, action" aria-label="Search grants" value="${query}" data-query /></label>
      <div class="segmented" role="radiogroup" aria-label="Scope">${Object.entries(SCOPES).map(
        ([id, label]) => html`<button type="button" role="radio" aria-checked="${String(id === scope)}" class="segmented-btn${id === scope ? " is-active" : ""}" data-scope="${id}">${label}</button>`
      )}</div>
    </div>
    ${rows.length
      ? html`<div class="pane grid-wrap"><table class="list-table grants-table">
          <thead><tr><th>Principal</th><th>Resource</th><th>Action</th><th>Resource org</th><th><span class="visually-hidden">Revoke</span></th></tr></thead>
          <tbody>${rows.map(({ p: [sub, obj, act], i }) => html`<tr>
            <td>${principalChip(sub)}</td><td>${resourceChip(obj)}</td><td><code class="action">${act}</code></td>
            <td>${orgBadge(orgOf(obj))}</td>
            <td class="row-actions"><button type="button" class="btn btn-danger-soft btn-sm" data-revoke="${i}">Revoke</button></td>
          </tr>`)}</tbody>
        </table></div>`
      : emptyState({ iconName: "search", title: "No grants match", body: "Try a different search or scope." })}`;
}

const PANELS = { grants: grantsPanel, groups: groupsPanel, tree: treePanel, orgs: orgsPanel };

function renderBody() {
  renderTabs();
  render(bodyEl, PANELS[tab]());
}

async function revoke(index) {
  const [sub, obj, act] = state.policies.p[index];
  try {
    await changePolicy("revoke", { sub, obj, act });
    toast(`Revoked p(${sub}, ${obj}, ${act})`, {
      tone: "allow",
      actionLabel: "Undo",
      onAction: () => changePolicy("grant", { sub, obj, act }).catch((err) => toast(`Undo failed: ${err.message}`, { tone: "deny" })),
    });
  } catch (err) {
    toast(`Couldn't revoke: ${err.message}`, { tone: "deny" });
  }
}

export default {
  mount(root) {
    render(root, html`
      ${pageHeader({
        eyebrow: "Govern · authz database",
        title: "Access policies",
        lede: html`The only place policy is written. Each change lands in Postgres, then the backend pushes the whole snapshot to OPA (<code>PUT /v1/data/policy_data</code>) inside the same request.`,
        actions: html`<button type="button" class="btn btn-primary" data-new>${icon("plus")}New grant</button>`,
      })}
      <div class="tabs tabs-page" role="tablist" data-tabs></div>
      <div data-body></div>`);
    tabsEl = root.querySelector("[data-tabs]");
    bodyEl = root.querySelector("[data-body]");
    root.addEventListener("click", (e) => {
      const t = e.target.closest("[data-tab],[data-scope],[data-revoke],[data-new]");
      if (!t) return;
      if (t.dataset.tab) tab = t.dataset.tab;
      else if (t.dataset.scope) scope = t.dataset.scope;
      else if (t.dataset.revoke) return revoke(Number(t.dataset.revoke));
      else return openGrantDrawer();
      renderBody();
    });
    root.addEventListener("input", (e) => {
      if (!e.target.matches("[data-query]")) return;
      query = e.target.value.trim().toLowerCase();
      const caret = e.target.selectionStart;
      renderBody();
      const input = bodyEl.querySelector("[data-query]");
      input.focus();
      input.setSelectionRange(caret, caret);
    });
  },
  async refresh() {
    await loadPolicies(); // pick up changes made from another browser tab
    renderBody();
  },
};
