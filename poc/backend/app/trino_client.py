import os
import time
import urllib.request

import trino

TRINO_HOST = os.environ.get("TRINO_HOST", "trino")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "postgres")


def wait_for_trino(retries: int = 40, delay: float = 2.0) -> None:
    """Block until Trino's coordinator responds, so the first demo query
    right after `docker compose up` doesn't race a cold start."""
    url = f"http://{TRINO_HOST}:{TRINO_PORT}/v1/info"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    print(f"[trino] coordinator ready after {attempt} attempt(s)")
                    return
        except Exception as err:  # noqa: BLE001
            print(f"[trino] not ready yet (attempt {attempt}/{retries}): {err}")
        time.sleep(delay)
    print("[trino] gave up waiting for coordinator — queries may fail until it's up")


def run_select(user: str, token: str, schema: str, table: str, limit: int = 25):
    """Run a fixed, safe SELECT against exactly one table.

    The POC never accepts free-form SQL from the client — the only thing a
    caller controls is which table to read, and that choice is what OPA
    already authorized. `user` is passed through as the Trino session user
    so it shows up in Trino's own query log too, and so Trino's own OPA
    check (independent of this one) evaluates against the real caller.

    `token` is the same Keycloak access token this backend already
    validated on the incoming API request, forwarded as-is — Trino
    verifies it itself (see trino/config.properties' JWT authenticator)
    rather than trusting the backend's word for who `user` is. Trino
    requires the token's principal (its preferred_username claim) to match
    the session user above, which holds here because each demo user's own
    token is what's forwarded for that user's own query.

    Sent as a raw header rather than trino.auth.JWTAuthentication — that
    class refuses to attach to a plain http:// connection (it assumes
    TLS), which this POC deliberately doesn't run between containers (see
    config.properties' allow-insecure-over-http, set for the same reason).
    Trino itself doesn't care how the header arrived, only that it's a
    valid bearer token.
    """
    conn = trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=user,
        catalog=TRINO_CATALOG,
        schema=schema,
        http_scheme="http",
        http_headers={"Authorization": f"Bearer {token}"},
    )
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{schema}"."{table}" LIMIT {int(limit)}')
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    return columns, rows
