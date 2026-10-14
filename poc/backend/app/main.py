import json
import re
import threading

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import airflow_client, db, feast_client, keycloak_admin, mlflow_client, opa_client, storage_client
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
    threading.Thread(target=mlflow_client.load_deployed_models, daemon=True).start()
    # Keeps re-pushing on an interval for the rest of this process's
    # life — see opa_client.resync_loop's own docstring for why a
    # startup-only push isn't enough (OPA can restart independently of
    # this backend).
    threading.Thread(target=opa_client.resync_loop, args=(db.get_policy_data,), daemon=True).start()


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


# Usecase tab — a 7-step wizard mirroring automl_wizard_pipeline.py's own
# step names (chọn bài toán -> kết nối & chọn dữ liệu -> tiền xử lý ->
# chọn mục tiêu -> biến đổi & tạo biến -> cấu hình & chạy -> kết quả).
# Step 2 (connect & select data) reuses /api/catalog and /api/query
# verbatim below — real OPA+Trino checks, not a bypass, unlike the Dag's
# own direct-Postgres read (see that file's docstring on why the Dag
# itself still can't do this: no logged-in caller for OPA to evaluate a
# grant against). Steps 3 and 5 (preprocess/transform) get their own
# stateless preview endpoints here, mirroring the Dag's own
# preprocess_data()/transform_and_create_features() task logic exactly —
# pure functions over whatever dataset the frontend already fetched via
# /api/query, no DB access. Only the final run (step 6) ever touches
# Airflow; the Dag re-derives everything from Postgres itself at train
# time regardless of what these previews showed, same trust-boundary
# reasoning as before this rewrite.
#
# ("Chọn dữ liệu" and "Làm sạch" used to be their own steps/endpoints —
# folded into step 2 above: picking a table and fetching its rows was
# always one decision, and dropping null/duplicate rows is now a fixed,
# automatic part of that fetch rather than a user-facing choice, so there
# was no longer a reason to show them as separate screens.)
AUTOML_RESOURCE = "airflow.dag.automl_wizard_pipeline"
AUTOML_CLASSIFICATION_MODELS = ["random_forest_classifier", "logistic_regression", "gradient_boosting_classifier"]
AUTOML_REGRESSION_MODELS = ["random_forest_regressor", "linear_regression", "gradient_boosting_regressor"]


@app.get("/api/wizard/options")
def wizard_options(current: AuthedUser = Depends(get_current_user)):
    return {
        "classification_models": AUTOML_CLASSIFICATION_MODELS,
        "regression_models": AUTOML_REGRESSION_MODELS,
    }


class WizardPreprocessRequest(BaseModel):
    columns: list[str]
    rows: list[list]
    numeric_columns: list[str] = []
    datetime_columns: list[str] = []
    normalize_columns: list[str] = []
    impute_columns: list[str] = []
    impute_strategy: str = "mean"  # mean | median | mode | constant
    impute_value: str | None = None


