// Read-only panels of the Access policies page: groups (g), the resource
// tree (g2) and organization tags — the non-grant parts of policy_data.
import { html } from "../dom.js";
import { icon } from "../icons.js";
import { membersOf, orgLabel, state, userById } from "../state.js";
import { avatar, callout, orgBadge, resourceChip } from "../ui-parts.js";

export function groupsPanel() {
  const groups = [...new Set(state.policies.g.map(([, grp]) => grp))];
  return html`<div class="card-grid">${groups.map((grp) => {
    const members = membersOf(grp);
    const orgs = new Set(members.map((m) => userById(m)?.org).filter(Boolean));
    const grants = state.policies.p.filter(([sub]) => sub === grp);
    return html`
      <article class="pane group-card">
        <header class="group-card-head">
          <span class="group-icon">${icon("users")}</span>
          <div><h3>${grp}</h3><p class="muted small">${members.length} members · ${grants.length} grants</p></div>
        </header>
        <ul class="member-rows">${members.map((m) => {
          const u = userById(m);
          return html`<li>${avatar(u)}<span class="member-rows-text"><strong>${u?.label || m}</strong><code>${m}</code></span>${orgBadge(u?.org)}</li>`;
        })}</ul>
        ${orgs.size > 1
          ? callout(html`Members span ${orgs.size} organizations. Every grant to <code>${grp}</code> still stops at each member's own org — <code>same_org()</code> applies independently.`, "warn")
          : ""}
        <div class="group-grants">
          <p class="eyebrow">Grants</p>
          <div class="chip-row">${grants.map(([, obj, act]) => html`<span class="grant-chip">${resourceChip(obj)}<small>${act}</small></span>`)}</div>
        </div>
      </article>`;
  })}</div>`;
}

export function treePanel() {
  const byParent = new Map();
  state.policies.g2.forEach(([child, parent]) => byParent.set(parent, [...(byParent.get(parent) || []), child]));
  return html`
    ${callout(html`One hop deep: a table matches grants on its schema and inherits the schema's organization (<code>resource_matches</code> and <code>org_of</code> in <code>authz.rego</code>).`)}
    <div class="card-grid">${[...byParent].map(
      ([parent, children]) => html`
        <article class="pane tree-card">
          <header class="tree-card-head">${resourceChip(parent)}${orgBadge(state.policies.resourceOrg[parent])}</header>
          <ul class="tree-lines">${children.map((c) => html`<li>${resourceChip(c)}</li>`)}</ul>
        </article>`
    )}</div>`;
}

export function orgsPanel() {
  const orgIds = [...new Set([...Object.keys(state.orgs), ...Object.values(state.policies.resourceOrg)])];
  return html`
    ${callout(html`Every user and every root resource carries exactly one organization. <code>same_org()</code> denies on mismatch, and on a missing tag.`)}
    <div class="card-grid">${orgIds.map((org) => {
      const users = state.users.filter((u) => u.org === org);
      const resources = Object.entries(state.policies.resourceOrg).filter(([, o]) => o === org).map(([r]) => r);
      return html`
        <article class="pane org-card">
          <header class="org-card-head"><span class="group-icon">${icon("building")}</span><div><h3>${orgLabel(org)}</h3><code class="muted">${org}</code></div></header>
          <p class="eyebrow">Users</p>
          <div class="avatar-row">${users.map((u) => html`<span class="avatar-chip">${avatar(u, "avatar-sm")}${u.id}</span>`)}</div>
          <p class="eyebrow">Resources</p>
          <div class="chip-row">${resources.map(resourceChip)}</div>
        </article>`;
    })}</div>`;
}
