import threading

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import db, keycloak_admin, opa_client, storage_client
from app.auth import AuthedUser, get_current_user
from app.seed import (
    AIRFLOW_ACTIONS,
    AIRFLOW_DAGS,
    CATALOG,
    ORGANIZATIONS,
    RESOURCE_ORG,
    all_known_airflow_resources,
    all_known_resources,
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
def list_users(current: AuthedUser = Depends(get_current_user)):
    return [{**u, "org_label": ORGANIZATIONS.get(u["org"], u["org"])} for u in db.list_users()]


class UserCreateRequest(BaseModel):
    username: str
    first_name: str
    last_name: str
    email: str
    password: str
    title: str = ""
    org: str = "org-001"
    region: str = "APAC"
    hr: bool = False
    groups: list[str] = []


class UserUpdateRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    password: str | None = None
    title: str | None = None
    org: str | None = None
    region: str | None = None
    hr: bool | None = None
    groups: list[str] | None = None


@app.post("/api/users")
def create_user(req: UserCreateRequest, current: AuthedUser = Depends(get_current_user)):
    if db.get_user(req.username):
        raise HTTPException(status_code=409, detail=f"user '{req.username}' already exists")
    if req.org not in ORGANIZATIONS:
        raise HTTPException(status_code=400, detail=f"unknown organization '{req.org}'")

    # Keycloak first: if this fails (e.g. username collision Keycloak
    # itself already knows about), nothing in the authz database changes.
    try:
        keycloak_admin.create_user(req.username, req.first_name, req.last_name, req.email, req.password)
    except keycloak_admin.KeycloakAdminError as err:
        raise HTTPException(status_code=502, detail=f"Keycloak user creation failed: {err}") from err

    label = f"{req.first_name} {req.last_name}".strip() or req.username
    try:
        db.create_user_attributes(req.username, label, req.title, req.region, req.hr, req.org)
        db.set_user_groups(req.username, req.groups)
    except Exception:
        # Don't leave an orphan Keycloak account (one that can log in but
        # is invisible to every /api endpoint below) behind a failed write.
        # Best-effort: the original db exception below is the one that
        # matters, not a secondary failure cleaning up after it.
        try:
            keycloak_admin.delete_user(req.username)
        except keycloak_admin.KeycloakAdminError:
            pass
        raise
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True, "id": req.username}


@app.put("/api/users/{username}")
def update_user(username: str, req: UserUpdateRequest, current: AuthedUser = Depends(get_current_user)):
    existing = db.get_user(username)
    if not existing:
        raise HTTPException(status_code=404, detail=f"unknown user '{username}'")
    if req.org is not None and req.org not in ORGANIZATIONS:
        raise HTTPException(status_code=400, detail=f"unknown organization '{req.org}'")

    try:
        keycloak_admin.update_user(
            username, first_name=req.first_name, last_name=req.last_name, email=req.email, password=req.password
        )
    except keycloak_admin.KeycloakAdminError as err:
        raise HTTPException(status_code=502, detail=f"Keycloak user update failed: {err}") from err

    label = existing["label"]
    if req.first_name is not None or req.last_name is not None:
        first = req.first_name if req.first_name is not None else existing["label"].split(" ", 1)[0]
        last = req.last_name if req.last_name is not None else ""
        label = f"{first} {last}".strip() or existing["label"]
    db.update_user_attributes(
        username,
        label=label,
        title=req.title if req.title is not None else existing["title"],
        region=req.region if req.region is not None else existing["region"],
        is_hr=req.hr if req.hr is not None else existing["hr"],
        org=req.org if req.org is not None else existing["org"],
    )
    if req.groups is not None:
        db.set_user_groups(username, req.groups)
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True}


@app.delete("/api/users/{username}")
def delete_user(username: str, current: AuthedUser = Depends(get_current_user)):
    if not db.get_user(username):
        raise HTTPException(status_code=404, detail=f"unknown user '{username}'")
    if username == current.username:
        raise HTTPException(status_code=400, detail="cannot delete the account you're logged in as")

    try:
        keycloak_admin.delete_user(username)
    except keycloak_admin.KeycloakAdminError as err:
        raise HTTPException(status_code=502, detail=f"Keycloak user deletion failed: {err}") from err
    db.delete_user_attributes(username)
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True}


@app.get("/api/organizations")
def list_organizations(current: AuthedUser = Depends(get_current_user)):
    return [{"id": org_id, "label": label} for org_id, label in ORGANIZATIONS.items()]


@app.get("/api/catalog")
def get_catalog(current: AuthedUser = Depends(get_current_user)):
    user = current.username
    if not db.get_user(user):
        raise HTTPException(status_code=404, detail=f"unknown user '{user}'")
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


