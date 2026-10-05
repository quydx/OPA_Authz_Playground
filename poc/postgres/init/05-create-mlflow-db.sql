-- MLflow's own backend store (experiments, runs, params, metrics, the
-- Model Registry) — same pattern as `authz` and `airflow`: its own
-- database on the shared Postgres instance, never overlapping with the
-- authz system of record or Airflow's metadata.
CREATE DATABASE mlflow;
