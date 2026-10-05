// Renderers for the Catalog page's right-hand pane: the schema overview,
// a table's header + tabs, its sample data (with the enforcement trace),
// and its Access tab (why this user can/can't read it, and who can).
import { html } from "../dom.js";
import { icon } from "../icons.js";
import { currentUser, state } from "../state.js";
import { decision, emptyState, orgBadge } from "../ui-parts.js";
import { explain, explainHtml, whoHasAccessHtml } from "../access-explainer.js";

const TRACE_ICON = { allow: "check", deny: "x", skip: "minus", error: "alert" };

function trace(steps) {
  return html`<ol class="trace" aria-label="Enforcement path">${steps.map(
    (s, i) => html`
      <li class="trace-step is-${s.state}">
        <span class="trace-marker">${icon(TRACE_ICON[s.state])}</span>
        <span class="trace-text">
          <span class="trace-label">${i + 1}. ${s.label}</span>
          <code>${s.rule}</code>
          <span class="trace-note">${s.note}</span>
        </span>
      </li>`
  )}</ol>`;
}

export function renderOverview(tree) {
  return html`
    <div class="overview">
      <ul class="schema-list">${(tree || []).map((schema) => {
        const readable = schema.tables.filter((t) => t.allowed).length;
        return html`
          <li class="schema-row">
            <div class="schema-row-head">
              <span class="schema-row-icon">${icon("layers")}</span>
              <strong>${schema.name}</strong>
              ${orgBadge(schema.org)}
              <span class="schema-row-meta">${readable} of ${schema.tables.length} tables readable</span>
            </div>
            <div class="schema-row-tables">${schema.tables.map(
              (t) => html`<button type="button" class="table-pill${t.allowed ? "" : " is-denied"}" data-select="${t.resource}">
                ${icon(t.allowed ? "table" : "lock")}${t.name}</button>`
            )}</div>
          </li>`;
      })}</ul>
      <div class="how-list">
        <p class="eyebrow">How a read is checked</p>
        <ol>
          <li><strong>Backend</strong> asks OPA <code>data.app.allow</code>. A deny stops here — Trino never sees the query.</li>
          <li><strong>Trino</strong> asks OPA <code>data.trino.allow</code> for the table itself, so connecting to Trino directly doesn't skip the check.</li>
          <li><strong>Trino</strong> then applies <code>rowFilters</code> and <code>columnMask</code> from <code>trino.rego</code> at planning time.</li>
        </ol>
      </div>
    </div>`;
}

const tab = (id, label, active) =>
  html`<button type="button" role="tab" class="tab${id === active ? " is-active" : ""}" aria-selected="${String(id === active)}" data-tab="${id}">${label}</button>`;

export function renderTableHeader(schema, table, activeTab) {
  return html`
    <div class="detail-head">
      <nav class="detail-crumbs" aria-label="Breadcrumb">
        <button type="button" class="link-btn" data-back>postgres</button>${icon("chevron-right")}
        <span>${schema.name}</span>${icon("chevron-right")}<span aria-current="page">${table.name}</span>
      </nav>
      <div class="detail-title">
        <span class="detail-icon">${icon("table")}</span>
        <h2>${table.name}</h2>
        ${decision(table.allowed, { allow: "Readable", deny: "No access" })}
        ${orgBadge(schema.org)}
      </div>
      <div class="tabs" role="tablist">${tab("data", "Sample data", activeTab)}${tab("access", "Access", activeTab)}</div>
    </div>`;
}

const isNumeric = (v) => typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v));
const cell = (v, numeric) =>
  v === null
    ? html`<td class="is-null${numeric ? " num" : ""}" title="NULL — may be a Trino column mask">NULL</td>`
    : html`<td class="${numeric ? "num" : ""}">${typeof v === "boolean" ? String(v) : v}</td>`;

