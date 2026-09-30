"""The backend's connection to MLflow — a tracking-server *client* only,
same shape as storage_client.py's relationship to MinIO: plain functions,
no class, one env var (MLFLOW_TRACKING_URI) is the only thing that needs
to be right. This module never runs `mlflow server` itself (see
mlflow/Dockerfile for that) and never talks to MinIO directly — artifacts
are proxied through the tracking server (its --serve-artifacts mode), so
this backend only ever needs that one HTTP URL.

"Deployed" means the model version carries the registry alias
"production" — MLflow's modern replacement for the deprecated
stage-based API (transition_model_version_stage), which is being removed
in a future MLflow major version. The alias is the durable fact, stored
in MLflow itself; _DEPLOYED below is just a hot in-memory copy of
whatever it currently points to, loaded lazily on deploy and reloaded in
full by load_deployed_models() on backend startup — the same "no cache
survives a restart on faith alone" spirit as opa_client.push_policy_data.
"""
import os
import time

import mlflow
import mlflow.pyfunc
import numpy as np
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
PRODUCTION_ALIAS = "production"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
_client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)

# model name -> {"version": str, "model": loaded pyfunc model}
_DEPLOYED: dict[str, dict] = {}


def list_models() -> list[dict]:
    """Every registered model and every version of it, newest first — the
    Models tab's one and only fetch. Metrics/params come from each
    version's own training run, not stored redundantly anywhere else."""
    out = []
    for rm in _client.search_registered_models():
        deployed_version = rm.aliases.get(PRODUCTION_ALIAS)
        versions = []
        for mv in sorted(_client.search_model_versions(f"name='{rm.name}'"), key=lambda v: int(v.version), reverse=True):
            try:
                run = _client.get_run(mv.run_id)
                metrics, params = run.data.metrics, run.data.params
            except MlflowException:
                metrics, params = {}, {}
            versions.append(
                {
                    "version": mv.version,
                    "run_id": mv.run_id,
                    "status": mv.status,
                    "created_at": mv.creation_timestamp,
                    "deployed": mv.version == deployed_version,
                    "metrics": metrics,
                    "params": params,
                }
            )
        out.append({"name": rm.name, "deployed_version": deployed_version, "versions": versions})
    return out


def deploy_model_version(name: str, version: str) -> dict:
    """Points the "production" alias at this version and eagerly loads it
    into the in-memory cache, so the very next /invoke call doesn't pay a
    cold-load penalty (or worse, 404 until someone happens to call it)."""
    _client.set_registered_model_alias(name=name, alias=PRODUCTION_ALIAS, version=version)
    model = mlflow.pyfunc.load_model(f"models:/{name}/{version}")
    _DEPLOYED[name] = {"version": version, "model": model}
    return {"name": name, "version": version}


def load_deployed_models(retries: int = 20, delay: float = 2.0) -> None:
    """Reloads every model currently carrying the "production" alias into
    the in-memory cache — called once on backend startup (in a background
    thread, same pattern as trino_client.wait_for_trino) so a restart
    doesn't silently break /invoke until someone happens to re-deploy.
    Retries because this backend can start before the mlflow container is
    actually accepting connections."""
    registered_models = None
    for attempt in range(1, retries + 1):
        try:
            registered_models = _client.search_registered_models()
            print(f"[startup] mlflow reachable after {attempt} attempt(s)")
            break
        except Exception as err:  # noqa: BLE001 — anything means "not ready yet"
            print(f"[startup] mlflow not ready yet (attempt {attempt}/{retries}): {err}")
            time.sleep(delay)
    if registered_models is None:
        print("[startup] gave up waiting for mlflow — deployed models won't be preloaded")
        return
    for rm in registered_models:
        version = rm.aliases.get(PRODUCTION_ALIAS)
        if not version:
            continue
        try:
            model = mlflow.pyfunc.load_model(f"models:/{rm.name}/{version}")
            _DEPLOYED[rm.name] = {"version": version, "model": model}
            print(f"[startup] reloaded deployed model '{rm.name}' v{version}")
        except MlflowException as err:
            print(f"[startup] failed to reload deployed model '{rm.name}' v{version}: {err}")


def get_deployed_version(name: str) -> str | None:
    deployed = _DEPLOYED.get(name)
    return deployed["version"] if deployed else None


def invoke(name: str, rows: list[list[float]]) -> list:
    """Runs inference against whatever version of `name` is currently
    deployed. `rows` is a plain list of feature vectors (JSON-friendly) —
    converted to a numpy array here because the model's auto-inferred
    signature (from the training Dag's input_example) is tensor-based and
    rejects a raw Python list outright."""
    deployed = _DEPLOYED.get(name)
    if not deployed:
        raise LookupError(f"no deployed version for model '{name}' — deploy one first")
    predictions = deployed["model"].predict(np.array(rows, dtype="float64"))
    return predictions.tolist() if hasattr(predictions, "tolist") else list(predictions)