@app.post("/api/wizard/preprocess")
def wizard_preprocess(req: WizardPreprocessRequest, current: AuthedUser = Depends(get_current_user)):
    """Step 3 preview — same logic as the Dag's preprocess_data() task:
    optional type casting (numeric/datetime, coercing unparseable values
    to null rather than raising — this is a preview of real demo data,
    not a strict schema), string normalization (trim + collapse
    whitespace + lowercase), and null imputation (mean/median/mode/
    constant) over whichever columns the caller names."""
    import pandas as pd

    if req.impute_strategy not in ("mean", "median", "mode", "constant"):
        raise HTTPException(status_code=400, detail="impute_strategy must be mean, median, mode or constant")

    df = pd.DataFrame(req.rows, columns=req.columns)
    before_null_count = int(df.isna().sum().sum())
    coerced_errors = 0

    for col in req.numeric_columns:
        if col not in req.columns:
            raise HTTPException(status_code=400, detail=f"numeric_columns: unknown column '{col}'")
        before_na = int(df[col].isna().sum())
        df[col] = pd.to_numeric(df[col], errors="coerce")
        coerced_errors += int(df[col].isna().sum()) - before_na

    for col in req.datetime_columns:
        if col not in req.columns:
            raise HTTPException(status_code=400, detail=f"datetime_columns: unknown column '{col}'")
        before_na = int(df[col].isna().sum())
        parsed = pd.to_datetime(df[col], errors="coerce")
        coerced_errors += int(parsed.isna().sum()) - before_na
        df[col] = parsed.astype(str).where(parsed.notna(), None)

    for col in req.normalize_columns:
        if col not in req.columns:
            raise HTTPException(status_code=400, detail=f"normalize_columns: unknown column '{col}'")
        df[col] = df[col].apply(lambda v: " ".join(str(v).split()).lower() if v is not None else v)

    for col in req.impute_columns:
        if col not in req.columns:
            raise HTTPException(status_code=400, detail=f"impute_columns: unknown column '{col}'")
        if req.impute_strategy in ("mean", "median"):
            numeric = pd.to_numeric(df[col], errors="coerce")
            fill = float(numeric.mean()) if req.impute_strategy == "mean" else float(numeric.median())
        elif req.impute_strategy == "mode":
            non_null = df[col].dropna()
            fill = non_null.mode().iloc[0] if not non_null.empty else None
        else:
            fill = req.impute_value
        df[col] = df[col].fillna(fill)

    after_null_count = int(df.isna().sum().sum())
    rows = df.where(pd.notnull(df), None).values.tolist()
    return {
        "columns": req.columns,
        "rows": rows,
        "before_null_count": before_null_count,
        "after_null_count": after_null_count,
        "coerced_errors": coerced_errors,
    }


class DerivedFeatureSpec(BaseModel):
    name: str
    expression: str


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _numeric_coerced_for_sql(df):
    """DuckDB (unlike pandas) won't implicitly compare/arith a VARCHAR
    against a number — and the Dag's own connect_and_select_data() hands
    back numeric Postgres columns (e.g. numeric(10,2) amounts) as plain
    strings, since its jsonable() helper stringifies anything that isn't
    already an int/float/str/bool for XCom. Give every column that's
    fully numeric-parseable a real numeric dtype before it reaches
    DuckDB, so e.g. `amount > 100` works the same regardless of whether
    amount arrived as a float or as "149.00". Genuinely textual columns
    (region, ...) fail the full-parse check and are left untouched."""
    import pandas as pd

    coerced = df.copy()
    for col in coerced.columns:
        if coerced[col].dtype == object:
            numeric = pd.to_numeric(coerced[col], errors="coerce")
            if numeric.notna().equals(coerced[col].notna()):
                coerced[col] = numeric
    return coerced


def _add_sql_derived_column(df, name: str, expression: str):
    """Evaluate `name`/`expression` as real SQL against `df` via DuckDB and
    return the resulting dataframe (df plus the new column). Used by both
    wizard_transform() below and the Dag's transform_and_create_features()
    task — kept deliberately narrow: a single projected expression, no
    filesystem/network access, no second statement."""
    import duckdb

    if not _IDENT_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"derived column name {name!r} must be a plain identifier (letters/digits/underscore, not starting with a digit)",
        )
    if name in df.columns:
        raise HTTPException(status_code=400, detail=f"derived column name {name!r} already exists")
    if ";" in expression:
        raise HTTPException(status_code=400, detail="derived column expression may not contain ';' — one expression only")

    con = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        con.register("t", _numeric_coerced_for_sql(df))
        try:
            return con.sql(f'SELECT *, ({expression}) AS "{name}" FROM t').df()
        except duckdb.Error as exc:
            raise HTTPException(status_code=400, detail=f"derived column {name!r}: {exc}") from exc
    finally:
        con.close()


class WizardTransformRequest(BaseModel):
    columns: list[str]
    rows: list[list]
    target_column: str
    feature_columns: str | None = None
    encoding: str = "onehot"  # onehot | ordinal
    scale: str = "none"  # none | standard | minmax
    log_columns: str | None = None
    derived: list[DerivedFeatureSpec] = []


