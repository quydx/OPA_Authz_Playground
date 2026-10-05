import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import airflow_client, db, opa_client, storage_client
from app.seed import (
    AIRFLOW_ACTIONS,
    AIRFLOW_DAGS,
    CATALOG,
    DEMO_USERS,
    ORGANIZATIONS,
    RESOURCE_ORG,
    all_known_airflow_resources,
    all_known_resources,
    all_known_subjects,
)
from app.trino_client import run_select, wait_for_trino

app = FastAPI(title="IDMA POC — Trino + Postgres + OPA")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    db.init_schema()
    seeded = db.seed_if_empty()
    print("[startup] seeded initial policy data" if seeded else "[startup] policy data already present, skipped seeding")
    # Reconciles additions to seed.py (new resource_org entries, new demo
    # users' attributes, new group memberships) into a database that was
    # already seeded before they existed — deliberately excludes
    # `policies`, which live grant/revoke edits, so a revoke made through
    # the UI still survives a restart. See db.sync_non_revocable_seed_data.
    db.sync_non_revocable_seed_data()
    # OPA holds no data of its own — push the current snapshot on every
    # startup so it's never serving stale/empty data after a restart.
    opa_client.push_policy_data(db.get_policy_data())
    threading.Thread(target=wait_for_trino, daemon=True).start()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/users")
def list_users():
    data = db.get_policy_data()
    groups_by_user: dict[str, list[str]] = {}
    for member, grp in data["g"]:
        groups_by_user.setdefault(member, []).append(grp)
    return [
        {
            "id": uid,
            "label": info["label"],
            "title": info["title"],
            "groups": groups_by_user.get(uid, []),
            "org": info["org"],
            "org_label": ORGANIZATIONS.get(info["org"], info["org"]),
        }
        for uid, info in DEMO_USERS.items()
    ]


@app.get("/api/organizations")
def list_organizations():
    return [{"id": org_id, "label": label} for org_id, label in ORGANIZATIONS.items()]


@app.get("/api/catalog")
def get_catalog(user: str):
    if user not in DEMO_USERS:
        raise HTTPException(status_code=404, detail=f"unknown demo user '{user}'")
    tree = []
    for schema, tables in CATALOG.items():
        schema_allowed = opa_client.check(user, schema, "select")
        tables_out = []
        for table in tables:
            resource = f"{schema}.{table}"
            tables_out.append(
                {
                    "name": table,
                    "resource": resource,
                    "allowed": opa_client.check(user, resource, "select"),
                }
            )
        org = RESOURCE_ORG.get(schema)
        tree.append(
            {
                "name": schema,
                "resource": schema,
                "allowed": schema_allowed,
                "org": org,
                "org_label": ORGANIZATIONS.get(org, org),
                "tables": tables_out,
            }
        )
    return tree


def deny_message(user: str, resource: str, action: str, verdict: dict) -> str:
    """Human-readable form of OPA's deny reason (opa/policies/app.rego)."""
    reason = verdict["reason"]
    user_org = verdict.get("user_org", "?")
    resource_org = verdict.get("resource_org", "?")
    messages = {
        "no_grant": f"no policy grants '{user}' {action} on '{resource}' — not directly, "
                    "not via a group, not via a parent resource.",
        "org_mismatch": f"'{user}' has a matching grant, but belongs to {user_org} while "
                        f"'{resource}' belongs to {resource_org}. Grants never cross the "
                        "organization boundary.",
        "user_org_unknown": f"'{user}' has no organization attribute — unknown identities "
                            "are denied (fail closed).",
        "resource_org_unknown": f"'{resource}' isn't tagged with an organization — untagged "
                                "resources are denied (fail closed).",
        "policy_data_missing": "OPA has no policy data loaded yet (cold start) — everything "
                               "is denied until the backend pushes it.",
        "opa_unreachable": "OPA could not be reached — denied by default (fail closed).",
    }
    return f"OPA denied this request — {messages.get(reason, reason)}"


class QueryRequest(BaseModel):
    user: str
    resource: str  # e.g. "sales.customers"


@app.post("/api/query")
def query(req: QueryRequest):
    if req.user not in DEMO_USERS:
        raise HTTPException(status_code=404, detail=f"unknown demo user '{req.user}'")
    if "." not in req.resource:
        raise HTTPException(status_code=400, detail="pick a table, not a schema, to run a query")

    verdict = opa_client.decide(req.user, req.resource, "select")
    decision = {"sub": req.user, "obj": req.resource, "act": "select"}

    if not verdict["allow"]:
        return {
            "allowed": False,
            "request": decision,
            "reason": verdict["reason"],
            "message": deny_message(req.user, req.resource, "select", verdict),
        }

    schema, table = req.resource.split(".", 1)
    try:
        columns, rows = run_select(req.user, schema, table)
    except Exception as err:  # noqa: BLE001 — surfaced to the demo UI verbatim
        raise HTTPException(status_code=502, detail=f"Trino query failed: {err}") from err

    return {"allowed": True, "request": decision, "columns": columns, "rows": rows}


