// Display-only mirror of opa/policies/authz.rego, used to explain *why*
// OPA answered the way it did. The decision shown on screen always comes
// from OPA (via the backend); this only annotates it. If the two ever
// disagree, OPA's copy of policy_data is stale — the UI says so.
//
//   allow(user, resource, action) if some p in policies:
//     subject_matches(user, p.sub)      user itself, or a group it's in
//     resource_matches(resource, p.obj) resource itself, or its parent
//     action == p.act
//     same_org(user, resource)          independent of the grant
import { html } from "./dom.js";
import { icon } from "./icons.js";
import { groupsOf, isGroup, membersOf, orgOf, parentsOf, state, userById } from "./state.js";
import { policyTuple, principalChip, resourceChip } from "./ui-parts.js";

export function explain(userId, resource, action) {
  const userOrg = userById(userId)?.org || null;
  const groups = groupsOf(userId);
  const parents = parentsOf(resource);
  const grants = state.policies.p
    .filter(([sub, obj, act]) => act === action && (sub === userId || groups.includes(sub)) && (obj === resource || parents.includes(obj)))
    .map(([sub, obj, act]) => ({ sub, obj, act, viaGroup: sub !== userId, inherited: obj !== resource }));
  const resourceOrg = orgOf(resource);
  let reason = "granted";
  if (!grants.length) reason = "no-grant";
  else if (!resourceOrg) reason = "untagged";
  else if (resourceOrg !== userOrg) reason = "org-mismatch";
  return { userId, resource, action, userOrg, resourceOrg, groups, parents, grants, reason, allowed: reason === "granted" };
}

const codeList = (items) => items.map((x, i) => html`${i ? ", " : ""}<code>${x}</code>`);

export function explainHtml(e) {
  const g = e.grants[0];
  const more = e.grants.length > 1 ? ` (+${e.grants.length - 1} more)` : "";
  switch (e.reason) {
    case "granted":
      return html`Granted by ${policyTuple(g.sub, g.obj, g.act)}${more}${
        g.viaGroup ? html` — <code>${e.userId}</code> is a member of <code>${g.sub}</code>` : ""
      }${g.inherited ? html`; <code>${e.resource}</code> inherits from <code>${g.obj}</code>` : ""}. Both sit in <code>${e.resourceOrg}</code>.`;
    case "org-mismatch":
      return html`${policyTuple(g.sub, g.obj, g.act)} matches, but <code>${e.resource}</code> belongs to <code>${
        e.resourceOrg
      }</code> and <code>${e.userId}</code> to <code>${e.userOrg || "no org"}</code>. <code>same_org()</code> denies it regardless of the grant.`;
    case "untagged":
      return html`<code>${e.resource}</code> has no organization tag, so <code>same_org()</code> can't hold. Denied (fail closed).`;
    default:
      return html`No policy grants <code>${e.action}</code> on <code>${e.resource}</code>${
        e.parents.length ? html` or its parent ${codeList(e.parents)}` : ""
      } to <code>${e.userId}</code>${e.groups.length ? html` or ${codeList(e.groups)}` : ""}. Default deny.`;
  }
}

// Every policy row that reaches `resource` (directly or through its parent),
// with the members it actually takes effect for once same_org() applies.
export function whoHasAccess(resource, action) {
  const parents = parentsOf(resource);
  const resourceOrg = orgOf(resource);
  return state.policies.p
    .filter(([, obj, act]) => act === action && (obj === resource || parents.includes(obj)))
    .map(([sub, obj]) => ({
      sub,
      obj,
      inherited: obj !== resource,
      members: (isGroup(sub) ? membersOf(sub) : [sub]).map((id) => {
        const org = userById(id)?.org || null;
        return { id, org, effective: Boolean(resourceOrg) && org === resourceOrg };
      }),
    }));
}

export const memberChip = (m) =>
  html`<span class="member ${m.effective ? "is-allow" : "is-deny"}" title="${
    m.effective ? `${m.id} · same org — applies` : `${m.id} · ${m.org || "no org"} — blocked by same_org()`
  }">${icon(m.effective ? "check" : "x")}${m.id}<small>${m.org || "?"}</small></span>`;

export function whoHasAccessHtml(resource, action) {
  const rows = whoHasAccess(resource, action);
  if (!rows.length) {
    return html`<p class="muted-text">No policy grants <code>${action}</code> on <code>${resource}</code> or its parent. Nobody can read it.</p>`;
  }
  return html`
    <div class="grid-wrap"><table class="list-table">
      <thead><tr><th>Principal</th><th>Granted on</th><th>Takes effect for</th></tr></thead>
      <tbody>${rows.map(
        (r) => html`<tr>
          <td>${principalChip(r.sub)}</td>
          <td>${resourceChip(r.obj)}${r.inherited ? html` <span class="tag">inherited</span>` : ""}</td>
          <td><div class="member-list">${r.members.length ? r.members.map(memberChip) : html`<span class="muted">no members</span>`}</div></td>
        </tr>`
      )}</tbody>
    </table></div>`;
}
