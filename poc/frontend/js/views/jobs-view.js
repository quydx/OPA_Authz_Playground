// Jobs page — an Airflow DAG list with a permission matrix per action.
// It previews policy only: every cell is the backend's data.app.allow check
// for (user, airflow.dag.<id>, action). Inside Airflow, opa_auth_manager asks
// data.airflow.allow — both wrap the same authz.allow(), so they agree.
import { api, withQuery } from "../api-client.js";
import { currentUser, state } from "../state.js";
import { html, render } from "../dom.js";
import { icon } from "../icons.js";
import { callout, decision, errorState, orgBadge, pageHeader, skeleton } from "../ui-parts.js";
import { explain, explainHtml } from "../access-explainer.js";
import { serviceUrl } from "../services-config.js";
import { openGrantDrawer } from "./grant-drawer.js";

let headEl, statsEl, tableEl;
let dags = null;
const expanded = new Set();

const actions = () => state.options.jobs.actions;

function renderHead() {
  const airflowUrl = serviceUrl("Airflow");
  render(headEl, pageHeader({
    eyebrow: "Explore · Airflow",
    title: "Jobs",
    lede: html`Which DAGs <strong>${currentUser().label}</strong> can view, trigger, and inspect. Airflow enforces the same answers natively through <code>opa_auth_manager</code>.`,
    actions: html`
      ${airflowUrl ? html`<a class="btn btn-ghost" href="${airflowUrl}" target="_blank" rel="noopener">${icon("external")}Open Airflow</a>` : ""}
      <button type="button" class="btn btn-secondary" data-grant>${icon("plus")}Grant access</button>`,
  }));
}

function renderStats() {
  const count = (a) => dags.filter((d) => d.permissions[a]).length;
  render(statsEl, html`
    <div class="stat-strip">
      <div class="stat"><span class="stat-value">${count("view")}<small>/${dags.length}</small></span><span class="stat-label">DAGs visible</span></div>
      <div class="stat"><span class="stat-value">${count("trigger")}</span><span class="stat-label">can trigger</span></div>
      <div class="stat"><span class="stat-value">${count("view_logs")}</span><span class="stat-label">can read logs</span></div>
      <div class="stat-note">${callout(html`This page never calls Airflow. Each mark is the backend asking OPA <code>data.app.allow</code> for <code>airflow.dag.&lt;id&gt;</code>. Log in to the Airflow UI as the same user to see Airflow enforce it.`)}</div>
    </div>`);
}

const permMark = (allowed, action) =>
  html`<span class="perm ${allowed ? "is-allow" : "is-deny"}" aria-label="${action}: ${allowed ? "allowed" : "denied"}">${icon(allowed ? "check" : "x")}</span>`;

function renderTable() {
  if (!dags) return render(tableEl, skeleton(6));
  const acts = actions();
  const rows = dags.map((dag) => {
    const open = expanded.has(dag.id);
    return html`
      <tr class="dag-row${open ? " is-open" : ""}" data-expand="${dag.id}" tabindex="0" aria-expanded="${String(open)}">
        <td>
          <div class="dag-name">${icon("flow")}<div><strong>${dag.label}</strong><code>${dag.id}</code></div></div>
          <p class="dag-desc">${dag.description}</p>
        </td>
        <td>${orgBadge(dag.org)}</td>
        ${acts.map((a) => html`<td class="perm-cell">${permMark(dag.permissions[a], a)}</td>`)}
        <td class="caret-cell">${icon(open ? "chevron-down" : "chevron-right")}</td>
      </tr>
      ${open
        ? html`<tr class="dag-detail"><td colspan="${acts.length + 3}"><ul class="why-list">${acts.map(
            (a) => html`<li>${decision(dag.permissions[a], { allow: a, deny: a })}<span>${explainHtml(explain(state.userId, dag.resource, a))}</span></li>`
          )}</ul></td></tr>`
        : ""}`;
  });
  render(tableEl, html`
    <div class="pane grid-wrap"><table class="grid grid-jobs">
      <thead><tr><th>DAG</th><th>Organization</th>${acts.map((a) => html`<th class="perm-cell"><code>${a}</code></th>`)}<th class="caret-cell"><span class="visually-hidden">Details</span></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`);
}

function toggle(row) {
  const id = row.dataset.expand;
  expanded.has(id) ? expanded.delete(id) : expanded.add(id);
  renderTable();
  tableEl.querySelector(`[data-expand="${id}"]`)?.focus();
}

export default {
  mount(root) {
    render(root, html`<div data-head></div><div data-stats></div><div data-table></div>`);
    headEl = root.querySelector("[data-head]");
    statsEl = root.querySelector("[data-stats]");
    tableEl = root.querySelector("[data-table]");
    root.addEventListener("click", (e) => {
      if (e.target.closest("[data-grant]")) return openGrantDrawer({ kind: "jobs" });
      const row = e.target.closest("[data-expand]");
      if (row) toggle(row);
    });
    root.addEventListener("keydown", (e) => {
      const row = e.target.closest?.("[data-expand]");
      if (row && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        toggle(row);
      }
    });
  },
  async refresh() {
    renderHead();
    renderTable();
    const user = state.userId;
    let next;
    try {
      next = await api(withQuery("/api/airflow/dags", { user }));
    } catch (err) {
      render(statsEl, "");
      return render(tableEl, errorState(err));
    }
    if (user !== state.userId) return; // user switched mid-flight; the newer refresh wins
    dags = next;
    renderStats();
    renderTable();
  },
};
