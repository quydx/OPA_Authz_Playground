"""End-to-end ML pipeline — extract, split, train, evaluate, quality-gated
register, optional auto-promote. Same iris/sklearn/MLflow story as
train_sample_model, but split into real pipeline stages (rather than one
monolithic task) and driven by Params you fill in on the Trigger UI
("Trigger DAG w/ config") instead of hardcoded constants.

Authorization for this DAG (who can view/trigger/see its logs or code) is
the same OPA-decided story as every other Dag here — see seed.py's grants
for airflow.dag.train_eval_pipeline.

Every XCom value below is a plain JSON-safe dict/list — including the
dataset itself (iris is 150 rows x 4 features, trivially small) — rather
than a model object or numpy array, because Airflow's default XCom
backend serializes through the metadata database and isn't meant to
carry either. The trained model itself never leaves the worker process
that created it: train_model logs it straight to MLflow and passes only
its run_id downstream; evaluate_model and register_if_passing load it
back from MLflow by that id rather than receiving it directly.

sklearn/mlflow are imported inside each task callable rather than at
module level so DAG parsing (which happens in the scheduler and
dag-processor regardless of whether this specific task ever runs) never
needs them importable — only task execution does. Same reasoning as
train_sample_model.
"""
from datetime import datetime

from airflow.sdk import DAG, Param, task

MLFLOW_EXPERIMENT = "idma-demo"

MODEL_TYPES = ["random_forest", "logistic_regression", "gradient_boosting"]


@task
def validate_config(**context):
    """Params are already type/range/enum-checked by the Trigger UI's own
    form (see each Param's constraints below) before a run is even
    created — this step is the pipeline's own explicit record of the
    resolved config in the logs, not a second line of defense."""
    p = context["params"]
    print(
        "Resolved config: "
        f"model_type={p['model_type']!r} test_size={p['test_size']} random_state={p['random_state']} "
        f"n_estimators={p['n_estimators']} max_depth={p['max_depth']} "
        f"min_accuracy={p['min_accuracy']} auto_promote={p['auto_promote']} "
        f"experiment_name={p['experiment_name']!r} registered_model_name={p['registered_model_name']!r}"
    )
    if p["model_type"] not in MODEL_TYPES:
        # Belt-and-suspenders: the Param enum below already prevents this
        # at trigger time, but a DAG run can also be started from the CLI
        # / REST API with an arbitrary conf dict that skips the UI form.
        raise ValueError(f"model_type must be one of {MODEL_TYPES}, got {p['model_type']!r}")


@task
def extract_data() -> dict:
    """Stands in for a real extraction step (e.g. a Trino SELECT against
    sales.* like the other Dags' EmptyOperator placeholders) — the iris
    toy dataset, chosen so this Dag runs without any external dependency
    beyond MLflow. Returns plain lists: the whole point of this function
    existing separately from split_data is to have a real "extract"
    boundary a production version would swap out for an actual query.
    """
    from sklearn.datasets import load_iris

    X, y = load_iris(return_X_y=True)
    return {"X": X.tolist(), "y": y.tolist()}


@task
def split_data(data: dict, **context) -> dict:
    from sklearn.model_selection import train_test_split

    p = context["params"]
    X_train, X_test, y_train, y_test = train_test_split(
        data["X"], data["y"], test_size=p["test_size"], random_state=p["random_state"]
    )
    print(f"split: {len(X_train)} train rows, {len(X_test)} test rows (test_size={p['test_size']})")
    return {"X_train": X_train, "X_test": X_test, "y_train": y_train, "y_test": y_test}


@task
def train_model(split: dict, **context) -> dict:
    import os

    import mlflow

    p = context["params"]
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(p["experiment_name"])

    if p["model_type"] == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        model = RandomForestClassifier(
            n_estimators=p["n_estimators"], max_depth=p["max_depth"] or None, random_state=p["random_state"]
        )
    elif p["model_type"] == "gradient_boosting":
        from sklearn.ensemble import GradientBoostingClassifier

        model = GradientBoostingClassifier(
            n_estimators=p["n_estimators"], max_depth=p["max_depth"] or 3, random_state=p["random_state"]
        )
    else:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=1000, random_state=p["random_state"])

    with mlflow.start_run() as run:
        model.fit(split["X_train"], split["y_train"])
        mlflow.log_param("model_type", p["model_type"])
        mlflow.log_param("test_size", p["test_size"])
        mlflow.log_param("random_state", p["random_state"])
        if p["model_type"] in ("random_forest", "gradient_boosting"):
            mlflow.log_param("n_estimators", p["n_estimators"])
            mlflow.log_param("max_depth", p["max_depth"])
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            input_example=split["X_train"][:5],
            # mlflow 3.x defaults to the skops serializer, which refuses to
            # save some sklearn internals (e.g. RandomForest's tree type)
            # without an explicit trust list. Pickle is fine here — the
            # only thing that ever loads this artifact is this same lab's
            # own backend (see mlflow_client.py), never a third party.
            serialization_format="pickle",
        )
        run_id = run.info.run_id

    print(f"logged run {run_id} (model_type={p['model_type']}) under experiment {p['experiment_name']!r}")
    return {"run_id": run_id}


