package airflow

# Airflow's native check, called by opa_auth_manager (see
# airflow/plugins/opa_auth_manager) on every is_authorized_dag() —
# viewing a Dag, triggering a run, reading its code or logs. Same shape
# as app.rego and trino.rego: a thin wrapper around the one shared
# authz.allow(), just reshaping Airflow's (user, dag_id, action) input
# into the (user, resource, action) triple the other two already use.
#
# Dag resources are named "airflow.dag.<dag_id>" in policy_data — see the
# grp_data_eng / grp_hr / bob grants in backend/app/seed.py. No separate
# policy engine, no duplicated matching logic.
#
# Input: {"user": "alice", "dag_id": "sales_etl", "action": "trigger"}

import data.authz
import future.keywords.if

default allow := false

allow if {
	authz.allow(input.user, sprintf("airflow.dag.%s", [input.dag_id]), input.action)
}