@app.post("/api/wizard/transform")
def wizard_transform(req: WizardTransformRequest, current: AuthedUser = Depends(get_current_user)):
    """Step 5 preview — same logic as the Dag's transform_and_create_features()
    task: pick feature_columns (explicit or auto-numeric), add any SQL
    derived columns, apply an optional log1p transform, then optional
    scaling, then encode whatever's still non-numeric (one-hot or
    ordinal)."""
    import numpy as np
    import pandas as pd

    if req.target_column not in req.columns:
        raise HTTPException(status_code=400, detail=f"target_column '{req.target_column}' not in columns {req.columns}")
    if req.encoding not in ("onehot", "ordinal"):
        raise HTTPException(status_code=400, detail="encoding must be 'onehot' or 'ordinal'")
    if req.scale not in ("none", "standard", "minmax"):
        raise HTTPException(status_code=400, detail="scale must be 'none', 'standard' or 'minmax'")

    df = pd.DataFrame(req.rows, columns=req.columns)
    requested = [c.strip() for c in (req.feature_columns or "").split(",") if c.strip()]
    if requested:
        unknown = [c for c in requested if c not in req.columns]
        if unknown:
            raise HTTPException(status_code=400, detail=f"feature_columns not found: {unknown} — available: {req.columns}")
        feature_cols = [c for c in requested if c != req.target_column]
    else:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        feature_cols = [c for c in numeric_cols if c not in (req.target_column, "id")]
    if not feature_cols:
        raise HTTPException(status_code=400, detail="no usable feature columns — set feature_columns explicitly")

    work = df[feature_cols].copy()

    for spec in req.derived:
        work = _add_sql_derived_column(work, spec.name, spec.expression)

    log_cols = [c.strip() for c in (req.log_columns or "").split(",") if c.strip()]
    for col in log_cols:
        if col not in work.columns:
            raise HTTPException(status_code=400, detail=f"log_columns: unknown column '{col}' — available: {list(work.columns)}")
        numeric = pd.to_numeric(work[col], errors="coerce")
        work[col] = np.log1p(numeric.clip(lower=0))

    numeric_feature_cols = work.select_dtypes(include="number").columns.tolist()
    if req.scale == "standard":
        for col in numeric_feature_cols:
            std = work[col].std()
            work[col] = (work[col] - work[col].mean()) / std if std else 0.0
    elif req.scale == "minmax":
        for col in numeric_feature_cols:
            lo, hi = work[col].min(), work[col].max()
            work[col] = (work[col] - lo) / (hi - lo) if hi != lo else 0.0

    non_numeric_cols = [c for c in work.columns if c not in numeric_feature_cols]
    if req.encoding == "onehot":
        encoded = pd.get_dummies(work, columns=non_numeric_cols, drop_first=False)
    else:
        encoded = work.copy()
        for col in non_numeric_cols:
            encoded[col] = pd.factorize(encoded[col])[0]

    preview_df = encoded.head(10)
    preview_rows = preview_df.where(pd.notnull(preview_df), None).values.tolist()
    return {
        "columns": list(encoded.columns),
        "feature_names": list(encoded.columns),
        "numeric_columns": numeric_feature_cols,
        "preview_rows": preview_rows,
    }


class WizardRunRequest(BaseModel):
    problem_type: str = "classification"
    schema_: str = "sales"
    table: str = "orders"
    # Step 3 — preprocess (see WizardPreprocessRequest above for meaning).
    preprocess_numeric_columns: str | None = None
    preprocess_datetime_columns: str | None = None
    preprocess_normalize_columns: str | None = None
    preprocess_impute_columns: str | None = None
    preprocess_impute_strategy: str = "mean"
    preprocess_impute_value: str | None = None
    target_column: str = "region"
    feature_columns: str | None = None
    # Step 5 — transform (see WizardTransformRequest above for meaning).
    transform_encoding: str = "onehot"
    transform_scale: str = "none"
    transform_log_columns: str | None = None
    transform_derived: list[DerivedFeatureSpec] = []
    # Opt-in: nothing is saved to Feast unless this is true and a name is
    # given — see automl_wizard_pipeline.py's luu_tru_dac_trung task.
    feature_view_save: bool = False
    feature_view_name: str | None = None
    model_type: str = "random_forest_classifier"
    test_size: float = 0.2
    random_state: int = 42
    n_estimators: int = 100
    max_depth: int = 4
    min_score: float = 0.5
    auto_promote: bool = False
    experiment_name: str = "idma-demo"
    registered_model_name: str = "automl-wizard-model"


