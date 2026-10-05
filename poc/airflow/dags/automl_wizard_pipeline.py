"""Configurable 8-step AutoML wizard pipeline — mirrors the iHub product's
own wizard flow (Chọn bài toán -> Kết nối dữ liệu -> Chọn dữ liệu -> Làm
sạch -> Chọn mục tiêu -> Tạo biến -> Cấu hình & chạy -> Kết quả) as eight
Airflow tasks, each filled in from the Trigger UI's config form (see the
DAG's `params` below) rather than hardcoded.

Deliberate simplification: steps 2-3 (connect/select data) read straight
from the `demo` Postgres database with its own psycopg2 connection,
*not* through Trino/OPA like the frontend's Data Catalog tab does. An
Airflow DAG run has no logged-in Keycloak user attached to it — there's
no per-request bearer token to forward the way backend/app/trino_client.py
does — so there's no "caller" for OPA to evaluate a grant against. Same
reasoning as train_sample_model/train_eval_pipeline never querying Trino
at all; this one at least reads the real demo.sales/demo.hr tables
instead of a toy dataset, but the read bypasses the authz layer entirely.
A real version of this wizard would need a service-identity story for
Airflow (e.g. a dedicated "airflow" OPA subject) — out of scope here.

Authorization for the Dag itself (who can view/trigger/see its logs or
code) is still the normal OPA-decided story — see seed.py's grants for
airflow.dag.automl_wizard_pipeline.

Heavy imports (psycopg2, pandas, sklearn, mlflow) stay inside each task
callable rather than at module level, same reasoning as every other Dag
here: DAG parsing happens in the scheduler and dag-processor regardless
of whether a given task ever runs, and shouldn't need them importable.
"""
from datetime import datetime

from airflow.sdk import DAG, Param, task

# Same demo-only credential already documented in the frontend's
# Credentials tab — this is the `demo` database the `sales`/`hr`/
# `org2_sales` schemas Trino also queries live in, read here directly.
DEMO_DB_DSN = "postgresql://poc:pocpass@idma-postgres:5432/demo"

MLFLOW_EXPERIMENT = "idma-demo"

CATALOG = {
    "sales": ["customers", "orders", "products"],
    "hr": ["employees", "salaries"],
    "org2_sales": ["customers", "orders"],
}

# Dropdown options for the "table" and "target_column" Params below.
# Airflow's Trigger UI renders Params as a single static form — there's
# no cross-field reactivity (e.g. "table" options narrowing to match
# whichever "schema" was picked), so these are the union across every
# table/column in CATALOG, not a schema-aware or table-aware list. A
# combination that doesn't actually exist (e.g. schema=hr + table=orders,
# or table=orders + target_column=department) is still caught — same as
# before this change — by select_data()/select_target()'s own runtime
# checks against the live database, which raise a clear error naming
# what *is* valid for the actual schema/table chosen. Hardcoded here
# (not introspected at DAG-parse time) for the same reason every other
# heavy/networked import in this file stays inside its task callable —
# dag-processor re-parses this file on every refresh cycle, and
# shouldn't need a live database connection just to render a dropdown.
# Keep in sync with CATALOG and the actual table schemas by hand if
# either changes.
ALL_TABLES = sorted({table for tables in CATALOG.values() for table in tables})
ALL_COLUMNS = sorted(
    {
        "id", "name", "email", "region",  # customers
        "customer_id", "amount", "order_date",  # orders
        "price",  # products
        "department", "hire_date",  # employees
        "employee_id", "salary", "currency",  # salaries
    }
)

CLASSIFICATION_MODELS = ["random_forest_classifier", "logistic_regression", "gradient_boosting_classifier"]
REGRESSION_MODELS = ["random_forest_regressor", "linear_regression", "gradient_boosting_regressor"]


@task(task_id="chon_bai_toan")
def choose_problem_type(**context) -> str:
    """Step 1 — Chọn bài toán (choose the problem type)."""
    problem_type = context["params"]["problem_type"]
    if problem_type not in ("classification", "regression"):
        # The Param enum below already prevents this from the Trigger UI
        # form — this guards the CLI/REST path, which can pass an
        # arbitrary conf dict that skips the form entirely.
        raise ValueError(f"problem_type must be 'classification' or 'regression', got {problem_type!r}")
    print(f"Chọn bài toán: {problem_type}")
    return problem_type