function dataGrid(columns, rows) {
  if (!rows.length) {
    return emptyState({ iconName: "table", title: "No rows returned", body: "The grant let the query through, but a row filter may have excluded every row for this user." });
  }
  // A column is right-aligned when every non-NULL value in it is numeric,
  // so the header lines up with its values.
  const numeric = columns.map((_, c) => rows.every((r) => r[c] === null || isNumeric(r[c])) && rows.some((r) => r[c] !== null));
  return html`<div class="grid-wrap grid-wrap-data"><table class="grid">
    <thead><tr><th class="rownum">#</th>${columns.map((c, i) => html`<th class="${numeric[i] ? "num" : ""}">${c}</th>`)}</tr></thead>
    <tbody>${rows.map((row, i) => html`<tr><td class="rownum">${i + 1}</td>${row.map((v, c) => cell(v, numeric[c]))}</tr>`)}</tbody>
  </table></div>`;
}

export function renderDataTab(table, { result, error }) {
  const user = state.userId;
  if (error) {
    const trinoDenied = /access denied|permission_denied/i.test(error.message);
    return html`
      ${trace([
        { label: "Backend", rule: "data.app.allow", state: "allow", note: "allowed" },
        { label: "Trino", rule: "data.trino.allow", state: trinoDenied ? "deny" : "error", note: trinoDenied ? "denied by Trino's own OPA check" : "query failed" },
      ])}
      <div class="callout callout-deny">${icon("alert")}<div><strong>${trinoDenied ? "Trino refused the query" : "Trino returned an error"}</strong><p class="mono-wrap">${error.message}</p></div></div>`;
  }
  if (!result.allowed) {
    return html`
      ${trace([
        { label: "Backend", rule: "data.app.allow", state: "deny", note: "denied — the request stops here" },
        { label: "Trino", rule: "data.trino.allow", state: "skip", note: "never reached" },
      ])}
      ${emptyState({
        iconName: "lock",
        title: `${user} can't read ${table.resource}`,
        body: explainHtml(explain(user, table.resource, "select")),
        action: html`<button type="button" class="btn btn-secondary" data-grant="${table.resource}">${icon("plus")}Grant access</button>`,
      })}`;
  }
  const [schemaName, tableName] = table.resource.split(".");
  return html`
    ${trace([
      { label: "Backend", rule: "data.app.allow", state: "allow", note: "allowed" },
      { label: "Trino", rule: "trino.allow · rowFilters · columnMask", state: "allow", note: "allowed — filters and masks applied where defined" },
    ])}
    ${dataGrid(result.columns, result.rows)}
    <p class="grid-foot">
      <span>${result.rows.length} ${result.rows.length === 1 ? "row" : "rows"}</span>
      <code>SELECT * FROM "${schemaName}"."${tableName}" LIMIT 25</code>
      <span>as <code>user=${user}</code></span>
    </p>`;
}

export function renderAccessTab(table) {
  const user = currentUser();
  const e = explain(user.id, table.resource, "select");
  return html`
    <section class="detail-section">
      <h3>Your access <span class="muted">as ${user.label}</span></h3>
      <div class="verdict">
        ${decision(table.allowed, { allow: "OPA: allow", deny: "OPA: deny" })}
        <p class="verdict-text">${explainHtml(e)}</p>
      </div>
      ${e.allowed !== table.allowed
        ? html`<div class="callout callout-warn">${icon("alert")}<div>OPA's answer differs from this explanation, which the browser derives from <code>/api/policies</code>. OPA's copy of <code>policy_data</code> is probably stale — restart the backend to re-push it.</div></div>`
        : ""}
    </section>
    <section class="detail-section">
      <div class="section-head">
        <h3>Who can read this table</h3>
        <button type="button" class="btn btn-secondary btn-sm" data-grant="${table.resource}">${icon("plus")}Grant access</button>
      </div>
      ${whoHasAccessHtml(table.resource, "select")}
    </section>
    <section class="detail-section">
      <h3>Query-time rules</h3>
      <p class="muted-text">Trino evaluates <code>trino.rego</code> while planning the query. Row filters and column masks defined there apply on top of any grant, using the caller's attributes from <code>user_attributes</code>:</p>
      <dl class="kv-inline">
        <div><dt>region</dt><dd>${user.region ?? "—"}</dd></div>
        <div><dt>hr</dt><dd>${user.hr === undefined ? "—" : String(user.hr)}</dd></div>
      </dl>
    </section>`;
}
