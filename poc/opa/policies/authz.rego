package authz

# Shared authorization core, consumed by both app.rego (the backend's own
# check) and trino.rego (Trino's native check) — one policy engine, two
# enforcement points, no duplicated logic between them.
#
# `data.policy_data` is not computed here — it's a plain document the
# backend pushes into OPA's store (PUT /v1/data/policy_data) after every
# write to the authz Postgres database, and once on every backend
# startup. Postgres stays the durable system of record; this is just its
# live mirror inside OPA. If it's missing or stale (OPA just restarted
# and the backend hasn't pushed yet), every lookup below is undefined,
# which both consumers treat as deny — fail closed, not fail open.

import future.keywords.if
import future.keywords.in

policies := data.policy_data.p          # [[sub, obj, act], ...]
groups := data.policy_data.g            # [[user, group], ...]
resource_tree := data.policy_data.g2    # [[child, parent], ...]
attributes := data.policy_data.attributes
resource_org := data.policy_data.resource_org  # {resource: org, ...} — schemas/dags only

# subject_matches: sub is the user itself, or a group the user belongs to.
subject_matches(user, sub) if user == sub

subject_matches(user, sub) if [user, sub] in groups

# resource_matches: obj is the resource itself, or its direct parent in
# the resource tree (table -> schema). Rego disallows unbounded
# self-recursion, so this is a bounded one-hop check rather than a
# general transitive closure — which is fine as long as the tree stays
# one level deep, exactly like the data it replaces (Casbin's g2 rules
# here were also ever only table -> schema, never deeper). A deeper tree
# would need either another explicit hop added below, or the backend
# pre-computing full ancestor chains before pushing policy_data, rather
# than Rego walking the tree itself.
resource_matches(resource, obj) if resource == obj

resource_matches(resource, obj) if {
	some edge in resource_tree
	edge[0] == resource
	edge[1] == obj
}

# org_of(resource): the organization a resource belongs to — looked up
# directly (schemas, Airflow Dags — the resource-tree roots) or via one
# hop up the resource tree (a table inherits its schema's org). Same
# bounded-one-hop shape as resource_matches, for the same reason: Rego
# doesn't allow unbounded self-recursion, and the tree is only ever one
# level deep here anyway.
org_of(resource) = org if {
	org := resource_org[resource]
}

org_of(resource) = org if {
	not resource_org[resource]
	some edge in resource_tree
	edge[0] == resource
	org := resource_org[edge[1]]
}

# same_org: independent of any grant below — a policy that matches
# subject and resource still isn't enough if the caller's organization
# isn't the resource's organization. Undefined (denied) if either side
# is unknown, same fail-closed shape as everything else in this file.
same_org(user, resource) if {
	org_of(resource) == attributes[user].org
}

# allow(user, resource, action): true if some policy grants a matching
# subject a matching resource for this action, AND the caller's
# organization is the resource's organization. Same shape as the old
# Casbin matcher: g(sub) && g2(obj) && act == p.act — with organization
# layered on as its own independent guarantee, the same way Trino's row
# filters and column masks apply independently of the table grant.
allow(user, resource, action) if {
	has_grant(user, resource, action)
	same_org(user, resource)
}

# has_grant: the grant half of allow() on its own, without the org check —
# split out so app.rego can tell "no grant at all" apart from "granted, but
# blocked by the organization boundary" when explaining a deny.
has_grant(user, resource, action) if {
	some p in policies
	subject_matches(user, p[0])
	resource_matches(resource, p[1])
	action == p[2]
}
