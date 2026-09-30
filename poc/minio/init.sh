#!/bin/sh
# Provisions MinIO for the org-isolation demo: one bucket per org, one MinIO
# user per org scoped to that bucket alone by IAM policy, and a few sample
# files uploaded with the root credentials (a provisioning-time action —
# the same shape as a platform admin populating a workspace before handing
# its own scoped credential to whatever reads it day to day).
#
# This is the ONE isolation guarantee in this POC that OPA never touches —
# see README.md's "Storage isolation" section for why that's deliberate.
set -e

mc alias set local "${MINIO_ENDPOINT:-http://minio:9000}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

mc mb --ignore-existing local/org-001
mc mb --ignore-existing local/org-002

cat > /tmp/org-001-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::org-001"]},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": ["arn:aws:s3:::org-001/*"]}
  ]
}
EOF
mc admin user add local org001svc org001SecretKey123 || true
mc admin policy create local org-001-policy /tmp/org-001-policy.json || true
mc admin policy attach local org-001-policy --user org001svc || true

cat > /tmp/org-002-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::org-002"]},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": ["arn:aws:s3:::org-002/*"]}
  ]
}
EOF
mc admin user add local org002svc org002SecretKey456 || true
mc admin policy create local org-002-policy /tmp/org-002-policy.json || true
mc admin policy attach local org-002-policy --user org002svc || true

mc cp --recursive /seed-files/org-001/ local/org-001/
mc cp --recursive /seed-files/org-002/ local/org-002/

# MLflow's artifact store — one bucket, one scoped user, same shape as the
# per-org buckets above (least-privilege credential, not the root user).
# Both the mlflow tracking server and anything logging/loading a model
# (the Airflow training DAG, the backend's model-serving cache) use this
# same credential — see docker-compose.yml's MLFLOW_S3_ENDPOINT_URL /
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars.
mc mb --ignore-existing local/mlflow-artifacts

cat > /tmp/mlflow-artifacts-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::mlflow-artifacts"]},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": ["arn:aws:s3:::mlflow-artifacts/*"]}
  ]
}
EOF
mc admin user add local mlflowsvc mlflowSecretKey789 || true
mc admin policy create local mlflow-artifacts-policy /tmp/mlflow-artifacts-policy.json || true
mc admin policy attach local mlflow-artifacts-policy --user mlflowsvc || true

echo "minio-init: buckets, scoped users, and sample files ready."
