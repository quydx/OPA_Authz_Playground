"""The backend's connection to MinIO — deliberately different in shape
from opa_client.py. Storage isolation isn't a policy decision; it's a
credential boundary. This module never asks OPA anything — it holds one
S3 client per organization, built from that organization's own MinIO
user (see seed.py's ORG_STORAGE), and only ever uses an org's own client
against buckets that client itself reports.

Bucket names are NOT hardcoded here or in seed.py. list_buckets() calls
MinIO's own ListBuckets API with the org's own credential — MinIO
filters that response to buckets the credential actually has some
access to (verified empirically: a user scoped to a single bucket via
IAM policy gets back exactly that one bucket, not an error and not
every bucket in the system). Whatever comes back *is* the org's bucket
list — real, live, and impossible to get wrong by editing the wrong
config file.

cross_org_attempt() is the one place this module uses the MinIO root
credential, and only for a read-only ListBuckets call to find a bucket
that exists but isn't in the acting org's own list — it never uses the
root credential to read or write object data. It then tries that
bucket with the acting org's own (non-root) credentials, on purpose, to
demonstrate the denial is real and structural rather than something
this backend simply chooses not to do.
"""
import os

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.seed import ORG_STORAGE

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "pocadmin")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "pocadminpass")


def _client(access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",
    )


def _client_for(org: str):
    creds = ORG_STORAGE[org]
    return _client(creds["access_key"], creds["secret_key"])


def _root_client():
    return _client(MINIO_ROOT_USER, MINIO_ROOT_PASSWORD)


def list_buckets(org: str) -> list[str]:
    """The org's own bucket list, straight from MinIO's ListBuckets API —
    not a lookup in seed.py. This is the actual answer to "what buckets
    can this org see", not an assumption about how init.sh named them."""
    client = _client_for(org)
    resp = client.list_buckets()
    return [b["Name"] for b in resp.get("Buckets", [])]


def list_files(org: str) -> dict[str, list[dict]]:
    """Every bucket the org's own credentials can see, and every file in
    each — all discovered live from MinIO. Currently one bucket per org
    in this demo, but nothing here assumes that stays true."""
    client = _client_for(org)
    out: dict[str, list[dict]] = {}
    for bucket in list_buckets(org):
        resp = client.list_objects_v2(Bucket=bucket)
        out[bucket] = [
            {
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            }
            for obj in resp.get("Contents", [])
        ]
    return out


def get_file(org: str, bucket: str, key: str) -> str:
    client = _client_for(org)
    resp = client.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary file, {len(body)} bytes — not previewable as text>"


def cross_org_attempt(acting_org: str) -> dict:
    """Find a bucket that exists but isn't in acting_org's own list (via
    the root credential, read-only, ListBuckets only), then try to read
    it using acting_org's own scoped credentials. Expected to fail."""
    own_buckets = set(list_buckets(acting_org))
    all_buckets = [b["Name"] for b in _root_client().list_buckets().get("Buckets", [])]
    candidates = [b for b in all_buckets if b not in own_buckets]
    if not candidates:
        return {"denied": None, "bucket": None, "message": "No other bucket exists to test against."}
    target_bucket = candidates[0]

    client = _client_for(acting_org)
    try:
        resp = client.list_objects_v2(Bucket=target_bucket)
        return {
            "denied": False,
            "bucket": target_bucket,
            "message": f"Unexpectedly succeeded — got {len(resp.get('Contents', []))} object(s). This should not happen.",
        }
    except ClientError as err:
        error = err.response.get("Error", {})
        return {
            "denied": True,
            "bucket": target_bucket,
            "code": error.get("Code", "Unknown"),
            "message": error.get("Message", str(err)),
        }