@app.post("/api/wizard/run")
def wizard_run(req: WizardRunRequest, current: AuthedUser = Depends(get_current_user)):
    """Step 6 — same (user, resource, action) check OPAAuthManager itself
    would run natively inside Airflow for a trigger call — enforced here
    instead of there because the svc-backend service account this calls
    Airflow as (see airflow_client.py) is an Admin, which bypasses that
    native check entirely. Skipping this would silently let anyone with a
    login trigger training runs regardless of their actual policy_data
    grants. problem_type/model_type are still cross-checked as a cheap
    defensive guard against a malformed direct API call — schema/table/
    target_column/preprocess/transform fields aren't, since steps 2-3-4-5
    already validated those live against the real catalog/columns/preview,
    and the Dag re-validates/re-derives them again itself regardless."""
    if not opa_client.check(current.username, AUTOML_RESOURCE, "trigger"):
        raise HTTPException(status_code=403, detail=f"OPA denied trigger on '{AUTOML_RESOURCE}' for '{current.username}'")

    if req.problem_type not in ("classification", "regression"):
        raise HTTPException(status_code=400, detail="problem_type must be 'classification' or 'regression'")
    expected_models = AUTOML_CLASSIFICATION_MODELS if req.problem_type == "classification" else AUTOML_REGRESSION_MODELS
    if req.model_type not in expected_models:
        raise HTTPException(status_code=400, detail=f"model_type '{req.model_type}' doesn't match problem_type={req.problem_type!r}")
    if req.feature_view_save:
        if not req.feature_view_name or not _IDENT_RE.match(req.feature_view_name):
            raise HTTPException(
                status_code=400,
                detail="feature_view_name is required when feature_view_save is true, and must be a plain identifier",
            )

    conf = {
        "problem_type": req.problem_type,
        "schema": req.schema_,
        "table": req.table,
        "preprocess_numeric_columns": req.preprocess_numeric_columns,
        "preprocess_datetime_columns": req.preprocess_datetime_columns,
        "preprocess_normalize_columns": req.preprocess_normalize_columns,
        "preprocess_impute_columns": req.preprocess_impute_columns,
        "preprocess_impute_strategy": req.preprocess_impute_strategy,
        "preprocess_impute_value": req.preprocess_impute_value,
        "target_column": req.target_column,
        "feature_columns": req.feature_columns,
        "transform_encoding": req.transform_encoding,
        "transform_scale": req.transform_scale,
        "transform_log_columns": req.transform_log_columns,
        # The Dag's transform_derived Param is a plain string (JSON-encoded)
        # — Airflow Params don't have a native "list of objects" type that's
        # worth the complexity here, same reasoning as feature_columns being
        # a comma-separated string instead of an array Param.
        "transform_derived": json.dumps([d.model_dump() for d in req.transform_derived]) if req.transform_derived else None,
        "feature_view_save": req.feature_view_save,
        "feature_view_name": req.feature_view_name,
        "model_type": req.model_type,
        "test_size": req.test_size,
        "random_state": req.random_state,
        "n_estimators": req.n_estimators,
        "max_depth": req.max_depth,
        "min_score": req.min_score,
        "auto_promote": req.auto_promote,
        "experiment_name": req.experiment_name,
        "registered_model_name": req.registered_model_name,
    }
    try:
        run = airflow_client.trigger_automl_run(conf)
    except airflow_client.AirflowClientError as err:
        raise HTTPException(status_code=502, detail=f"Airflow trigger failed: {err}") from err
    return {"dag_run_id": run["dag_run_id"], "state": run["state"]}


