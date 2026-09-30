FROM python:3.11-slim

# The full "mlflow" package (not mlflow-skinny) — this container runs the
# tracking server itself, unlike the airflow/backend images which only
# need the client APIs (mlflow-skinny==3.16.1 there — must match this
# major version). psycopg2-binary talks to the Postgres backend store;
# boto3 talks to MinIO as the S3-compatible artifact store.
RUN pip install --no-cache-dir mlflow==3.16.1 psycopg2-binary==2.9.12 boto3==1.43.91

EXPOSE 5000

ENV MLFLOW_DISABLE_AGENT_HINT=1

# Backend store and artifact root come from env vars (see docker-compose.yml)
# rather than being baked in, same reasoning as every other service here.
#
# --allowed-hosts: mlflow 3.x rejects any request whose Host header isn't
# on this list (DNS-rebinding protection, defaults to localhost + private
# IPs only) — without it, every call from the airflow/backend containers
# (Host: mlflow:5000, on Docker's internal network) gets a 403 "possible
# DNS rebinding attack detected" even though the request is legitimate.
CMD ["sh", "-c", "mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri \"$MLFLOW_BACKEND_STORE_URI\" --artifacts-destination \"$MLFLOW_ARTIFACT_ROOT\" --serve-artifacts --allowed-hosts 'mlflow,mlflow:5000,localhost,localhost:5000'"]
