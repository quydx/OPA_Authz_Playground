"""Configurable 7-step AutoML wizard pipeline — mirrors the iHub product's
own wizard flow (Chọn bài toán -> Kết nối & chọn dữ liệu -> Tiền xử lý ->
Chọn mục tiêu -> Biến đổi & tạo biến -> Cấu hình & chạy -> Kết quả) as
seven Airflow tasks. ("Chọn dữ liệu" and "Làm sạch" used to be their own
steps; connecting to the source and picking a table were really one
decision, and dropping null/duplicate rows is now just an automatic,
fixed part of that same step rather than a user-facing screen — folding
both in keeps the wizard to the steps that actually require a choice.)

There's also an eighth task, luu_tru_dac_trung, that isn't one of the
seven wizard screens — it's a second, independent consumer of step 5's
output that writes the engineered features into Feast (see
poc/feast/repo/features.py) alongside, not instead of, step 6 training
on them. See that task's own docstring.

The frontend's Usecase tab now drives this as a real multi-screen wizard
(see backend/app/main.py's /api/wizard/* endpoints) rather than a single
form: steps 2, 3 and 5 (connect+select/preprocess/transform) are
previewed live against the actual data before the user ever reaches
step 6 — that preview goes through /api/catalog and /api/query, i.e. the
same OPA+Trino path the Data Catalog tab uses, not this Dag. This Dag
only ever runs for real at step 6 ("Cấu hình & chạy"), and re-derives
everything itself from Postgres at that point rather than trusting
whatever the wizard already showed — same trust-boundary reasoning as
before this rewrite, just no longer also needing to double as a
human-facing Trigger-UI form (its old `schema`/`table`/`target_column`
Param dropdowns are gone — see below).

Deliberate simplification unchanged from before: step 2 (connect & select
data) reads straight from the `demo` Postgres database with its own
psycopg2 connection, *not* through Trino/OPA. An Airflow DAG run has no
logged-in Keycloak user attached to it — there's no per-request bearer
token to forward the way backend/app/trino_client.py does — so there's no
"caller" for OPA to evaluate a grant against. Same reasoning as
train_sample_model/train_eval_pipeline never querying Trino at all; this
one at least reads the real demo.sales/demo.hr tables instead of a toy
dataset, but the read bypasses the authz layer entirely — the wizard's
own live-preview steps are what actually give the user an OPA-gated view
of this same data before they get here.

Authorization for the Dag itself (who can view/trigger/see its logs or
code) is still the normal OPA-decided story — see seed.py's grants for
airflow.dag.automl_wizard_pipeline.

Heavy imports (psycopg2, pandas, sklearn, mlflow) stay inside each task
callable rather than at module level, same reasoning as every other Dag
here: DAG parsing happens in the scheduler and dag-processor regardless
of whether a given task ever runs, and shouldn't need them importable.
"""
import os
from datetime import datetime

from airflow.sdk import DAG, Param, task

# Same demo-only credential already documented in the frontend's
# Credentials tab — this is the `demo` database the `sales`/`hr`/
# `org2_sales` schemas Trino also queries live in, read here directly.
# Via env var (same pattern as MLFLOW_TRACKING_URI below) because the
# Postgres hostname differs between docker-compose ("postgres") and the
# k8s manifests ("idma-postgres") — see docker-compose.yml's
# airflow-common-env and k8s/08-airflow.yaml.
DEMO_DB_DSN = os.environ["DEMO_DB_DSN"]

MLFLOW_EXPERIMENT = "idma-demo"

# Only used to scope connect_and_select_data()'s information_schema filter —
# not a UI dropdown source anymore (the wizard gets its live schema/table
# list from /api/catalog instead), so unlike before this rewrite there's
# no need to also keep a table/column union in sync here by hand.
SCHEMAS = ["sales", "hr", "org2_sales"]

CLASSIFICATION_MODELS = ["random_forest_classifier", "logistic_regression", "gradient_boosting_classifier"]
REGRESSION_MODELS = ["random_forest_regressor", "linear_regression", "gradient_boosting_regressor"]


