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

OPA holds no data of its own — it's a cache of whatever Postgres (the
authz system of record) last pushed into it. That's fine as long as OPA
and this backend restart together, but they don't always: on 2026-10-13
OPA alone restarted (a k8s node event independent of the backend) and
came back with empty policy_data — every OPA-gated check then failed
closed (denying everything) until someone noticed and manually restarted
the backend to re-push. resync_loop() below exists so that doesn't need
a human: it keeps re-pushing on an interval regardless of whether OPA
actually needed it, so an OPA-only restart self-heals within one
interval instead of silently denying everything indefinitely.
"""
import json
import os
import time
import urllib.request
from typing import Callable

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")


def check(user: str, resource: str, action: str) -> bool:
    payload = json.dumps({"input": {"user": user, "resource": resource, "action": action}}).encode()
    req = urllib.request.Request(
        f"{OPA_URL}/v1/data/app/allow",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read())
            return bool(body.get("result", False))
    except Exception as err:  # noqa: BLE001 — fail closed: any OPA/network error denies the request
        print(f"[opa] check failed, denying by default: {err}")
        return False


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


def resync_loop(get_data: Callable[[], dict], interval_s: int = 60) -> None:
    """Runs forever on a daemon thread (see main.py's on_startup) —
    re-pushes the current policy snapshot into OPA every `interval_s`
    seconds, unconditionally, regardless of whether OPA actually lost its
    data. Cheap enough at this lab's scale to not bother checking first;
    push_policy_data() already swallows its own errors, so one failed
    push (OPA mid-restart) just gets retried next interval rather than
    killing this loop. `get_data` is a thunk (db.get_policy_data) so this
    module never has to import db directly."""
    while True:
        time.sleep(interval_s)
        push_policy_data(get_data())
