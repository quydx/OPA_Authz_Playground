"""The backend's own connection to OPA — the same policy engine Trino
calls, just a different rule (`data.app.allow`) shaped for a plain
(user, resource, action) check instead of Trino's request format.

Two directions of traffic:
  - `check()`: the backend asks OPA for a decision, same as Trino does.
  - `push_policy_data()`: after every write to the authz database, the
    backend pushes the fresh snapshot into OPA's in-memory data store
    (`PUT /v1/data/policy_data`) so every enforcement point — this check
    included — sees the change immediately. No poll delay, no cache to
    invalidate.
"""
import json
import os
import urllib.request

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")


def decide(user: str, resource: str, action: str) -> dict:
    """OPA's full answer from data.app: {allow, reason, user_org?,
    resource_org?} — reason says which check failed (see app.rego)."""
    payload = json.dumps({"input": {"user": user, "resource": resource, "action": action}}).encode()
    req = urllib.request.Request(
        f"{OPA_URL}/v1/data/app",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            result = json.loads(resp.read()).get("result") or {}
    except Exception as err:  # noqa: BLE001 — fail closed: any OPA/network error denies the request
        print(f"[opa] check failed, denying by default: {err}")
        return {"allow": False, "reason": "opa_unreachable"}
    result["allow"] = bool(result.get("allow", False))
    return result


def check(user: str, resource: str, action: str) -> bool:
    return decide(user, resource, action)["allow"]


def push_policy_data(data: dict) -> None:
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{OPA_URL}/v1/data/policy_data",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
    except Exception as err:  # noqa: BLE001
        print(f"[opa] failed to push policy data — OPA will serve stale/empty data until this succeeds: {err}")
