FROM python:3.11-slim

# Version pinned to the one actually verified against this repo's
# feature_store.yaml/features.py during implementation (real `feast
# apply` + `feast materialize-incremental` + `feast serve` all exercised
# against a throwaway Postgres) — same "pin exactly what was tested"
# reasoning as mlflow/Dockerfile's mlflow==3.16.1.
#
# +grpcio: not needed by `feast apply`/`feast serve` (both ran fine
# without it), but `feast ui` imports feast.registry_server unconditionally
# to mount its REST-over-registry routes and fails at startup with
# "ModuleNotFoundError: No module named 'grpc'" without this extra —
# verified by running `feast ui` against this image before adding it.
RUN pip install --no-cache-dir "feast[postgres,grpcio]==0.66.0"

# repo/ also holds registry_api.py — idma-feast-admin's own entrypoint
# (see docker-compose.yml's/11-feast.yaml's command override for that
# service). One image, three commands: this file's own CMD (feature
# server), `feast ui` (idma-feast-ui), `python registry_api.py`
# (idma-feast-admin) — no separate Dockerfile needed for either.
COPY repo/ /app/repo/
WORKDIR /app/repo

EXPOSE 6566
EXPOSE 8888
EXPOSE 6568

# FEAST_DB_HOST differs between docker-compose ("postgres") and k8s
# ("idma-postgres") — see feature_store.yaml's own comment; set here only
# as a docker-compose-flavored default, same pattern as mlflow/Dockerfile's
# MLFLOW_ALLOWED_HOSTS default. k8s overrides it (see ../k8s/11-feast.yaml).
ENV FEAST_DB_HOST=postgres

# `feast apply` is idempotent (safe to re-run against an already-applied
# registry — verified) and needs Postgres reachable, so it runs as this
# container's own startup step rather than a separate init
# service/Job — simpler than mirroring airflow-init's split here, and
# nothing else needs to run between apply and serve. `feast serve` is the
# actual long-running process; `exec` hands it PID 1 so container
# stop/restart signals reach it directly.
CMD ["sh", "-c", "feast apply && exec feast serve -h 0.0.0.0 -p 6566"]
