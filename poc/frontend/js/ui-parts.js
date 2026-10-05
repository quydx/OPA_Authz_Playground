// Small render helpers shared by every view: badges, chips, headers,
// empty/loading/error states. All return `html` fragments.
import { html } from "./dom.js";
import { icon } from "./icons.js";
import { isGroup, orgLabel } from "./state.js";

export const initials = (label = "?") =>
  label
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

export const avatar = (user, cls = "") =>
  html`<span class="avatar ${cls}" aria-hidden="true">${initials(user?.label || user?.id)}</span>`;

export function orgBadge(org) {
  if (!org) return html`<span class="badge badge-warn" title="No organization tag — same_org() denies">untagged</span>`;
  return html`<span class="badge badge-org" title="${orgLabel(org)}">${icon("building")}${org}</span>`;
}

export function decision(allowed, { allow = "Allow", deny = "Deny" } = {}) {
  return html`<span class="decision ${allowed ? "is-allow" : "is-deny"}">${icon(allowed ? "check" : "x")}${
    allowed ? allow : deny
  }</span>`;
}

export const resourceKind = (res) => (res.startsWith("airflow.dag.") ? "dag" : res.includes(".") ? "table" : "schema");
const KIND_ICON = { dag: "flow", table: "table", schema: "layers" };

export const resourceChip = (res) =>
  html`<span class="chip chip-resource" title="${resourceKind(res)}">${icon(KIND_ICON[resourceKind(res)])}<code>${res}</code></span>`;

export function principalChip(sub) {
  const group = isGroup(sub);
  return html`<span class="chip ${group ? "chip-group" : "chip-user"}" title="${group ? "group" : "user"}">${icon(
    group ? "users" : "user"
  )}<span>${sub}</span></span>`;
}

export const policyTuple = (sub, obj, act) =>
  html`<code class="tuple">p(<b>${sub}</b>, <b>${obj}</b>, <b>${act}</b>)</code>`;

export function pageHeader({ eyebrow, title, lede, actions }) {
  return html`
    <header class="page-head">
      <div class="page-head-text">
        ${eyebrow ? html`<p class="eyebrow">${eyebrow}</p>` : ""}
        <h1>${title}</h1>
        ${lede ? html`<p class="lede">${lede}</p>` : ""}
      </div>
      ${actions ? html`<div class="page-head-actions">${actions}</div>` : ""}
    </header>`;
}

export function emptyState({ iconName = "info", title, body, action, tone = "" }) {
  return html`
    <div class="empty ${tone}">
      <span class="empty-icon">${icon(iconName)}</span>
      <p class="empty-title">${title}</p>
      ${body ? html`<p class="empty-body">${body}</p>` : ""}
      ${action || ""}
    </div>`;
}

const SKELETON_WIDTHS = [92, 74, 86, 63, 80, 70];
export const skeleton = (lines = 4) =>
  html`<div class="skeleton-stack" aria-busy="true" aria-label="Loading">${Array.from(
    { length: lines },
    (_, i) => html`<span class="skeleton" style="--w:${SKELETON_WIDTHS[i % SKELETON_WIDTHS.length]}%"></span>`
  )}</div>`;

export const errorState = (err) =>
  emptyState({ iconName: "alert", title: "Couldn't load this", body: err.message, tone: "empty-error" });

export const callout = (content, tone = "info") =>
  html`<div class="callout callout-${tone}">${icon(tone === "warn" ? "alert" : "info")}<div>${content}</div></div>`;
