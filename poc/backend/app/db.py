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
    # Same idea, for the Users tab (see main.py's /api/users CRUD): display
    # name and job title, previously only ever held in seed.py's DEMO_USERS
    # dict and never persisted for a user created live through the UI.
    "ALTER TABLE user_attributes ADD COLUMN IF NOT EXISTS label TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE user_attributes ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''",
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
                    "INSERT INTO user_attributes (username, label, title, region, is_hr, org) "
                    "VALUES (:u, :l, :t, :r, :h, :o) ON CONFLICT DO NOTHING"
                ),
                {
                    "u": username,
                    "l": info["label"],
                    "t": info["title"],
                    "r": info["region"],
                    "h": info["hr"],
                    "o": info["org"],
                },
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

    `user_attributes` rows for the DEMO_USERS keys are the same story now
    that the Users tab (see main.py's /api/users CRUD) lets org/region/HR/
    label/title be edited live for any user, including the five seed ones
    — ON CONFLICT DO NOTHING here, same reasoning as the `policies`
    exclusion above: unconditionally re-applying seed.py's values on every
    startup would silently undo a live edit to alice/bob/carol/dave/erin's
    row. A *new* key added to DEMO_USERS in seed.py still reaches an
    already-seeded database fine — there's no existing row for it to
    conflict with.
    """
    with engine.begin() as conn:
        for username, info in DEMO_USERS.items():
            conn.execute(
                text(
                    "INSERT INTO user_attributes (username, label, title, region, is_hr, org) "
                    "VALUES (:u, :l, :t, :r, :h, :o) ON CONFLICT (username) DO NOTHING"
                ),
                {
                    "u": username,
                    "l": info["label"],
                    "t": info["title"],
                    "r": info["region"],
                    "h": info["hr"],
                    "o": info["org"],
                },
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


# ---------------------------------------------------------------------
# Users CRUD (see main.py's /api/users) — this table, not seed.py's
# DEMO_USERS dict, is the runtime source of truth for "does this user
# exist" once the backend has started, same as the module docstring
# above always intended for the rest of policy_data.
# ---------------------------------------------------------------------


def known_subjects() -> list[str]:
    """Every subject a grant/revoke form can target: every user this app
    knows about (not just the five seeded ones) plus every group name in
    use — see main.py's /api/policy-options and /api/airflow/policy-options."""
    with engine.connect() as conn:
        usernames = [row[0] for row in conn.execute(text("SELECT username FROM user_attributes ORDER BY username"))]
        groups = sorted({row[0] for row in conn.execute(text("SELECT DISTINCT grp FROM group_membership"))})
    return usernames + groups


def list_users() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT username, label, title, region, is_hr, org FROM user_attributes ORDER BY username")
        ).fetchall()
        g = conn.execute(text("SELECT member, grp FROM group_membership")).fetchall()
    groups_by_user: dict[str, list[str]] = {}
    for member, grp in g:
        groups_by_user.setdefault(member, []).append(grp)
    return [
        {
            "id": row.username,
            "label": row.label,
            "title": row.title,
            "region": row.region,
            "hr": row.is_hr,
            "org": row.org,
            "groups": groups_by_user.get(row.username, []),
        }
        for row in rows
    ]


def get_user(username: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT username, label, title, region, is_hr, org FROM user_attributes WHERE username = :u"),
            {"u": username},
        ).first()
        if not row:
            return None
        groups = [
            r[0]
            for r in conn.execute(text("SELECT grp FROM group_membership WHERE member = :u"), {"u": username})
        ]
    return {
        "id": row.username,
        "label": row.label,
        "title": row.title,
        "region": row.region,
        "hr": row.is_hr,
        "org": row.org,
        "groups": groups,
    }


def create_user_attributes(username: str, label: str, title: str, region: str, is_hr: bool, org: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_attributes (username, label, title, region, is_hr, org) "
                "VALUES (:u, :l, :t, :r, :h, :o)"
            ),
            {"u": username, "l": label, "t": title, "r": region, "h": is_hr, "o": org},
        )


def update_user_attributes(username: str, label: str, title: str, region: str, is_hr: bool, org: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE user_attributes SET label = :l, title = :t, region = :r, is_hr = :h, org = :o "
                "WHERE username = :u"
            ),
            {"u": username, "l": label, "t": title, "r": region, "h": is_hr, "o": org},
        )
        return result.rowcount > 0


def set_user_groups(username: str, groups: list[str]) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM group_membership WHERE member = :u"), {"u": username})
        for grp in groups:
            conn.execute(
                text("INSERT INTO group_membership (member, grp) VALUES (:u, :g) ON CONFLICT DO NOTHING"),
                {"u": username, "g": grp},
            )


def delete_user_attributes(username: str) -> bool:
    """Cascades to the rows only this username could own: its group
    memberships and any direct policy grants naming it as subject. Group
    grants (subject = a group name) are untouched — those belong to the
    group, not this one member of it."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM policies WHERE subject = :u"), {"u": username})
        conn.execute(text("DELETE FROM group_membership WHERE member = :u"), {"u": username})
        result = conn.execute(text("DELETE FROM user_attributes WHERE username = :u"), {"u": username})
        return result.rowcount > 0
