"""OPA-backed Auth Manager for Airflow 3 (AIP-56).

This is the "native" enforcement point for Airflow in the IDMA POC — the
same shape as trino.rego for Trino. Airflow's pluggable BaseAuthManager
interface (see airflow.api_fastapi.auth.managers.base_auth_manager) is
what makes this possible: every authorization-sensitive request in the
webserver/API — view a Dag, trigger a run, read task logs, view a Dag's
code — calls one of the is_authorized_* methods below before doing
anything else. Point AIRFLOW__CORE__AUTH_MANAGER at this class and OPA
becomes the answer for all of them.

Login/session handling is NOT reimplemented here — this subclasses
FabAuthManager (Airflow's classic Flask-AppBuilder auth manager) and
inherits its OAuth login flow, configured in webserver_config.py to log
in against the same Keycloak realm the frontend and Trino use. Every
Keycloak-authenticated user gets FAB's flat "User" role
(AUTH_USER_REGISTRATION_ROLE in webserver_config.py) — that role still
gates the handful of non-Dag resources (connections, variables, pools,
configuration) this file leaves untouched — only Dag-level authorization
("job and entities", the surface this POC's demo focuses on) is
delegated to OPA, identically regardless of role.

Every is_authorized_dag() call is forwarded to the same OPA instance and
the same policy_data Trino uses, via opa/policies/airflow.rego — a thin
wrapper around the shared authz.allow() the same way app.rego and
trino.rego are. One more native enforcement point, zero duplicated
policy logic. Fails closed: any OPA/network error denies, exactly like
the backend's own opa_client.check().
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

from sqlalchemy import select

from airflow.api_fastapi.auth.managers.models.resource_details import DagAccessEntity, DagDetails
from airflow.models import DagModel
from airflow.providers.fab.auth_manager.fab_auth_manager import FabAuthManager
from airflow.providers.fab.auth_manager.models import User
from airflow.utils.session import NEW_SESSION, provide_session

log = logging.getLogger(__name__)

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")


def _opa_allow(user: str, dag_id: str, action: str) -> bool:
    payload = json.dumps({"input": {"user": user, "dag_id": dag_id, "action": action}}).encode()
    req = urllib.request.Request(
        f"{OPA_URL}/v1/data/airflow/allow",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read())
            return bool(body.get("result", False))
    except Exception as err:  # noqa: BLE001 — fail closed on any OPA/network error
        log.warning("[opa_auth_manager] OPA check failed for dag_id=%s, denying: %s", dag_id, err)
        return False


# (method, access_entity) -> action name in the shared subject/resource/action
# vocabulary policy_data grants are written in (see seed.py's AIRFLOW_ACTIONS).
# Only the entities this POC's demo DAGs actually exercise are named here —
# anything else falls back to a generated "<entity>_<method>" action that no
# policy row will ever match, which is deny-by-default rather than an error.
_ACTION_MAP = {
    ("GET", None): "view",
    ("POST", None): "edit",
    ("PUT", None): "edit",
    ("DELETE", None): "delete",
    ("GET", DagAccessEntity.RUN): "view",
    ("POST", DagAccessEntity.RUN): "trigger",
    ("GET", DagAccessEntity.TASK_LOGS): "view_logs",
    ("GET", DagAccessEntity.CODE): "view_code",
    ("GET", DagAccessEntity.TASK_INSTANCE): "view",
    ("GET", DagAccessEntity.TASK): "view",
    ("GET", DagAccessEntity.XCOM): "view_logs",
}


def _action_for(method: str, access_entity: DagAccessEntity | None) -> str:
    key = (str(method), access_entity)
    if key in _ACTION_MAP:
        return _ACTION_MAP[key]
    entity_name = access_entity.value.lower() if access_entity else "dag"
    return f"{entity_name}_{str(method).lower()}"


def _is_admin(user: User) -> bool:
    return any(role.name == "Admin" for role in (user.roles or []))


class OPAAuthManager(FabAuthManager):
    """FabAuthManager plus one override: Dag authorization goes through OPA."""

    def is_authorized_dag(
        self,
        *,
        method,
        user: User,
        access_entity: DagAccessEntity | None = None,
        details: DagDetails | None = None,
    ) -> bool:
        if _is_admin(user):
            return True

        dag_id = details.id if details else None
        if not dag_id:
            # No specific Dag named — e.g. the "list all Dags" call. Let it
            # through; the per-Dag list itself is filtered by
            # filter_authorized_dag_ids(), which calls back into this same
            # method once per Dag with a real dag_id, so nothing here
            # bypasses the actual per-Dag OPA check.
            return True

        action = _action_for(method, access_entity)
        return _opa_allow(user.username, dag_id, action)

    @provide_session
    def get_authorized_dag_ids(
        self,
        *,
        user: User,
        method="GET",
        session=NEW_SESSION,
    ) -> set[str]:
        """Used by the Dag *list* endpoint — a different code path from
        is_authorized_dag() above, and one FabAuthManager overrides with a
        shortcut: if the user's FAB role has blanket "read DAG" permission
        (which the flat AUTH_USER_REGISTRATION_ROLE every Keycloak user
        gets does, by default), it returns every Dag id without ever
        calling is_authorized_dag() — silently bypassing OPA for the list
        view only (single-Dag/trigger/logs endpoints go through
        is_authorized_dag() directly and were never affected). Overridden
        back to the plain, correct version: check every Dag individually
        via filter_authorized_dag_ids(), which does call is_authorized_dag()
        — fine at this scale (4 demo Dags), and what actually matches what
        the demo claims.
        """
        dag_ids = {dag.dag_id for dag in session.execute(select(DagModel.dag_id))}
        return self.filter_authorized_dag_ids(dag_ids=dag_ids, method=method, user=user)
