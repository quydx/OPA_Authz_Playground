-- Feast's own registry/offline-store/online-store database — same "one
-- instance, separate database" pattern as authz/airflow/mlflow. No tables
-- created here any more: every feature view now gets its own table
-- (fv_<name>), created on demand by idma-feast-admin (see
-- poc/feast/registry_api.py) the first time a wizard user checks "save
-- this transformation as a Feature View" and names it — not a fixed
-- schema known ahead of time, so there's nothing to create up front.
CREATE DATABASE feast;
