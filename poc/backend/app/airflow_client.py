"""The backend's connection to the Airflow apiserver's REST API (v2) —
used only by the Usecase wizard to trigger and poll `automl_wizard_pipeline`
runs. Plain functions over urllib, same shape as opa_client.py/
mlflow_client.py, no extra HTTP dependency.

Authenticates as a dedicated local FAB user (svc-backend, created by
docker-compose's airflow-init / k8s's idma-airflow-init Job) rather than
forwarding the caller's own Keycloak bearer token: Airflow's REST API
mints its own JWT via POST /auth/token against FAB's local user store,
which is a different credential space from the Keycloak OAuth login the
browser uses to reach the Airflow *webserver* (see webserver_config.py)
— an OAuth-only Keycloak account has no usable local password for this
endpoint to check. The service account carries the Admin role, which
OPAAuthManager's is_authorized_dag() lets through unconditionally (see
auth_manager.py's _is_admin) — deliberate, same reasoning
automl_wizard_pipeline.py's own docstring gives for why its data read
bypasses OPA: a Dag run has no logged-in end-user for OPA to evaluate a
grant against. The real per-user "can this person trigger this Dag"
check happens in main.py before this module is ever called, against the
same airflow.dag.automl_wizard_pipeline resource OPAAuthManager itself
would check natively.
"""
import json
import os
import time
import urllib.error
import urllib.request

AIRFLOW_API_URL = os.environ.get("AIRFLOW_API_URL", "http://airflow-apiserver:8080")
AIRFLOW_API_USERNAME = os.environ.get("AIRFLOW_API_USERNAME", "svc-backend")
AIRFLOW_API_PASSWORD = os.environ.get("AIRFLOW_API_PASSWORD", "poc-airflow-api-password")

AUTOML_DAG_ID = "automl_wizard_pipeline"

# Cached JWT — Airflow mints these with a 24h expiry (see
# api_fastapi/auth/tokens.py), comfortably longer than this process's own
# cache margin below, so one token is reused across many requests instead
# of re-authenticating every call.
_token: str | None = None
_token_fetched_at: float = 0.0
_TOKEN_TTL_S = 20 * 60  # re-fetch well before Airflow's own ~24h expiry


class AirflowClientError(Exception):
    pass


def _request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{AIRFLOW_API_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        raise AirflowClientError(f"{method} {path} -> HTTP {err.code}: {detail}") from err
    except urllib.error.URLError as err:
        raise AirflowClientError(f"{method} {path} failed: {err}") from err


def _get_token(force_refresh: bool = False) -> str:
    global _token, _token_fetched_at
    if not force_refresh and _token and (time.monotonic() - _token_fetched_at) < _TOKEN_TTL_S:
        return _token

    payload = json.dumps({"username": AIRFLOW_API_USERNAME, "password": AIRFLOW_API_PASSWORD}).encode()
    req = urllib.request.Request(
        f"{AIRFLOW_API_URL}/auth/token",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as err:
        raise AirflowClientError(f"couldn't authenticate to Airflow apiserver: {err}") from err

    _token = body["access_token"]
    _token_fetched_at = time.monotonic()
    return _token


def _request_with_auth(method: str, path: str, body: dict | None = None) -> dict:
    """Retries once with a forced token refresh on a 401 — covers both a
    stale cached token and the apiserver having restarted with a fresh
    AIRFLOW__API_AUTH__JWT_SECRET (dev-only risk; see docker-compose.yml's
    comment on that setting being fixed)."""
    token = _get_token()
    try:
        return _request(method, path, token, body)
    except AirflowClientError as err:
        if "HTTP 401" not in str(err):
            raise
        token = _get_token(force_refresh=True)
        return _request(method, path, token, body)


def _ensure_unpaused(dag_id: str) -> None:
    """A manually-triggered run of a *paused* Dag is created but never
    picked up by the scheduler — it sits in "queued" forever (verified
    empirically against this stack; not the older-Airflow assumption that
    pause only blocks automatic scheduling). Every demo Dag here starts
    paused (AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=true) and nothing
    else in this POC unpauses automl_wizard_pipeline, so the trigger path
    does it itself rather than silently hanging."""
    token = _get_token()
    dag = _request("GET", f"/api/v2/dags/{dag_id}", token)
    if dag.get("is_paused"):
        _request("PATCH", f"/api/v2/dags/{dag_id}", token, {"is_paused": False})


def trigger_automl_run(conf: dict) -> dict:
    """Starts one automl_wizard_pipeline run with the given Param
    overrides (see that Dag's own `params=` for the accepted keys) and
    returns the fresh DagRun — most importantly its `dag_run_id`, which
    the Usecase wizard polls via get_run_status() below."""
    _ensure_unpaused(AUTOML_DAG_ID)
    return _request_with_auth(
        "POST", f"/api/v2/dags/{AUTOML_DAG_ID}/dagRuns", {"logical_date": None, "conf": conf}
    )


def get_run_status(dag_run_id: str) -> dict:
    """The DagRun's current `state` (queued/running/success/failed/...)
    plus its own echoed `conf`. Cheap enough to poll from the frontend
    every couple of seconds — this is a demo-scale single-tenant Dag."""
    return _request_with_auth("GET", f"/api/v2/dags/{AUTOML_DAG_ID}/dagRuns/{dag_run_id}")


def get_run_result(dag_run_id: str, task_id: str = "ket_qua") -> dict | None:
    """A task's full return value via its `return_value` XCom — default
    `ket_qua` (registered model name/version, score, per-class/regression
    metrics). None if that task hasn't produced one yet (still running,
    or the run failed/was skipped before reaching it); callers should
    only call this once get_run_status() reports state == "success",
    except for `luu_tru_dac_trung` — a parallel branch off step 5, not a
    dependency of `ket_qua`, so main.py polls it independently rather
    than gating it on the overall run's state (see wizard_run_status)."""
    try:
        entry = _request_with_auth(
            "GET",
            f"/api/v2/dags/{AUTOML_DAG_ID}/dagRuns/{dag_run_id}/taskInstances/{task_id}/xcomEntries/return_value",
        )
    except AirflowClientError as err:
        if "HTTP 404" in str(err):
            return None
        raise
    return entry.get("value")
