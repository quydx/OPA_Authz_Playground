"""The authz database: the durable system of record for policy data.

Three tables mirror exactly what Casbin's p/g/g2 rules used to hold —
subject/resource/action grants, user-to-group membership, and the
resource tree — plus a fourth for the per-user attributes (region, HR
status) OPA's masking and row-filter rules read. This is the only place
policy data is written; OPA never owns data of its own, it's pushed a
fresh copy of this on every change (see opa_client.push_policy_data).
"""
import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.seed import DEMO_USERS, GROUP_MEMBERSHIP, INITIAL_POLICIES, RESOURCE_ORG, RESOURCE_TREE

AUTHZ_DB_URL = os.environ.get("AUTHZ_DB_URL", "postgresql://poc:pocpass@postgres:5432/authz")

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS policies (
        id SERIAL PRIMARY KEY,
        subject TEXT NOT NULL,
        resource TEXT NOT NULL,
        action TEXT NOT NULL,
        UNIQUE (subject, resource, action)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_membership (
        id SERIAL PRIMARY KEY,
        member TEXT NOT NULL,
        grp TEXT NOT NULL,
        UNIQUE (member, grp)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resource_tree (
        id SERIAL PRIMARY KEY,
        child TEXT NOT NULL,
        parent TEXT NOT NULL,
        UNIQUE (child, parent)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_attributes (
        username TEXT PRIMARY KEY,
        region TEXT NOT NULL,
        is_hr BOOLEAN NOT NULL DEFAULT false,
        org TEXT NOT NULL DEFAULT 'org-001'
    )
    """,
    # Idempotent migration for authz databases seeded before organizations
    # existed — CREATE TABLE above is a no-op on those, this adds the
    # column in place. The default matches every pre-org demo user, who
    # were all implicitly org-001 anyway.
    "ALTER TABLE user_attributes ADD COLUMN IF NOT EXISTS org TEXT NOT NULL DEFAULT 'org-001'",
    """
    CREATE TABLE IF NOT EXISTS resource_org (
        resource TEXT PRIMARY KEY,
        org TEXT NOT NULL
    )
    """,
]


def _connect_with_retry(retries: int = 20, delay: float = 1.5):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            engine = create_engine(AUTHZ_DB_URL, pool_pre_ping=True)
            with engine.connect():
                pass
            return engine
        except OperationalError as err:
            last_err = err
            print(f"[db] Postgres not ready yet (attempt {attempt}/{retries}): {err}")
            time.sleep(delay)
    raise RuntimeError(f"Could not connect to authz database: {last_err}")


engine = _connect_with_retry()


def init_schema() -> None:
    with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(text(stmt))


def seed_if_empty() -> bool:
    """Seed the authz database once. Returns True if seeding happened."""
    with engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM policies")).scalar()
        if count:
            return False
        for member, grp in GROUP_MEMBERSHIP:
            conn.execute(
                text("INSERT INTO group_membership (member, grp) VALUES (:m, :g) ON CONFLICT DO NOTHING"),
                {"m": member, "g": grp},
            )
        for child, parent in RESOURCE_TREE:
            conn.execute(
                text("INSERT INTO resource_tree (child, parent) VALUES (:c, :p) ON CONFLICT DO NOTHING"),
                {"c": child, "p": parent},
            )
        for sub, obj, act in INITIAL_POLICIES:
            conn.execute(
                text("INSERT INTO policies (subject, resource, action) VALUES (:s, :o, :a) ON CONFLICT DO NOTHING"),
                {"s": sub, "o": obj, "a": act},
            )
        for username, info in DEMO_USERS.items():
            conn.execute(
                text(
                    "INSERT INTO user_attributes (username, region, is_hr, org) VALUES (:u, :r, :h, :o) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"u": username, "r": info["region"], "h": info["hr"], "o": info["org"]},
            )
        for resource, org in RESOURCE_ORG.items():
            conn.execute(
                text("INSERT INTO resource_org (resource, org) VALUES (:res, :org) ON CONFLICT DO NOTHING"),
                {"res": resource, "org": org},
            )
    return True


def sync_non_revocable_seed_data() -> None:
    """Reconcile tables nothing in the API ever lets a user edit or revoke,
    so additions to seed.py (a new resource_org entry, a new demo user's
    attributes, a new group membership row) reach an authz database that
    was already seeded before they existed — without the one-shot
    seed_if_empty() gate, which exists specifically so a live grant/revoke
    made through the UI survives a backend restart.

    Deliberately excludes `policies`: those rows ARE revocable via
    /api/policies/revoke, so re-inserting them here on every startup would
    silently undo a live revoke. New rows for INITIAL_POLICIES only reach
    an already-seeded database the same way this whole file recommends in
    README.md — a one-time manual insert, or `docker-compose down -v`.
    """
    with engine.begin() as conn:
        for username, info in DEMO_USERS.items():
            conn.execute(
                text(
                    "INSERT INTO user_attributes (username, region, is_hr, org) VALUES (:u, :r, :h, :o) "
                    "ON CONFLICT (username) DO UPDATE SET org = EXCLUDED.org"
                ),
                {"u": username, "r": info["region"], "h": info["hr"], "o": info["org"]},
            )
        for member, grp in GROUP_MEMBERSHIP:
            conn.execute(
                text("INSERT INTO group_membership (member, grp) VALUES (:m, :g) ON CONFLICT DO NOTHING"),
                {"m": member, "g": grp},
            )
        for child, parent in RESOURCE_TREE:
            conn.execute(
                text("INSERT INTO resource_tree (child, parent) VALUES (:c, :p) ON CONFLICT DO NOTHING"),
                {"c": child, "p": parent},
            )
        for resource, org in RESOURCE_ORG.items():
            conn.execute(
                text(
                    "INSERT INTO resource_org (resource, org) VALUES (:res, :org) "
                    "ON CONFLICT (resource) DO UPDATE SET org = EXCLUDED.org"
                ),
                {"res": resource, "org": org},
            )


def get_policy_data() -> dict:
    """The full policy snapshot — pushed into OPA verbatim after every
    write, and reused as-is for the frontend's Policy inspector panel."""
    with engine.connect() as conn:
        p = conn.execute(text("SELECT subject, resource, action FROM policies ORDER BY id")).fetchall()
        g = conn.execute(text("SELECT member, grp FROM group_membership ORDER BY id")).fetchall()
        g2 = conn.execute(text("SELECT child, parent FROM resource_tree ORDER BY id")).fetchall()
        attrs = conn.execute(text("SELECT username, region, is_hr, org FROM user_attributes")).fetchall()
        res_org = conn.execute(text("SELECT resource, org FROM resource_org ORDER BY resource")).fetchall()
    return {
        "p": [list(row) for row in p],
        "g": [list(row) for row in g],
        "g2": [list(row) for row in g2],
        "attributes": {row.username: {"region": row.region, "hr": row.is_hr, "org": row.org} for row in attrs},
        "resource_org": {row.resource: row.org for row in res_org},
    }


def add_policy(sub: str, obj: str, act: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO policies (subject, resource, action) VALUES (:s, :o, :a) "
                "ON CONFLICT DO NOTHING"
            ),
            {"s": sub, "o": obj, "a": act},
        )
        return result.rowcount > 0


def remove_policy(sub: str, obj: str, act: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM policies WHERE subject = :s AND resource = :o AND action = :a"),
            {"s": sub, "o": obj, "a": act},
        )
        return result.rowcount > 0