@app.get("/api/policies")
def get_policies():
    data = db.get_policy_data()
    return {
        "p": data["p"],
        "g": data["g"],
        "g2": data["g2"],
        "resource_org": [[resource, org] for resource, org in data["resource_org"].items()],
    }


class PolicyRequest(BaseModel):
    sub: str
    obj: str
    act: str = "select"


@app.get("/api/policy-options")
def policy_options():
    return {"subjects": all_known_subjects(), "resources": all_known_resources()}


@app.get("/api/airflow/dags")
def list_airflow_dags(user: str):
    """Same idea as /api/catalog, one layer up: for each demo Dag, the
    current user's permission on every action in the Airflow vocabulary
    (view / trigger / view_logs / view_code), decided by the exact same
    OPA check (data.app.allow) that opa_auth_manager calls natively
    inside Airflow itself via data.airflow.allow — this endpoint doesn't
    talk to Airflow at all, it previews the same policy_data decision."""
    if user not in DEMO_USERS:
        raise HTTPException(status_code=404, detail=f"unknown demo user '{user}'")
    out = []
    for dag_id, info in AIRFLOW_DAGS.items():
        resource = f"airflow.dag.{dag_id}"
        permissions = {action: opa_client.check(user, resource, action) for action in AIRFLOW_ACTIONS}
        org = RESOURCE_ORG.get(resource)
        out.append(
            {
                "id": dag_id,
                "label": info["label"],
                "description": info["description"],
                "resource": resource,
                "permissions": permissions,
                "org": org,
                "org_label": ORGANIZATIONS.get(org, org),
            }
        )
    return out


@app.get("/api/airflow/policy-options")
def airflow_policy_options():
    return {
        "subjects": all_known_subjects(),
        "resources": all_known_airflow_resources(),
        "actions": AIRFLOW_ACTIONS,
    }


@app.get("/api/storage/files")
def list_storage_files(user: str):
    """Buckets and files the caller's own org can see — discovered live
    from MinIO's own ListBuckets/ListObjectsV2 responses using that org's
    scoped credentials (see storage_client.py), not a bucket name looked
    up from config. There's no OPA check here at all: the isolation is
    the credential itself, structurally unable to reach another bucket."""
    if user not in DEMO_USERS:
        raise HTTPException(status_code=404, detail=f"unknown demo user '{user}'")
    org = DEMO_USERS[user]["org"]
    try:
        buckets = storage_client.list_files(org)
    except Exception as err:  # noqa: BLE001 — surfaced to the demo UI verbatim
        raise HTTPException(status_code=502, detail=f"MinIO request failed: {err}") from err
    return {
        "org": org,
        "org_label": ORGANIZATIONS.get(org, org),
        "buckets": buckets,
    }


@app.get("/api/storage/file")
def get_storage_file(user: str, bucket: str, key: str):
    if user not in DEMO_USERS:
        raise HTTPException(status_code=404, detail=f"unknown demo user '{user}'")
    org = DEMO_USERS[user]["org"]
    try:
        content = storage_client.get_file(org, bucket, key)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"couldn't read '{bucket}/{key}': {err}") from err
    return {"bucket": bucket, "key": key, "content": content}


@app.get("/api/storage/cross-check")
def storage_cross_check(user: str):
    """Proves the isolation is real rather than assumed: finds a bucket
    that exists but isn't in the caller's own org's ListBuckets response
    (via the MinIO root credential — read-only, ListBuckets only, never
    used to read object data), then deliberately reads it using the
    caller's own scoped credentials. Expected result is a genuine MinIO
    AccessDenied — see storage_client.cross_org_attempt."""
    if user not in DEMO_USERS:
        raise HTTPException(status_code=404, detail=f"unknown demo user '{user}'")
    org = DEMO_USERS[user]["org"]
    try:
        result = storage_client.cross_org_attempt(org)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MinIO request failed: {err}") from err
    return {"acting_org": org, **result}


@app.get("/api/airflow/credentials")
def airflow_credentials():
    """Live demo-user passwords for the Credentials tab. `available` is
    false outside Kubernetes (no K8S_NAMESPACE) — the frontend falls back
    to log-fetch instructions in that case rather than showing nothing."""
    return {"available": airflow_client.available(), "passwords": airflow_client.fetch_passwords()}


@app.post("/api/policies/grant")
def grant_policy(req: PolicyRequest):
    added = db.add_policy(req.sub, req.obj, req.act)
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True, "added": added}


@app.post("/api/policies/revoke")
def revoke_policy(req: PolicyRequest):
    removed = db.remove_policy(req.sub, req.obj, req.act)
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True, "removed": removed}
