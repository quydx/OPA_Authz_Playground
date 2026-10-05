"""Live Airflow demo-user passwords, read straight from the running
apiserver pod's own logs via the Kubernetes API — never stored anywhere,
because there's nothing stable to store. SimpleAuthManager mints a fresh
random password per user every time the apiserver process starts, and
prints each one exactly once to stdout.

Outside Kubernetes there's no in-cluster API to reach for this. On
docker-compose the apiserver is instead seeded with fixed lab passwords
from AIRFLOW_DEMO_PASSWORDS (see poc/docker-compose.yml), and the backend
gets the same JSON value — so it simply serves that. With neither set,
this module reports itself unavailable rather than guessing.
K8S_NAMESPACE is only set by poc/k8s/06-backend.yaml.
"""
import json
import os
import re

K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE")
AIRFLOW_DEMO_PASSWORDS = os.environ.get("AIRFLOW_DEMO_PASSWORDS")
AIRFLOW_APISERVER_LABEL = "app=idma-airflow-apiserver"

_PASSWORD_RE = re.compile(r"Password for user '([^']+)': (\S+)")


def available() -> bool:
    return bool(K8S_NAMESPACE or AIRFLOW_DEMO_PASSWORDS)


def fetch_passwords() -> dict[str, str]:
    """{username: password} parsed from the current apiserver pod's log
    buffer. Empty if unavailable, no pod is up, or the API call fails —
    callers treat that as "show the fallback instructions", not an error."""
    if AIRFLOW_DEMO_PASSWORDS:
        try:
            return json.loads(AIRFLOW_DEMO_PASSWORDS)
        except ValueError:
            return {}
    if not K8S_NAMESPACE:
        return {}

    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except Exception:
        return {}

    v1 = client.CoreV1Api()
    try:
        pods = v1.list_namespaced_pod(K8S_NAMESPACE, label_selector=AIRFLOW_APISERVER_LABEL)
    except Exception:
        return {}

    passwords: dict[str, str] = {}
    for pod in pods.items:
        if pod.status.phase != "Running":
            continue
        try:
            logs = v1.read_namespaced_pod_log(name=pod.metadata.name, namespace=K8S_NAMESPACE)
        except Exception:
            continue
        for match in _PASSWORD_RE.finditer(logs):
            passwords[match.group(1)] = match.group(2)
    return passwords
