-- Airflow's own metadata database (Dag runs, task instances, connections,
-- variables, ...) — kept on the same Postgres instance as `demo` and
-- `authz` for this POC, but its own database so nothing about how
-- Airflow stores its state overlaps with the authz system of record.
CREATE DATABASE airflow;