@task(task_id="chon_bai_toan")
def choose_problem_type(**context) -> str:
    """Step 1 — Chọn bài toán (choose the problem type)."""
    problem_type = context["params"]["problem_type"]
    if problem_type not in ("classification", "regression"):
        # The Param enum below already prevents this from a form — this
        # guards the CLI/REST path, which can pass an arbitrary conf dict
        # that skips Param validation entirely.
        raise ValueError(f"problem_type must be 'classification' or 'regression', got {problem_type!r}")
    print(f"Chọn bài toán: {problem_type}")
    return problem_type


@task(task_id="ket_noi_va_chon_du_lieu")
def connect_and_select_data(**context) -> dict:
    """Step 2 — Kết nối & chọn dữ liệu (connect to the data source and
    select which table to use — one screen/task now, not two: connecting
    was only ever a validation + information_schema lookup, selecting a
    table was the only part that actually read rows). Rows with any null,
    and exact-duplicate rows, are dropped automatically here — fixed, not
    user-configurable, previously its own "Làm sạch" step/Param."""
    import psycopg2

    p = context["params"]
    schema, table = p["schema"], p["table"]

    conn = psycopg2.connect(DEMO_DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(%s) ORDER BY table_schema, table_name",
                (SCHEMAS,),
            )
            catalog: dict[str, list[str]] = {}
            for s, t in cur.fetchall():
                catalog.setdefault(s, []).append(t)
            if schema not in catalog:
                raise ValueError(f"unknown schema {schema!r} — available: {sorted(catalog)}")
            if table not in catalog[schema]:
                raise ValueError(f"unknown table '{schema}.{table}' — available in {schema}: {sorted(catalog[schema])}")

            # Table/schema names are checked against the live catalog
            # above, never taken from the request as a raw SQL fragment.
            cur.execute(f'SELECT * FROM "{schema}"."{table}"')
            columns = [d.name for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # date/decimal objects aren't JSON-safe for XCom — stringify anything
    # that isn't already a plain int/float/str/bool/None.
    def jsonable(v):
        return v if v is None or isinstance(v, (int, float, str, bool)) else str(v)

    rows = [[jsonable(v) for v in row] for row in rows]
    before = len(rows)
    rows = [r for r in rows if all(v is not None for v in r)]
    seen = set()
    deduped = []
    for r in rows:
        key = tuple(r)
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    rows = deduped

    print(f"Kết nối & chọn dữ liệu: {schema}.{table} — {before} -> {len(rows)} dòng (drop nulls+duplicates), {len(columns)} cột: {columns}")
    # 4, not some more typical-looking round number like 10 or 50: this
    # lab's own demo tables are deliberately tiny (sales.orders has 6
    # rows total) — the real floor is "enough for train_test_split to
    # leave at least 1 row in both halves even at max test_size (0.5)",
    # not a realistic training-set size.
    if len(rows) < 4:
        raise ValueError(f"only {len(rows)} rows left after dropping nulls/duplicates — too few to train on")
    return {"columns": columns, "rows": rows}


@task(task_id="tien_xu_ly")
def preprocess_data(dataset: dict, **context) -> dict:
    """Step 3 — Tiền xử lý (preprocess): optional type casting (coercing
    unparseable values to null rather than raising — this is real demo
    data, not a strict schema), string normalization (trim + collapse
    whitespace + lowercase), and null imputation (mean/median/mode/
    constant). Mirrors backend/app/main.py's /api/wizard/preprocess
    preview exactly. Every field here is optional (empty string = skip),
    same "empty means auto" convention as feature_columns below."""
    import pandas as pd

    p = context["params"]
    columns, rows = dataset["columns"], dataset["rows"]
    df = pd.DataFrame(rows, columns=columns)

    def names(key: str) -> list[str]:
        return [c.strip() for c in (p[key] or "").split(",") if c.strip()]

    numeric_cols = names("preprocess_numeric_columns")
    datetime_cols = names("preprocess_datetime_columns")
    normalize_cols = names("preprocess_normalize_columns")
    impute_cols = names("preprocess_impute_columns")
    impute_strategy = p["preprocess_impute_strategy"]
    impute_value = p["preprocess_impute_value"]

    for col in numeric_cols:
        if col not in columns:
            raise ValueError(f"preprocess numeric column '{col}' not found — available: {columns}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in datetime_cols:
        if col not in columns:
            raise ValueError(f"preprocess datetime column '{col}' not found — available: {columns}")
        parsed = pd.to_datetime(df[col], errors="coerce")
        df[col] = parsed.astype(str).where(parsed.notna(), None)
    for col in normalize_cols:
        if col not in columns:
            raise ValueError(f"preprocess normalize column '{col}' not found — available: {columns}")
        df[col] = df[col].apply(lambda v: " ".join(str(v).split()).lower() if v is not None else v)
    for col in impute_cols:
        if col not in columns:
            raise ValueError(f"preprocess impute column '{col}' not found — available: {columns}")
        if impute_strategy in ("mean", "median"):
            numeric = pd.to_numeric(df[col], errors="coerce")
            fill = float(numeric.mean()) if impute_strategy == "mean" else float(numeric.median())
        elif impute_strategy == "mode":
            non_null = df[col].dropna()
            fill = non_null.mode().iloc[0] if not non_null.empty else None
        elif impute_strategy == "constant":
            fill = impute_value
        else:
            raise ValueError(f"preprocess_impute_strategy must be mean|median|mode|constant, got {impute_strategy!r}")
        df[col] = df[col].fillna(fill)

    rows_out = df.where(pd.notnull(df), None).values.tolist()
    print(
        f"Tiền xử lý: numeric={numeric_cols} datetime={datetime_cols} "
        f"normalize={normalize_cols} impute={impute_cols}({impute_strategy})"
    )
    return {"columns": columns, "rows": rows_out}


@task(task_id="chon_muc_tieu")
def select_target(dataset: dict, **context) -> dict:
    """Step 4 — Chọn mục tiêu (select the target column)."""
    p = context["params"]
    columns = dataset["columns"]
    target = p["target_column"]
    if target not in columns:
        raise ValueError(f"target_column '{target}' not found — available columns: {columns}")
    print(f"Chọn mục tiêu: {target}")
    return {"columns": columns, "rows": dataset["rows"], "target_column": target}


@task(task_id="bien_doi_tao_bien")
def transform_and_create_features(dataset: dict, **context) -> dict:
    """Step 5 — Biến đổi & tạo biến (transform + feature creation). Empty
    feature_columns means "auto": every numeric, non-id, non-target
    column. An explicit list may also name text columns — those get
    encoded (one-hot or ordinal) rather than rejected, since several of
    this lab's own demo columns (region, department, ...) are exactly
    this shape. Optional SQL-expression derived columns / log1p / scaling
    are applied before encoding, in that order — mirrors
    backend/app/main.py's /api/wizard/transform preview exactly."""
    import json
    import re

    import duckdb
    import numpy as np
    import pandas as pd

    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def numeric_coerced_for_sql(frame):
        # DuckDB (unlike pandas) won't implicitly compare/arith a VARCHAR
        # against a number — and connect_and_select_data() above hands back
        # numeric Postgres columns (e.g. numeric(10,2) amounts) as plain
        # strings, since its jsonable() helper stringifies anything that
        # isn't already an int/float/str/bool for XCom. Give every column
        # that's fully numeric-parseable a real numeric dtype before it
        # reaches DuckDB, so e.g. `amount > 100` works the same regardless
        # of whether amount arrived as a float or as "149.00". Genuinely
        # textual columns (region, ...) fail the full-parse check and are
        # left untouched.
        coerced = frame.copy()
        for col in coerced.columns:
            if coerced[col].dtype == object:
                numeric = pd.to_numeric(coerced[col], errors="coerce")
                if numeric.notna().equals(coerced[col].notna()):
                    coerced[col] = numeric
        return coerced

    def add_sql_derived_column(frame, name, expression):
        if not ident_re.match(name):
            raise ValueError(f"derived column name {name!r} must be a plain identifier (letters/digits/underscore, not starting with a digit)")
        if name in frame.columns:
            raise ValueError(f"derived column name {name!r} already exists")
        if ";" in expression:
            raise ValueError("derived column expression may not contain ';' — one expression only")
        con = duckdb.connect(":memory:", config={"enable_external_access": False})
        try:
            con.register("t", numeric_coerced_for_sql(frame))
            try:
                return con.sql(f'SELECT *, ({expression}) AS "{name}" FROM t').df()
            except duckdb.Error as exc:
                raise ValueError(f"derived column {name!r}: {exc}") from exc
        finally:
            con.close()

    p = context["params"]
    columns, rows, target = dataset["columns"], dataset["rows"], dataset["target_column"]
    df = pd.DataFrame(rows, columns=columns)

    # Optional — None when left blank (see the Param's nullable type
    # below), same "empty means auto" meaning as the old plain-string
    # default.
    requested = [c.strip() for c in (p["feature_columns"] or "").split(",") if c.strip()]
    if requested:
        unknown = [c for c in requested if c not in columns]
        if unknown:
            raise ValueError(f"feature_columns not found: {unknown} — available: {columns}")
        feature_cols = [c for c in requested if c != target]
    else:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        feature_cols = [c for c in numeric_cols if c not in (target, "id")]
    if not feature_cols:
        raise ValueError("no usable feature columns — set feature_columns explicitly")

    work = df[feature_cols].copy()

    # transform_derived is a JSON-encoded string Param (see the Dag's
    # params= block) rather than an array Param — same "plain string,
    # parsed here" convention as feature_columns above, just JSON instead
    # of comma-separated since each entry is itself a small object.
    derived = json.loads(p["transform_derived"]) if p["transform_derived"] else []
    for spec in derived:
        work = add_sql_derived_column(work, spec["name"], spec["expression"])

    log_cols = [c.strip() for c in (p["transform_log_columns"] or "").split(",") if c.strip()]
    for col in log_cols:
        if col not in work.columns:
            raise ValueError(f"transform_log_columns: unknown column '{col}' — available: {list(work.columns)}")
        numeric = pd.to_numeric(work[col], errors="coerce")
        work[col] = np.log1p(numeric.clip(lower=0))

    numeric_feature_cols = work.select_dtypes(include="number").columns.tolist()
    scale = p["transform_scale"]
    if scale == "standard":
        for col in numeric_feature_cols:
            std = work[col].std()
            work[col] = (work[col] - work[col].mean()) / std if std else 0.0
    elif scale == "minmax":
        for col in numeric_feature_cols:
            lo, hi = work[col].min(), work[col].max()
            work[col] = (work[col] - lo) / (hi - lo) if hi != lo else 0.0

    # Feast feature-store write (the luu_tru_dac_trung task below, not
    # this one) — opt-in only (feature_view_save checkbox in step 5; see
    # poc/feast/registry_api.py for the create-or-update-by-name side of
    # this). Captured from `work` exactly as it stands right here — after
    # feature selection, derived columns, log1p and scaling, but before
    # one-hot/ordinal encoding below — because the user is explicitly
    # naming *this* configured transformation as the thing to save, not
    # some abstraction independent of it (unlike the old fixed
    # sales_orders_features view this replaced, which deliberately used
    # raw, scale-independent values for exactly the opposite reason).
    # "id" in columns (not a schema/table allowlist) is what makes this
    # table-agnostic — any source table with a plain id column works.
    #
    # {} not None: Airflow 3.0.6's Task Execution API rejects the XCom
    # push with a 422 ("Field required") when the returned dict has a
    # bare None value nested inside it — reproduced directly against
    # this stack. An empty dict serializes fine and is equally falsy for
    # luu_tru_dac_trung's own `if not engineered:` check below.
    engineered_features = {}
    if p["feature_view_save"] and "id" in columns:
        engineered_features = {
            "entity_ids": df["id"].tolist(),
            "columns": list(work.columns),
            "dtypes": {c: str(work[c].dtype) for c in work.columns},
            "rows": work.where(pd.notnull(work), None).values.tolist(),
        }

    non_numeric_cols = [c for c in work.columns if c not in numeric_feature_cols]
    encoding = p["transform_encoding"]
    if encoding == "onehot":
        X_df = pd.get_dummies(work, columns=non_numeric_cols, drop_first=False)
    else:
        X_df = work.copy()
        for col in non_numeric_cols:
            X_df[col] = pd.factorize(X_df[col])[0]

    y = df[target]
    print(
        f"Biến đổi & tạo biến: {feature_cols} -> {list(X_df.columns)} "
        f"(encoding={encoding}, scale={scale}, log={log_cols}, derived={[d['name'] for d in derived]})"
    )
    return {
        "X": X_df.values.tolist(),
        "y": y.tolist(),
        "feature_names": list(X_df.columns),
        "engineered_features": engineered_features,
    }


@task(task_id="luu_tru_dac_trung")
def luu_tru_dac_trung(features: dict, **context) -> dict:
    """Step 5's second branch — "store features". Opt-in (the
    feature_view_save/feature_view_name Params, set from step 5's "save
    this transformation as a Feature View" checkbox) and create-or-update
    by name: registers/updates the feature view's schema against
    idma-feast-admin (the one thing in this stack with Feast SDK write
    access — see poc/feast/registry_api.py for why this task itself
    stays HTTP-only, no new heavy dependency here), writes this run's
    rows into that feature view's own Postgres table, then triggers an
    incremental materialization into Feast's online store via
    idma-feast's own feature-server API.

    Runs in parallel with cau_hinh_va_chay (both consume
    transform_and_create_features()'s return value independently, so
    Airflow schedules them side by side) rather than before it — a
    feature-store write failing should never block model
    training/registration.

    No-ops, not an error, when engineered_features is None — i.e. the
    checkbox was off, or the source table has no plain `id` column (see
    that field's own comment in transform_and_create_features)."""
    import json
    import os
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone

    import psycopg2

    engineered = features["engineered_features"]
    if not engineered:
        print("Lưu trữ đặc trưng: bỏ qua (chưa chọn lưu, hoặc bảng không có cột id)")
        # "" not None — same Airflow 3.0.6 XCom-push quirk as
        # engineered_features's own {} vs None above: a bare None nested
        # in the returned dict gets this task's XCom push rejected with a
        # 422. frontend's renderWizDone() only branches on `skipped`, so
        # an empty string here changes nothing observable.
        return {"written_rows": 0, "feature_view": "", "skipped": True}

    name = context["params"]["feature_view_name"]
    columns = engineered["columns"]
    dtypes = engineered["dtypes"]

    # 1. Register/update the feature view's schema first — if this fails
    # (unsupported dtype, bad name), fail loudly here rather than insert
    # rows into a table Feast's registry doesn't know about.
    fv_body = json.dumps(
        {"name": name, "columns": [{"name": c, "dtype": dtypes[c]} for c in columns]}
    ).encode()
    fv_req = urllib.request.Request(
        f"{os.environ['FEAST_ADMIN_URL']}/feature-views",
        data=fv_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(fv_req, timeout=30) as resp:
            fv_result = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        raise ValueError(f"feature view {name!r} registration failed: {err.read().decode(errors='replace')}") from err
    except urllib.error.URLError as err:
        raise ValueError(f"feature view {name!r} registration failed: {err}") from err
    table = fv_result["table"]

    # 2. One "now" for the whole batch — see transform_and_create_features()'s
    # own comment on engineered_features for why this must be wall-clock
    # time, not some business-date column: materialize_incremental's
    # watermark only moves forward, so every row here needs a timestamp
    # at or after whatever the previous run already materialized.
    now = datetime.now(timezone.utc)
    col_list = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * (2 + len(columns)))
    rows = [
        (entity_id, now, *row)
        for entity_id, row in zip(engineered["entity_ids"], engineered["rows"])
    ]

    conn = psycopg2.connect(os.environ["FEAST_DB_DSN"])
    try:
        with conn.cursor() as cur:
            cur.executemany(
                f'INSERT INTO "{table}" (entity_id, event_timestamp, {col_list}) VALUES ({placeholders})',
                rows,
            )
        conn.commit()
    finally:
        conn.close()

    # 3. Materialize into the online store.
    end_ts = now.isoformat()
    mat_body = json.dumps({"end_ts": end_ts, "feature_views": [name]}).encode()
    mat_req = urllib.request.Request(
        f"{os.environ['FEAST_FEATURE_SERVER_URL']}/materialize-incremental",
        data=mat_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(mat_req, timeout=30) as resp:
            resp.read()
    except urllib.error.URLError as err:
        raise ValueError(f"feast materialize-incremental failed: {err}") from err

    print(f"Lưu trữ đặc trưng: {len(rows)} dòng -> {name} ({table}), materialized to {end_ts}")
    return {"written_rows": len(rows), "feature_view": name, "skipped": False}


@task(task_id="cau_hinh_va_chay")
def configure_and_run(features: dict, problem_type: str, **context) -> dict:
    """Step 6 — Cấu hình & chạy (configure hyperparameters and train)."""
    import os

    import mlflow
    from sklearn.model_selection import train_test_split

    p = context["params"]
    model_type = p["model_type"]
    expected = CLASSIFICATION_MODELS if problem_type == "classification" else REGRESSION_MODELS
    if model_type not in expected:
        raise ValueError(f"model_type {model_type!r} doesn't match problem_type={problem_type!r} — expected one of {expected}")

    X_train, X_test, y_train, y_test = train_test_split(
        features["X"], features["y"], test_size=p["test_size"], random_state=p["random_state"]
    )

    if model_type == "random_forest_classifier":
        from sklearn.ensemble import RandomForestClassifier

        model = RandomForestClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"] or None, random_state=p["random_state"])
    elif model_type == "gradient_boosting_classifier":
        from sklearn.ensemble import GradientBoostingClassifier

        model = GradientBoostingClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"] or 3, random_state=p["random_state"])
    elif model_type == "logistic_regression":
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=1000, random_state=p["random_state"])
    elif model_type == "random_forest_regressor":
        from sklearn.ensemble import RandomForestRegressor

        model = RandomForestRegressor(n_estimators=p["n_estimators"], max_depth=p["max_depth"] or None, random_state=p["random_state"])
    elif model_type == "gradient_boosting_regressor":
        from sklearn.ensemble import GradientBoostingRegressor

        model = GradientBoostingRegressor(n_estimators=p["n_estimators"], max_depth=p["max_depth"] or 3, random_state=p["random_state"])
    else:
        from sklearn.linear_model import LinearRegression

        model = LinearRegression()

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(p["experiment_name"])

    with mlflow.start_run() as run:
        model.fit(X_train, y_train)
        mlflow.log_param("problem_type", problem_type)
        mlflow.log_param("model_type", model_type)
        mlflow.log_param("test_size", p["test_size"])
        mlflow.log_param("feature_names", ",".join(features["feature_names"]))
        if "random_forest" in model_type or "gradient_boosting" in model_type:
            mlflow.log_param("n_estimators", p["n_estimators"])
            mlflow.log_param("max_depth", p["max_depth"])
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            input_example=X_train[:5],
            # see train_sample_model.py's same note: pickle, not the mlflow
            # 3.x default skops serializer, because some sklearn internals
            # (RandomForest's tree type) need an explicit skops trust list
            # this lab doesn't configure. Only this lab's own backend ever
            # loads this artifact back.
            serialization_format="pickle",
        )
        run_id = run.info.run_id

    print(f"Cấu hình & chạy: model_type={model_type}, run_id={run_id}")
    return {"run_id": run_id, "X_test": X_test, "y_test": y_test}


@task(task_id="ket_qua")
def show_results(train_result: dict, problem_type: str, **context) -> dict:
    """Step 7 — Kết quả (results): evaluate on the held-out split, apply
    the quality gate, register and optionally promote — same mechanism
    backend/app/mlflow_client.py's "Deploy" button uses for the
    "production" alias."""
    import mlflow
    import mlflow.pyfunc
    from mlflow.tracking import MlflowClient

    p = context["params"]
    run_id = train_result["run_id"]
    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")
    preds = model.predict(train_result["X_test"])
    y_test = train_result["y_test"]

    if problem_type == "classification":
        from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

        metrics = {
            "accuracy": accuracy_score(y_test, preds),
            "precision_macro": precision_score(y_test, preds, average="macro", zero_division=0),
            "recall_macro": recall_score(y_test, preds, average="macro", zero_division=0),
            "f1_macro": f1_score(y_test, preds, average="macro", zero_division=0),
        }
        score = metrics["accuracy"]
    else:
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

        metrics = {
            "r2": r2_score(y_test, preds),
            "mae": mean_absolute_error(y_test, preds),
            "rmse": mean_squared_error(y_test, preds) ** 0.5,
        }
        score = metrics["r2"]

    client = MlflowClient()
    for key, value in metrics.items():
        client.log_metric(run_id, key, value)

    print("Kết quả: " + ", ".join(f"{k}={v:.4f}" for k, v in metrics.items()))

    if score < p["min_score"]:
        print(f"score {score:.4f} < min_score {p['min_score']} — không đăng ký model")
        return {"registered": False, "run_id": run_id, "score": score, "metrics": metrics}

    name = p["registered_model_name"]
    try:
        client.create_registered_model(name)
    except Exception:  # noqa: BLE001 — already exists is the overwhelmingly common case
        pass
    mv = client.create_model_version(name=name, source=f"runs:/{run_id}/model", run_id=run_id)
    print(f"Đã đăng ký '{name}' v{mv.version} (score={score:.4f})")

    if p["auto_promote"]:
        client.set_registered_model_alias(name=name, alias="production", version=mv.version)
        print(f"Đã triển khai '{name}' v{mv.version} vào production")

    return {
        "registered": True,
        "name": name,
        "version": mv.version,
        "run_id": run_id,
        "score": score,
        "metrics": metrics,
        "promoted": p["auto_promote"],
    }


with DAG(
    dag_id="automl_wizard_pipeline",
    description="7-step AutoML wizard: chon bai toan -> ket noi & chon du lieu -> tien xu ly -> chon muc tieu -> bien doi & tao bien -> cau hinh & chay -> ket qua",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["ml", "idma-demo", "automl"],
    params={
        "problem_type": Param(
            "classification",
            type="string",
            enum=["classification", "regression"],
            title="1. Chọn bài toán",
            description="classification or regression.",
        ),
        # schema/table/target_column have no enum= — the wizard UI (see
        # backend/app/main.py's /api/wizard/* + /api/catalog + /api/query)
        # already validated these live against the real catalog/columns
        # before ever submitting, and connect_and_select_data()/
        # select_target() below still check the real pairing against the
        # live database regardless. No static dropdown to keep in sync by
        # hand anymore.
        "schema": Param("sales", type="string", title="2. Kết nối & chọn dữ liệu — schema"),
        "table": Param("orders", type="string", title="2. Kết nối & chọn dữ liệu — table"),
        "preprocess_numeric_columns": Param(
            None, type=["string", "null"], title="3. Tiền xử lý — cast to numeric (optional, comma-separated)"
        ),
        "preprocess_datetime_columns": Param(
            None, type=["string", "null"], title="3. Tiền xử lý — cast to datetime (optional, comma-separated)"
        ),
        "preprocess_normalize_columns": Param(
            None,
            type=["string", "null"],
            title="3. Tiền xử lý — normalize (optional, comma-separated)",
            description="Trim + collapse whitespace + lowercase.",
        ),
        "preprocess_impute_columns": Param(
            None, type=["string", "null"], title="3. Tiền xử lý — impute nulls in (optional, comma-separated)"
        ),
        "preprocess_impute_strategy": Param(
            "mean", type="string", enum=["mean", "median", "mode", "constant"], title="3. Tiền xử lý — impute strategy"
        ),
        "preprocess_impute_value": Param(
            None, type=["string", "null"], title="3. Tiền xử lý — impute constant value (optional)"
        ),
        "target_column": Param("region", type="string", title="4. Chọn mục tiêu"),
        "feature_columns": Param(
            None,
            type=["string", "null"],
            title="5. Biến đổi & tạo biến — feature columns (optional)",
            description="Comma-separated column names. Leave blank for auto (every numeric column except the "
            "target/id). Non-numeric columns named here are encoded per transform_encoding.",
        ),
        "transform_encoding": Param(
            "onehot", type="string", enum=["onehot", "ordinal"], title="5. Biến đổi & tạo biến — categorical encoding"
        ),
        "transform_scale": Param(
            "none", type="string", enum=["none", "standard", "minmax"], title="5. Biến đổi & tạo biến — numeric scaling"
        ),
        "transform_log_columns": Param(
            None,
            type=["string", "null"],
            title="5. Biến đổi & tạo biến — log1p columns (optional, comma-separated)",
        ),
        "transform_derived": Param(
            None,
            type=["string", "null"],
            title="5. Biến đổi & tạo biến — derived columns (optional, JSON)",
            description='JSON array of {"name","col_a","op","col_b"}, op one of sum|diff|product|ratio.',
        ),
        "feature_view_save": Param(
            False,
            type="boolean",
            title="5. Biến đổi & tạo biến — save this transformation as a Feature View",
        ),
        "feature_view_name": Param(
            None,
            type=["string", "null"],
            title="5. Biến đổi & tạo biến — feature view name (required if saving)",
            description="Create-or-update by name, via idma-feast-admin — see luu_tru_dac_trung.",
        ),
        "model_type": Param(
            "random_forest_classifier",
            type="string",
            enum=CLASSIFICATION_MODELS + REGRESSION_MODELS,
            title="6. Cấu hình & chạy — model",
            description="Must match problem_type's family (see the *_classifier / *_regressor suffix).",
        ),
        "test_size": Param(0.2, type="number", minimum=0.05, maximum=0.5, title="6. Cấu hình & chạy — test size"),
        "random_state": Param(42, type="integer", title="6. Cấu hình & chạy — random state"),
        "n_estimators": Param(100, type="integer", minimum=1, maximum=1000, title="6. Cấu hình & chạy — n_estimators"),
        "max_depth": Param(4, type="integer", minimum=0, maximum=50, title="6. Cấu hình & chạy — max_depth (0 = unlimited)"),
        "min_score": Param(
            0.5,
            type="number",
            minimum=0,
            maximum=1,
            title="7. Kết quả — minimum score to register",
            description="accuracy for classification, R² for regression.",
        ),
        "auto_promote": Param(False, type="boolean", title="7. Kết quả — auto-promote to production"),
        "experiment_name": Param(MLFLOW_EXPERIMENT, type="string", title="MLflow experiment"),
        "registered_model_name": Param("automl-wizard-model", type="string", title="Registered model name"),
    },
) as dag:
    problem = choose_problem_type()
    raw = connect_and_select_data()
    preprocessed = preprocess_data(raw)
    targeted = select_target(preprocessed)
    features = transform_and_create_features(targeted)
    trained = configure_and_run(features, problem)
    show_results(trained, problem)
    # Second, independent consumer of `features` — see luu_tru_dac_trung's
    # own docstring for why this runs beside cau_hinh_va_chay rather than
    # before it.
    luu_tru_dac_trung(features)

    # configure_and_run already depends on both `features` and `problem`
    # via the data passed above, but that alone lets Airflow run
    # choose_problem_type() and connect_and_select_data() in parallel
    # (step 1 and step 2 racing each other) since neither consumes the
    # other's output. This forces the plain 1 -> 2 -> ... -> 7 chain the
    # wizard itself presents, matching what a human stepping through it
    # one screen at a time would actually do.
    problem >> raw