@task(task_id="ket_noi_du_lieu")
def connect_data_source() -> dict:
    """Step 2 — Kết nối dữ liệu (connect to the data source). Just the
    `demo` Postgres database in this lab — validates connectivity and
    hands back the schema/table catalog live from information_schema,
    rather than trusting the module-level CATALOG constant to still
    match what's actually in the database."""
    import psycopg2

    conn = psycopg2.connect(DEMO_DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(%s) ORDER BY table_schema, table_name",
                (list(CATALOG.keys()),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    catalog: dict[str, list[str]] = {}
    for schema, table in rows:
        catalog.setdefault(schema, []).append(table)
    print(f"Kết nối dữ liệu OK — {sum(len(t) for t in catalog.values())} bảng trong {len(catalog)} schema")
    return catalog


@task(task_id="chon_du_lieu")
def select_data(catalog: dict, **context) -> dict:
    """Step 3 — Chọn dữ liệu (select which table to use)."""
    import psycopg2

    p = context["params"]
    schema, table = p["schema"], p["table"]
    if schema not in catalog:
        raise ValueError(f"unknown schema {schema!r} — available: {sorted(catalog)}")
    if table not in catalog[schema]:
        raise ValueError(f"unknown table '{schema}.{table}' — available in {schema}: {sorted(catalog[schema])}")

    conn = psycopg2.connect(DEMO_DB_DSN)
    try:
        with conn.cursor() as cur:
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
    print(f"Chọn dữ liệu: {schema}.{table} — {len(rows)} dòng, {len(columns)} cột: {columns}")
    return {"columns": columns, "rows": rows}


@task(task_id="lam_sach")
def clean_data(dataset: dict, **context) -> dict:
    """Step 4 — Làm sạch (clean the data)."""
    p = context["params"]
    columns, rows = dataset["columns"], dataset["rows"]
    before = len(rows)

    if p["drop_nulls"]:
        rows = [r for r in rows if all(v is not None for v in r)]
    if p["drop_duplicates"]:
        seen = set()
        deduped = []
        for r in rows:
            key = tuple(r)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        rows = deduped

    print(f"Làm sạch: {before} -> {len(rows)} dòng (drop_nulls={p['drop_nulls']}, drop_duplicates={p['drop_duplicates']})")
    # 4, not some more typical-looking round number like 10 or 50: this
    # lab's own demo tables are deliberately tiny (sales.orders has 6
    # rows total) — the real floor is "enough for train_test_split to
    # leave at least 1 row in both halves even at max test_size (0.5)",
    # not a realistic training-set size.
    if len(rows) < 4:
        raise ValueError(f"only {len(rows)} rows left after cleaning — too few to train on")
    return {"columns": columns, "rows": rows}


@task(task_id="chon_muc_tieu")
def select_target(dataset: dict, **context) -> dict:
    """Step 5 — Chọn mục tiêu (select the target column)."""
    p = context["params"]
    columns = dataset["columns"]
    target = p["target_column"]
    if target not in columns:
        raise ValueError(f"target_column '{target}' not found — available columns: {columns}")
    print(f"Chọn mục tiêu: {target}")
    return {"columns": columns, "rows": dataset["rows"], "target_column": target}


@task(task_id="tao_bien")
def create_features(dataset: dict, **context) -> dict:
    """Step 6 — Tạo biến (feature creation). Empty feature_columns means
    "auto": every numeric, non-id, non-target column. An explicit list
    may also name text columns — those get one-hot encoded here rather
    than rejected, since several of this lab's own demo columns
    (region, department, ...) are exactly this shape."""
    import pandas as pd

    p = context["params"]
    columns, rows, target = dataset["columns"], dataset["rows"], dataset["target_column"]
    df = pd.DataFrame(rows, columns=columns)

    # Optional — None when left blank on the Trigger UI form (see the
    # Param's nullable type below), same "empty means auto" meaning as
    # the old plain-string default.
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

    X = pd.get_dummies(df[feature_cols], drop_first=False)
    y = df[target]

    print(f"Tạo biến: {feature_cols} -> {list(X.columns)} ({len(X.columns)} cột sau one-hot)")
    return {"X": X.values.tolist(), "y": y.tolist(), "feature_names": list(X.columns)}


@task(task_id="cau_hinh_va_chay")
def configure_and_run(features: dict, problem_type: str, **context) -> dict:
    """Step 7 — Cấu hình & chạy (configure hyperparameters and train)."""
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
    """Step 8 — Kết quả (results): evaluate on the held-out split, apply
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
    description="8-step AutoML wizard: chon bai toan -> ket noi du lieu -> chon du lieu -> lam sach -> chon muc tieu -> tao bien -> cau hinh & chay -> ket qua",
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
        "schema": Param(
            "sales",
            type="string",
            enum=list(CATALOG.keys()),
            title="3. Chọn dữ liệu — schema",
        ),
        "table": Param(
            "orders",
            type="string",
            enum=ALL_TABLES,
            title="3. Chọn dữ liệu — table",
            description="Must actually exist under the chosen schema (e.g. hr has no 'orders') — "
            "every table name across every schema is listed here since the form can't filter "
            "this dropdown by the schema you picked above; select_data checks the real pairing.",
        ),
        "drop_nulls": Param(True, type="boolean", title="4. Làm sạch — drop rows with any null"),
        "drop_duplicates": Param(True, type="boolean", title="4. Làm sạch — drop duplicate rows"),
        "target_column": Param(
            "region",
            type="string",
            enum=ALL_COLUMNS,
            title="5. Chọn mục tiêu",
            description="Must actually exist in the selected table — every column name across every table is "
            "listed here since the form can't filter this dropdown by the table you picked above; "
            "select_target checks the real pairing.",
        ),
        "feature_columns": Param(
            None,
            type=["string", "null"],
            title="6. Tạo biến — feature columns (optional)",
            description="Comma-separated column names. Leave blank for auto (every numeric column except the "
            "target/id). Non-numeric columns named here are one-hot encoded.",
        ),
        "model_type": Param(
            "random_forest_classifier",
            type="string",
            enum=CLASSIFICATION_MODELS + REGRESSION_MODELS,
            title="7. Cấu hình & chạy — model",
            description="Must match problem_type's family (see the *_classifier / *_regressor suffix).",
        ),
        "test_size": Param(0.2, type="number", minimum=0.05, maximum=0.5, title="7. Cấu hình & chạy — test size"),
        "random_state": Param(42, type="integer", title="7. Cấu hình & chạy — random state"),
        "n_estimators": Param(100, type="integer", minimum=1, maximum=1000, title="7. Cấu hình & chạy — n_estimators"),
        "max_depth": Param(4, type="integer", minimum=0, maximum=50, title="7. Cấu hình & chạy — max_depth (0 = unlimited)"),
        "min_score": Param(
            0.5,
            type="number",
            minimum=0,
            maximum=1,
            title="8. Kết quả — minimum score to register",
            description="accuracy for classification, R² for regression.",
        ),
        "auto_promote": Param(False, type="boolean", title="8. Kết quả — auto-promote to production"),
        "experiment_name": Param(MLFLOW_EXPERIMENT, type="string", title="MLflow experiment"),
        "registered_model_name": Param("automl-wizard-model", type="string", title="Registered model name"),
    },
) as dag:
    problem = choose_problem_type()
    catalog = connect_data_source()
    raw = select_data(catalog)
    cleaned = clean_data(raw)
    targeted = select_target(cleaned)
    features = create_features(targeted)
    trained = configure_and_run(features, problem)
    show_results(trained, problem)

    # configure_and_run already depends on both `features` and `problem`
    # via the data passed above, but that alone lets Airflow run
    # choose_problem_type() and connect_data_source() in parallel (step 1
    # and step 2 racing each other) since neither consumes the other's
    # output. This forces the plain 1 -> 2 -> ... -> 8 chain the wizard
    # itself presents, matching what a human stepping through it one
    # screen at a time would actually do.
    problem >> catalog
