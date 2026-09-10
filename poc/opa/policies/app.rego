package app

# The backend's own authorization check — same engine and same policy
# data as Trino's, just a plain (user, resource, action) input shape
# since we control both sides of this call.
#
# Input: {"user": "alice", "resource": "sales.customers", "action": "select"}

import data.authz
import future.keywords.if

default allow := false

allow if {
	authz.allow(input.user, input.resource, input.action)
}