class QueryRequest(BaseModel):
    resource: str  # e.g. "sales.customers"


@app.post("/api/query")
def query(req: QueryRequest, current: AuthedUser = Depends(get_current_user)):
    user = current.username
    if not db.get_user(user):
        raise HTTPException(status_code=404, detail=f"unknown user '{user}'")
    if "." not in req.resource:
        raise HTTPException(status_code=400, detail="pick a table, not a schema, to run a query")

    allowed = opa_client.check(user, req.resource, "select")
    decision = {"sub": user, "obj": req.resource, "act": "select"}

    if not allowed:
        return {
            "allowed": False,
            "request": decision,
            "message": f"OPA denied this request — no policy grants '{user}' select on '{req.resource}'.",
        }

    schema, table = req.resource.split(".", 1)
    try:
        columns, rows = run_select(user, current.token, schema, table)
    except Exception as err:  # noqa: BLE001 — surfaced to the demo UI verbatim
        raise HTTPException(status_code=502, detail=f"Trino query failed: {err}") from err

    return {"allowed": True, "request": decision, "columns": columns, "rows": rows}


@app.get("/api/policies")
def get_policies(current: AuthedUser = Depends(get_current_user)):
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
def policy_options(current: AuthedUser = Depends(get_current_user)):
    return {"subjects": db.known_subjects(), "resources": all_known_resources()}


@app.get("/api/airflow/dags")
def list_airflow_dags(current: AuthedUser = Depends(get_current_user)):
    """Same idea as /api/catalog, one layer up: for each demo Dag, the
    current user's permission on every action in the Airflow vocabulary
    (view / trigger / view_logs / view_code), decided by the exact same
    OPA check (data.app.allow) that opa_auth_manager calls natively
    inside Airflow itself via data.airflow.allow — this endpoint doesn't
    talk to Airflow at all, it previews the same policy_data decision."""
    user = current.username
    if not db.get_user(user):
        raise HTTPException(status_code=404, detail=f"unknown user '{user}'")
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
def airflow_policy_options(current: AuthedUser = Depends(get_current_user)):
    return {
        "subjects": db.known_subjects(),
        "resources": all_known_airflow_resources(),
        "actions": AIRFLOW_ACTIONS,
    }


@app.get("/api/storage/files")
def list_storage_files(current: AuthedUser = Depends(get_current_user)):
    """Buckets and files the caller's own org can see — discovered live
    from MinIO's own ListBuckets/ListObjectsV2 responses using that org's
    scoped credentials (see storage_client.py), not a bucket name looked
    up from config. There's no OPA check here at all: the isolation is
    the credential itself, structurally unable to reach another bucket."""
    user = current.username
    user_row = db.get_user(user)
    if not user_row:
        raise HTTPException(status_code=404, detail=f"unknown user '{user}'")
    org = user_row["org"]
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
def get_storage_file(bucket: str, key: str, current: AuthedUser = Depends(get_current_user)):
    user = current.username
    user_row = db.get_user(user)
    if not user_row:
        raise HTTPException(status_code=404, detail=f"unknown user '{user}'")
    org = user_row["org"]
    try:
        content = storage_client.get_file(org, bucket, key)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"couldn't read '{bucket}/{key}': {err}") from err
    return {"bucket": bucket, "key": key, "content": content}


@app.get("/api/storage/cross-check")
def storage_cross_check(current: AuthedUser = Depends(get_current_user)):
    """Proves the isolation is real rather than assumed: finds a bucket
    that exists but isn't in the caller's own org's ListBuckets response
    (via the MinIO root credential — read-only, ListBuckets only, never
    used to read object data), then deliberately reads it using the
    caller's own scoped credentials. Expected result is a genuine MinIO
    AccessDenied — see storage_client.cross_org_attempt."""
    user = current.username
    user_row = db.get_user(user)
    if not user_row:
        raise HTTPException(status_code=404, detail=f"unknown user '{user}'")
    org = user_row["org"]
    try:
        result = storage_client.cross_org_attempt(org)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MinIO request failed: {err}") from err
    return {"acting_org": org, **result}


@app.post("/api/policies/grant")
def grant_policy(req: PolicyRequest, current: AuthedUser = Depends(get_current_user)):
    added = db.add_policy(req.sub, req.obj, req.act)
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True, "added": added}


@app.post("/api/policies/revoke")
def revoke_policy(req: PolicyRequest, current: AuthedUser = Depends(get_current_user)):
    removed = db.remove_policy(req.sub, req.obj, req.act)
    opa_client.push_policy_data(db.get_policy_data())
    return {"ok": True, "removed": removed}
