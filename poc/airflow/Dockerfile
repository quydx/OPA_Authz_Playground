FROM apache/airflow:3.0.6

# scikit-learn/numpy/pandas/boto3 already ship in the base image — only
# mlflow itself is missing. mlflow-skinny (not the full "mlflow" package)
# is enough here: this DAG only ever acts as a tracking-server *client*
# (mlflow.start_run, mlflow.sklearn.log_model, ...) and never runs the
# server's own Flask/gunicorn stack, so there's no reason to pull those in
# — or risk them colliding with Airflow's own pinned Flask.
#
# --constraint pins every dependency mlflow-skinny shares with Airflow
# (requests, packaging, protobuf, click, ...) to the exact versions
# Airflow 3.0.6 itself was tested against, so this install can't quietly
# upgrade something Airflow depends on.
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.6/constraints-3.12.txt" \
    -r /requirements.txt
