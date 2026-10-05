// Shared app state: the demo identities, the acting user, and the policy
// snapshot from the authz database (the same document the backend pushes
// into OPA). Views subscribe to be told when the user or policies change.
import { api } from "./api-client.js";

const USER_KEY = "ihub-acting-user";

export const state = {
  users: [],
  orgs: {}, // org id -> label
  userId: null,
  policies: null, // { p, g, g2, resourceOrg }
  options: null, // { data: /api/policy-options, jobs: /api/airflow/policy-options }
};

const listeners = new Set();
export const subscribe = (fn) => listeners.add(fn);
const emit = (reason) => listeners.forEach((fn) => fn(reason));

function readStoredUser() {
  try {
    return localStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}

export async function bootstrap() {
  const [users, orgs, data, jobs] = await Promise.all([
    api("/api/users"),
    api("/api/organizations"),
    api("/api/policy-options"),
    api("/api/airflow/policy-options"),
  ]);
  state.users = users;
  state.orgs = Object.fromEntries(orgs.map((o) => [o.id, o.label]));
  state.options = { data, jobs };
  const stored = readStoredUser();
  state.userId = users.some((u) => u.id === stored) ? stored : users[0]?.id;
  await loadPolicies();
}

export async function loadPolicies() {
  const raw = await api("/api/policies");
  state.policies = { p: raw.p, g: raw.g, g2: raw.g2, resourceOrg: Object.fromEntries(raw.resource_org) };
  return state.policies;
}

export function setUser(id) {
  state.userId = id;
  try {
    localStorage.setItem(USER_KEY, id);
  } catch {
    /* private mode — the choice just won't survive a reload */
  }
  emit("user");
}

// kind: "grant" | "revoke". The backend writes Postgres, then pushes the
// fresh snapshot into OPA inside the same request, so a reload right after
// this resolves already reflects the change everywhere.
export async function changePolicy(kind, { sub, obj, act }) {
  const res = await api(`/api/policies/${kind}`, { method: "POST", body: { sub, obj, act } });
  await loadPolicies();
  emit("policies");
  return res;
}

// ---- lookups over the policy snapshot --------------------------------
export const currentUser = () => state.users.find((u) => u.id === state.userId);
export const userById = (id) => state.users.find((u) => u.id === id);
export const orgLabel = (org) => state.orgs[org] || org;
export const isGroup = (sub) => !state.users.some((u) => u.id === sub);
export const groupsOf = (user) => state.policies.g.filter(([m]) => m === user).map(([, grp]) => grp);
export const membersOf = (group) => state.policies.g.filter(([, grp]) => grp === group).map(([m]) => m);
export const parentsOf = (resource) => state.policies.g2.filter(([c]) => c === resource).map(([, p]) => p);

// Same lookup order as authz.rego's org_of(): the resource's own tag, else
// its parent's (a table inherits its schema's org). null = untagged.
export function orgOf(resource) {
  const { resourceOrg } = state.policies;
  if (resourceOrg[resource]) return resourceOrg[resource];
  return parentsOf(resource).map((p) => resourceOrg[p]).find(Boolean) || null;
}
