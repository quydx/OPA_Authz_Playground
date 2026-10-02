FROM python:3.11-slim

# The full "mlflow" package (not mlflow-skinny) — this container runs the
# tracking server itself, unlike the airflow/backend images which only
# need the client APIs (mlflow-skinny==3.16.1 there — must match this
# major version). psycopg2-binary talks to the Postgres backend store;
# boto3 talks to MinIO as the S3-compatible artifact store.
RUN pip install --no-cache-dir mlflow==3.16.1 psycopg2-binary==2.9.12 boto3==1.43.91

EXPOSE 5000

ENV MLFLOW_DISABLE_AGENT_HINT=1
# Default matches docker-compose's service name ("mlflow"). k8s overrides
# this to its own Service DNS name (see 09-mlflow.yaml) — deliberately an
# env var rather than baked into this shared image, see the flag's own
# comment below for why it has to match wherever this actually runs.
ENV MLFLOW_ALLOWED_HOSTS="mlflow,mlflow:5000,localhost,localhost:5000"

# Backend store and artifact root come from env vars (see docker-compose.yml)
# rather than being baked in, same reasoning as every other service here.
#
# --allowed-hosts: mlflow 3.x rejects any request whose Host header isn't
# on this list (DNS-rebinding protection, defaults to localhost + private
# IPs only) — without it, every call from another pod/container (whose
# Host header is that platform's own DNS name for this service, e.g.
# "mlflow:5000" in docker-compose or "idma-mlflow:5000" in k8s) gets a 403
# "possible DNS rebinding attack detected" even though the request is
# legitimate. Env var (not hardcoded) because that hostname differs
# between docker-compose and k8s, and this image is shared by both.
#
# --workers: mlflow's own default (4) measured at ~2GB RSS under real use
# (verified via `docker stats` against the docker-compose deployment) —
# fine with plenty of headroom locally, but OOM-killed the k8s pod at a
# 1.5Gi limit. ${MLFLOW_WORKERS:-4} keeps 4 as the default and lets a
# memory-constrained deployment (see 09-mlflow.yaml) turn it down.
CMD ["sh", "-c", "mlflow server --host 0.0.0.0 --port 5000 --workers \"${MLFLOW_WORKERS:-4}\" --backend-store-uri \"$MLFLOW_BACKEND_STORE_URI\" --artifacts-destination \"$MLFLOW_ARTIFACT_ROOT\" --serve-artifacts --allowed-hosts \"$MLFLOW_ALLOWED_HOSTS\""]
