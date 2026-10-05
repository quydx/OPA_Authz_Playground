package app

# The backend's own authorization check — same engine and same policy
# data as Trino's, just a plain (user, resource, action) input shape
# since we control both sides of this call.
#
# Input: {"user": "alice", "resource": "sales.customers", "action": "select"}
# Query data.app for {allow, reason, user_org?, resource_org?}.

import data.authz
import future.keywords.if

default allow := false

allow if {
	authz.allow(input.user, input.resource, input.action)
}

# Why allow is what it is — computed here, by the engine that made the
# decision, so the backend never has to re-derive it. The else-chain is
# ordered: the first failing check is the reported one.
reason := "granted" if {
	allow
} else := "policy_data_missing" if {
	not data.policy_data # cold start: backend hasn't pushed yet
} else := "no_grant" if {
	not authz.has_grant(input.user, input.resource, input.action)
} else := "user_org_unknown" if {
	not authz.attributes[input.user].org
} else := "resource_org_unknown" if {
	not authz.org_of(input.resource)
} else := "org_mismatch"

# Both sides of the organization check, for the deny message. Each is
# simply absent from the response when unknown.
user_org := authz.attributes[input.user].org

resource_org := authz.org_of(input.resource)