@task
def evaluate_model(split: dict, train_result: dict, **context) -> dict:
    """Loads the just-trained model back from MLflow by run_id (never
    receives the model object itself via XCom — see the module docstring)
    and scores it against the held-out test split, logging the full
    metric set onto the same run so it shows up next to the model in the
    Models tab and MLflow's own UI."""
    import mlflow
    import mlflow.pyfunc
    from mlflow.tracking import MlflowClient
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

    run_id = train_result["run_id"]
    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")
    preds = model.predict(split["X_test"])

    metrics = {
        "accuracy": accuracy_score(split["y_test"], preds),
        "precision_macro": precision_score(split["y_test"], preds, average="macro", zero_division=0),
        "recall_macro": recall_score(split["y_test"], preds, average="macro", zero_division=0),
        "f1_macro": f1_score(split["y_test"], preds, average="macro", zero_division=0),
    }

    client = MlflowClient()
    for key, value in metrics.items():
        client.log_metric(run_id, key, value)

    print(f"run {run_id} eval: " + ", ".join(f"{k}={v:.4f}" for k, v in metrics.items()))
    return {"run_id": run_id, **metrics}


@task
def register_if_passing(eval_result: dict, **context) -> dict:
    """Quality gate: only registers a new model version — making it show
    up in the backend's Models tab at all — if accuracy clears
    min_accuracy. A run that doesn't pass stays in MLflow as a logged,
    inspectable experiment run, just never becomes a registry version
    anyone can deploy."""
    from mlflow.tracking import MlflowClient

    p = context["params"]
    run_id = eval_result["run_id"]
    accuracy = eval_result["accuracy"]

    if accuracy < p["min_accuracy"]:
        print(f"accuracy {accuracy:.4f} < min_accuracy {p['min_accuracy']} — not registering run {run_id}")
        return {"registered": False, "run_id": run_id, "accuracy": accuracy}

    client = MlflowClient()
    name = p["registered_model_name"]
    try:
        client.create_registered_model(name)
    except Exception:  # noqa: BLE001 — already exists is the overwhelmingly common case
        pass
    mv = client.create_model_version(name=name, source=f"runs:/{run_id}/model", run_id=run_id)
    print(f"registered '{name}' v{mv.version} from run {run_id} (accuracy={accuracy:.4f})")
    return {"registered": True, "name": name, "version": mv.version, "run_id": run_id, "accuracy": accuracy}


@task
def promote_to_production(register_result: dict, **context) -> None:
    """Same mechanism backend/app/mlflow_client.py's deploy_model_version
    uses for the Models tab's own "Deploy" button — setting the
    "production" alias. Only runs if the config asked for it (auto_promote)
    and the quality gate above actually registered a version."""
    p = context["params"]
    if not p["auto_promote"]:
        print("auto_promote=False — leaving the production alias untouched")
        return
    if not register_result["registered"]:
        print("model wasn't registered (missed the accuracy gate) — nothing to promote")
        return

    from mlflow.tracking import MlflowClient

    name, version = register_result["name"], register_result["version"]
    MlflowClient().set_registered_model_alias(name=name, alias="production", version=version)
    print(f"promoted '{name}' v{version} to the production alias")


with DAG(
    dag_id="train_eval_pipeline",
    description="End-to-end ML pipeline: extract -> split -> train -> evaluate -> quality-gated register -> optional promote",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["ml", "idma-demo"],
    params={
        "model_type": Param(
            "random_forest",
            type="string",
            enum=MODEL_TYPES,
            title="Model type",
            description="Which scikit-learn classifier to train.",
        ),
        "test_size": Param(
            0.2,
            type="number",
            minimum=0.05,
            maximum=0.5,
            title="Test size",
            description="Fraction of rows held out for evaluation.",
        ),
        "random_state": Param(
            42,
            type="integer",
            title="Random state",
            description="Seed for the train/test split and the model itself.",
        ),
        "n_estimators": Param(
            100,
            type="integer",
            minimum=1,
            maximum=1000,
            title="n_estimators",
            description="Tree count — only used by random_forest / gradient_boosting.",
        ),
        "max_depth": Param(
            4,
            type="integer",
            minimum=0,
            maximum=50,
            title="max_depth",
            description="Max tree depth — only used by random_forest / gradient_boosting. 0 = unlimited.",
        ),
        "min_accuracy": Param(
            0.8,
            type="number",
            minimum=0,
            maximum=1,
            title="Minimum accuracy to register",
            description="Quality gate — a run scoring below this is logged to MLflow but never registered.",
        ),
        "auto_promote": Param(
            False,
            type="boolean",
            title="Auto-promote to production",
            description="If the model clears the accuracy gate, also point the \"production\" alias at it.",
        ),
        "experiment_name": Param(
            MLFLOW_EXPERIMENT,
            type="string",
            title="MLflow experiment",
        ),
        "registered_model_name": Param(
            "sample-classifier",
            type="string",
            title="Registered model name",
        ),
    },
) as dag:
    data = extract_data()
    split = split_data(data)
    config_check = validate_config()
    trained = train_model(split)
    evaluated = evaluate_model(split, trained)
    registered = register_if_passing(evaluated)
    promoted = promote_to_production(registered)

    config_check >> trained
    evaluated >> registered >> promoted
