"""Demo DAG — trains a small scikit-learn classifier and logs/registers it
in MLflow (see mlflow/ and docker-compose.yml's `mlflow` service), so the
backend's Models tab has something real to list and deploy.

Authorization for this DAG (who can view/trigger/see its logs or code) is
the same OPA-decided story as every other Dag here — see seed.py's grants
for airflow.dag.train_sample_model.

mlflow/sklearn are imported inside the task callable rather than at module
level so DAG parsing (which happens in the scheduler and dag-processor
regardless of whether this specific task ever runs) never needs them
importable — only task execution does.
"""
from datetime import datetime

from airflow.sdk import DAG, task

MLFLOW_EXPERIMENT = "idma-demo"
REGISTERED_MODEL_NAME = "sample-classifier"


@task
def train_and_log_model():
    import os

    import mlflow
    import mlflow.sklearn
    from sklearn.datasets import load_iris
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score
    from sklearn.model_selection import train_test_split

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    n_estimators = 100
    max_depth = 4

    X, y = load_iris(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    with mlflow.start_run():
        model = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, random_state=42)
        model.fit(X_train, y_train)
        accuracy = accuracy_score(y_test, model.predict(X_test))

        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_param("max_depth", max_depth)
        mlflow.log_metric("accuracy", accuracy)
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            registered_model_name=REGISTERED_MODEL_NAME,
            input_example=X_train[:5],
            # mlflow 3.x defaults to the skops serializer, which refuses to
            # save RandomForest's internal sklearn.tree._tree.Tree type
            # without an explicit trust list. Pickle is fine here — the
            # only thing that ever loads this artifact is this same
            # lab's own backend (see mlflow_client.py), never a
            # third party's file.
            serialization_format="pickle",
        )

    print(f"Trained and registered '{REGISTERED_MODEL_NAME}': accuracy={accuracy:.4f}")


with DAG(
    dag_id="train_sample_model",
    description="Trains a sample iris classifier and registers it in MLflow",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["ml", "idma-demo"],
) as dag:
    train_and_log_model()