@app.get("/api/wizard/run/{dag_run_id}")
def wizard_run_status(dag_run_id: str, current: AuthedUser = Depends(get_current_user)):
    """Step 7."""
    if not opa_client.check(current.username, AUTOML_RESOURCE, "view"):
        raise HTTPException(status_code=403, detail=f"OPA denied view on '{AUTOML_RESOURCE}' for '{current.username}'")

    try:
        status = airflow_client.get_run_status(dag_run_id)
    except airflow_client.AirflowClientError as err:
        raise HTTPException(status_code=502, detail=f"Airflow status check failed: {err}") from err

    result = None
    if status["state"] == "success":
        try:
            result = airflow_client.get_run_result(dag_run_id)
        except airflow_client.AirflowClientError as err:
            raise HTTPException(status_code=502, detail=f"Airflow result fetch failed: {err}") from err
        # luu_tru_dac_trung is a parallel branch off step 5 (see that
        # task's own docstring), not a dependency of ket_qua — polled
        # here as a best-effort add-on rather than gating it on ket_qua's
        # own success. None (still 404) just means it hasn't finished
        # yet; the next poll picks it up once it has.
        try:
            feast_result = airflow_client.get_run_result(dag_run_id, task_id="luu_tru_dac_trung")
        except airflow_client.AirflowClientError:
            feast_result = None
        if result is not None and feast_result is not None:
            result["feast"] = feast_result
    return {"dag_run_id": dag_run_id, "state": status["state"], "conf": status.get("conf"), "result": result}


@app.get("/api/wizard/features/online/{entity_id}")
def wizard_online_features(
    entity_id: int,
    feature_view: str,
    features: str,
    current: AuthedUser = Depends(get_current_user),
):
    """Closes the store->serve loop for the demo: look up whatever
    luu_tru_dac_trung last materialized for this entity_id under the
    named feature view, straight from Feast's online store. `feature_view`
    and `features` (comma-separated) are required now — every feature
    view is user-named and dynamically-shaped (see
    poc/feast/registry_api.py), so there's no longer one fixed default to
    assume. Same OPA check as the rest of the wizard — this is still part
    of that flow, not a new authz surface. 404 (not an empty 200) when
    Feast has nothing for this entity, same convention the rest of this
    file uses for "that thing doesn't exist"."""
    if not opa_client.check(current.username, AUTOML_RESOURCE, "view"):
        raise HTTPException(status_code=403, detail=f"OPA denied view on '{AUTOML_RESOURCE}' for '{current.username}'")

    feature_list = [f.strip() for f in features.split(",") if f.strip()]
    if not feature_list:
        raise HTTPException(status_code=400, detail="features must be a non-empty comma-separated list")

    try:
        result = feast_client.get_online_features(entity_id, feature_view, feature_list)
    except feast_client.FeastClientError as err:
        raise HTTPException(status_code=502, detail=f"Feast lookup failed: {err}") from err
    if result is None:
        raise HTTPException(status_code=404, detail=f"no stored features for entity_id {entity_id} in '{feature_view}'")
    return {"entity_id": entity_id, "feature_view": feature_view, "features": result}


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


@app.get("/api/models")
def list_models(current: AuthedUser = Depends(get_current_user)):
    """Every model MLflow's Model Registry knows about, every version of
    each, and which one (if any) is deployed — see mlflow_client.py. No
    OPA check here: unlike the Data Catalog/Airflow tabs this is
    modeling per-org data access, just what's been trained."""
    try:
        return mlflow_client.list_models()
    except Exception as err:  # noqa: BLE001 — surfaced to the demo UI verbatim
        raise HTTPException(status_code=502, detail=f"MLflow request failed: {err}") from err


@app.post("/api/models/{name}/versions/{version}/deploy")
def deploy_model(name: str, version: str, current: AuthedUser = Depends(get_current_user)):
    try:
        return mlflow_client.deploy_model_version(name, version)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MLflow deploy failed: {err}") from err


class ModelInvokeRequest(BaseModel):
    rows: list[list[float]]  # feature vectors, same column order as training


@app.post("/api/models/{name}/invoke")
def invoke_model(name: str, req: ModelInvokeRequest, current: AuthedUser = Depends(get_current_user)):
    try:
        predictions = mlflow_client.invoke(name, req.rows)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Inference failed: {err}") from err
    return {"model": name, "version": mlflow_client.get_deployed_version(name), "predictions": predictions}
