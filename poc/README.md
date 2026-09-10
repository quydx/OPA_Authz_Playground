# OPA Access Control — Minimal POC

A trimmed-down, runnable version of the IDMA authorization model: **Trino +
Airflow + Postgres + OPA + MinIO**, with a FastAPI backend and a small
static frontend to drive it. One policy engine (OPA), backed by one system
of record (a Postgres `authz` database), enforced at **three independent
points**:

- **The backend** — a plain `(user, resource, action)` check before it
  opens a Trino connection at all.
- **Trino itself** — the same policy data, natively, during query
  planning: table-level allow/deny, column masking, and row filtering.
  This is what makes the second point matter — it applies to *any* client
  that reaches Trino, not just traffic that happens to go through the
  backend.
- **Airflow itself** — via a custom Auth Manager (AIP-56) that swaps
  Airflow's own authorization engine for one that calls OPA on every Dag
  action: view, trigger, view logs, view code. Same policy data, same
  shared matching logic, no proxy in front of Airflow.

Plus a fourth guarantee that's deliberately **not** OPA-mediated —
**storage isolation via MinIO's own IAM** — see "Storage isolation" below
for why that's the honest design, not an inconsistency.

## Architecture

```
 frontend (nginx, static)  →  backend (FastAPI)  →  OPA (app.allow)
                                      │        ↘
                                      │         PUT policy_data on every write
                                      │              ↓
                                      ├──→  Trino    →  OPA (trino.allow, rowFilters, columnMask)
                                      │        │
                                      │        └──→  demo DB (Postgres)
                                      │
                                      └──→  Airflow  →  OPA (airflow.allow)
                                             (opa_auth_manager, AIP-56)

 authz DB (Postgres) ← the durable system of record; OPA holds a live
                         in-memory mirror, pushed on every change.
```

Nothing here is a "Casbin-style" library embedded anywhere — OPA is the
only decision engine, called three times: once by the backend
(`app.rego`), once by Trino (`trino.rego`), once by Airflow's own Auth
Manager (`airflow.rego`), all three delegating to shared logic in
`authz.rego`. See `opa/policies/`.

## Why three enforcement points instead of one

A gateway-only check (just the backend) can't stop someone who connects
straight to Trino — CLI, JDBC, a BI tool — from skipping it entirely, and
it can't stop someone logging into Airflow's own UI and triggering a Dag
directly either. A Trino-only check can't express things the app layer
cares about (which workspace a user can see, whether they can create a
catalog, etc.) and isn't where you'd want every authorization decision
for every backend service to live. So: the backend still gates its own
request path, and Trino and Airflow each independently gate their own —
same policy data, no duplicated logic, no gap between any of them.

The two engine-native points aren't the same *shape*, though — worth
being precise about, since it's easy to assume "native" always means
the same mechanism:

- **Trino** has a pluggable `SystemAccessControl` SPI that the
  `trino-opa` connector implements out of the box — `trino.rego` is
  consumed by an authorizer someone else already wrote.
- **Airflow** has a pluggable `BaseAuthManager` SPI (AIP-56), but no
  ready-made OPA implementation ships for it — `opa_auth_manager` (see
  `airflow/plugins/`) is a small custom class this POC wrote, subclassing
  Airflow's own `SimpleAuthManager` and overriding only `is_authorized_dag`
  to call OPA.

Both are still "native" in the sense that matters: the check happens
*inside* the engine's own request path, not in a reverse proxy sitting in
front of it. Contrast that with a hypothetical engine with no pluggable
authorization interface at all (Apache Polaris's Iceberg REST catalog is
one real example) — there, the only honest option left is a purpose-built
gate in front of it that calls OPA before forwarding the request, because
there's no hook to plug into.

## How data flows without duplication

Postgres (`authz` database) is the only place policy is *written*. OPA
never owns data — it's pushed a full snapshot via the Data API
(`PUT /v1/data/policy_data`):

- once on every backend startup, and
- **synchronously, inside the same request handler**, after every
  `/api/policies/grant` or `/api/policies/revoke` call.

That second part is what keeps the live grant/revoke demo feeling
instant — by the time the frontend's fetch resolves, OPA already has the
new data. No polling, no cache to invalidate, no propagation delay.

`opa/policies/authz.rego` holds the shared matching logic — subject
matches if it's the user themselves or a group they belong to; resource
matches if it's the object itself or its parent in the resource tree
(kept to one hop; see the file's comments on why Rego doesn't allow
unbounded recursion here). Same shape as a Casbin `g`/`g2` matcher, just
expressed in Rego and backed by Postgres instead of a bundled adapter:

```
allow(user, resource, action) :=
    some policy (sub, obj, act) where
    user matches sub (directly, or via group)
    AND resource matches obj (directly, or via its parent)
    AND action == act
```

`app.rego` wraps that for the backend's plain input shape. `trino.rego`
wraps it for Trino's request shape, and adds the two query-time-only
rules:

- **Column mask**: `hr.salaries.salary` → `NULL` for anyone who isn't HR.
- **Row filter**: `sales.orders` gets `WHERE region = '<caller's region>'`
  injected automatically, on top of whatever table-level grant they have.

Both fail **closed**: if `policy_data` hasn't been pushed yet (cold start)
or the backend is unreachable, every check in both `app.rego` and
`trino.rego` denies rather than silently allowing. `not <undefined>` is
`true` in Rego — that's what makes this the default instead of something
you have to opt into.

The user→region/HR attribute table lives in the same `authz` database
(`user_attributes`), pushed alongside everything else — no second
hand-maintained copy.

## Airflow: authorization inside the engine, not in front of it

`airflow/plugins/opa_auth_manager/auth_manager.py` implements Airflow's
`BaseAuthManager` interface (AIP-56, the pluggable auth SPI Airflow 3
introduced) and points `AIRFLOW__CORE__AUTH_MANAGER` at it. Every
authorization-sensitive request Airflow's API/webserver handles — view a
Dag, trigger a run, read task logs, view a Dag's code — calls this
class's `is_authorized_dag()` before doing anything else.

Login/session handling isn't reimplemented: `OPAAuthManager` subclasses
Airflow's own `SimpleAuthManager` and inherits its static
username/password/role model (`AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS`)
rather than wiring Keycloak SSO into Airflow, which this POC doesn't
attempt — the same "not everywhere yet" honesty as the isolation
guarantees in the main IDMA deck. That role still gates the handful of
non-Dag resources (connections, variables, pools, configuration) this
POC leaves untouched. Only Dag-level authorization — the "job and
entities" surface the demo focuses on — is overridden to call OPA.

`is_authorized_dag()` receives Airflow's own vocabulary — a `method`
(`GET`/`POST`/`PUT`/`DELETE`) and an optional `access_entity`
(`RUN`, `TASK_LOGS`, `CODE`, `TASK_INSTANCE`, `XCOM`, …). The auth
manager maps that pair onto the same small action vocabulary
`opa/policies/airflow.rego` and `backend/app/seed.py` already speak —
`view`, `trigger`, `view_logs`, `view_code` — and asks OPA:

```
POST /v1/data/airflow/allow
{"input": {"user": "alice", "dag_id": "sales_etl", "action": "trigger"}}
```

`airflow.rego` is a two-line wrapper, the same shape as `app.rego`:

```rego
allow if {
	authz.allow(input.user, sprintf("airflow.dag.%s", [input.dag_id]), input.action)
}
```

Dag resources live in the same `policies` table as everything else —
`airflow.dag.sales_etl`, `airflow.dag.hr_payroll_sync`,
`airflow.dag.customer_export` — granted directly or via `grp_data_eng` /
`grp_hr` exactly like the Trino schemas. Nothing new to learn on the
policy side; only the resource namespace changed.

The frontend's **Airflow Jobs** tab doesn't talk to Airflow at all — it
calls `GET /api/airflow/dags`, which runs the *exact same*
`opa_client.check(user, resource, action)` the Data Catalog tab uses
(`data.app.allow`, not `data.airflow.allow` — both delegate to the same
`authz.allow()` underneath). It's a preview of the policy decision, not a
proxy to Airflow's API — the real enforcement, the kind that holds even
if you bypass this frontend entirely and hit Airflow's UI directly, is
`opa_auth_manager` calling `data.airflow.allow` inside Airflow itself.

## Organization isolation

Every user and every resource belongs to exactly one organization
(`backend/app/seed.py`'s `DEMO_USERS[...]["org"]` and `RESOURCE_ORG`).
`authz.rego`'s `same_org()` check is layered on top of the existing
subject/resource/action match — **independent** of it, the same way
Trino's row filters and column masks apply independently of a table
grant:

```rego
allow(user, resource, action) if {
	some p in policies
	subject_matches(user, p[0])
	resource_matches(resource, p[1])
	action == p[2]
	same_org(user, resource)   # ← the boundary, checked regardless of the grant above
}
```

The demo makes this concrete rather than abstract: **`erin` (org-002) is
deliberately placed in the same `grp_data_eng` group as `alice`
(org-001)** — a realistic naming collision, since "data engineer" is a
common role title across companies — and that group is granted access to
*both* organizations' resources (`sales` for org-001, `org2_sales` for
org-002). Without the org check, either user could reach the other
org's data through that shared group. With it, `alice` still can't touch
`org2_sales` or `org2_etl`, and `erin` still can't touch `sales`, `hr`,
or any of org-001's three Dags — verified against the real running
Trino and Airflow, not just OPA in isolation (see the walkthrough).

`resource_org` covers schemas and Dags directly; a table (`sales.customers`)
inherits its schema's org through the same one-hop resource tree
`resource_matches` already walks — no separate per-table tagging needed.
Fails closed like everything else here: an untagged resource or a user
with no `org` attribute makes `same_org` undefined, which denies.

## Storage isolation

Every other isolation guarantee in this POC — Trino, Airflow, the org
check above — is the *same policy engine* asked a different shape of
question. Storage isolation isn't: it's enforced by **MinIO's own IAM**,
and this backend never asks OPA anything about it. That's deliberate,
not an oversight — it's exactly what IDMA's own architecture describes:
*"each Storage Box mints its own access key and secret"* is a credential
boundary, not a policy decision, and mixing the two would blur a
distinction worth keeping clear: **isolation is independent per layer.**

`minio/init.sh` (run once by the `minio-init` service) provisions:

- one bucket per organization — `org-001`, `org-002`
- one MinIO **user** per organization (`org001svc`, `org002svc`), each
  with an IAM policy scoped to `s3:ListBucket`/`GetObject`/`PutObject`
  on *only* its own bucket
- a few sample files uploaded with the root credentials — a
  provisioning-time action, the same shape as a platform admin
  populating a workspace before handing over its own scoped credential

The backend (`app/storage_client.py`) holds both orgs' credentials (it's
the thing provisioning storage on their behalf, the same way it holds
Trino's `user=` impersonation) but only ever uses an org's own client
against buckets that client itself reports — with one exception:
`/api/storage/cross-check` deliberately uses one org's credentials
against a bucket it *doesn't* own, to prove the denial is real (a
genuine MinIO `AccessDenied`), not just "this backend chooses not to ask."

**Bucket names are discovered live from MinIO, never hardcoded.**
`seed.py`'s `ORG_STORAGE` holds only credentials — no bucket name field
at all. `storage_client.list_buckets(org)` calls MinIO's own
`ListBuckets` API with that org's scoped credential, and MinIO filters
the response to buckets the credential actually has access to (verified
empirically: a user scoped to one bucket via IAM policy gets back
exactly that bucket, not an error and not every bucket in the system —
no extra `s3:ListAllMyBuckets` grant needed). Whatever MinIO returns *is*
the org's bucket list, which is also what makes this genuinely dynamic
rather than a naming convention the code happens to assume: rename a
bucket, add a second one to an org, and the UI picks it up with no code
change. The one place this module *does* use the root credential is
`cross_org_attempt()` — a single read-only `ListBuckets` call to find a
bucket outside the caller's own list, never used to read or write
object data.

## Sample data

Postgres seeds three schemas in the `demo` database:

- `sales` — `customers`, `orders` (denormalized with a `region` column for
  the row filter), `products` — org-001 (`postgres/init/02-demo-data.sql`)
- `hr` — `employees`, `salaries` (the sensitive one) — org-001 (same file)
- `org2_sales` — `customers`, `orders` — org-002's own tenant, same shape
  as `sales`, fully separate schema (`postgres/init/04-demo-data-org2.sql`)

MinIO seeds one bucket per org (`minio/seed-files/org-001/`,
`minio/seed-files/org-002/`) with a `README.txt`, a CSV summary, and a
`notes/onboarding.md` — enough to click through and read for real.

## Demo users & policies

Seeded automatically on first backend startup (`backend/app/seed.py` →
the `authz` database):

| User | Org | Group | Trino access | Airflow access |
|---|---|---|---|---|
| `alice` | org-001 | `grp_data_eng` | all of `sales` (group grant on the schema, inherited by every table) | `sales_etl` — view, trigger, logs, code |
| `bob` | org-001 | — | `sales.customers` only (direct grant on one table) | `customer_export` — view only (direct grant) |
| `carol` | org-001 | `grp_hr` | all of `hr` (group grant on the schema) | `hr_payroll_sync` — view, trigger, logs |
| `dave` | org-001 | — | nothing — no policy at all, until you grant one live in the UI | nothing — same story, one layer up |
| `erin` | org-002 | `grp_data_eng` (same group as alice) | all of `org2_sales` — **not** `sales`, despite sharing alice's group | `org2_etl` — view, trigger, logs, code — **not** `sales_etl`, same reason |

Plus a region and HR flag per user, used only by Trino's masking/filtering rules:

| User | Region | HR? |
|---|---|---|
| `alice` | APAC | no |
| `bob` | EMEA | no |
| `carol` | APAC | yes |
| `dave` | LATAM | no |
| `erin` | EMEA | no |

## Running it

```bash
cd poc
docker-compose up -d --build
```

First boot takes a few minutes — Postgres init scripts, Trino coordinator
startup, and `apache/airflow:3.0.6` is a large image the first time it's
pulled, plus `airflow-init` running `airflow db migrate` before the
api-server/scheduler/dag-processor start. Then open:

- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:8001 (docs at `/docs`)
- **Trino UI:** http://localhost:8080
- **Airflow UI:** http://localhost:8082 — log in as `alice` / `bob` / `carol` / `dave` / `erin`;
  passwords are generated on first boot, printed to `docker-compose logs airflow-apiserver`
  (look for `Password for user '<name>'`) — or read them straight from the container:
  `docker exec poc_airflow-apiserver_1 cat /opt/airflow/simple_auth_manager_passwords.json.generated`
- **OPA API:** http://localhost:8181 (`/v1/data/app/allow`, `/v1/data/trino/allow`,
  `/v1/data/airflow/allow`, `/v1/data/policy_data`)
- **MinIO Console:** http://localhost:9001 — log in with the root credentials below to see both
  buckets and both scoped users; the demo itself never uses the root credentials
- **Postgres:** `localhost:5433`, user `poc` / password `pocpass`, databases `demo`, `authz`, and `airflow`
- **MinIO root credentials** (admin only — see `docker-compose.yml`): `pocadmin` / `pocadminpass`

Stop everything with `docker-compose down` (add `-v` to also drop the
Postgres volume and reset all policies back to the seed state).

> Ports are non-default (`5433`, `8001`, `8082`) because `5432`, `8000`,
> and `8080` were already in use by other containers on this machine.
> Change them back in `docker-compose.yml` and `frontend/app.js` (`API`
> constant) if that's not the case for you.

> **Already had this stack running before a `git pull`?** A handful of
> things only happen once, against a genuinely fresh state — cleanest fix
> is always `docker-compose down -v && docker-compose up -d --build`
> (also resets any live grant/revoke changes back to the seed state). To
> patch an existing stack in place instead, non-destructively:
>
> 1. **New `postgres/init/*.sql` files never run** against an existing
>    `poc_pgdata` volume (Postgres only runs them once, on an empty data
>    directory). Apply the new one by hand, e.g.
>    `docker exec -i poc_postgres_1 psql -U poc -d demo < postgres/init/04-demo-data-org2.sql`
>    (or `-d demo -c "CREATE DATABASE airflow;"` for the Airflow db).
> 2. **New rows in `INITIAL_POLICIES` never get inserted** into an
>    already-seeded `authz` database (`db.seed_if_empty()` is a one-shot
>    gate, deliberately, so a live revoke through the UI survives a
>    restart). Insert the new rows by hand from `seed.py`, then restart
>    the backend so it re-pushes `policy_data` into OPA. Additions to
>    `DEMO_USERS`/`GROUP_MEMBERSHIP`/`RESOURCE_ORG` don't have this
>    problem — `db.sync_non_revocable_seed_data()` reconciles those on
>    every startup automatically, since nothing in the API ever lets a
>    user revoke them.
> 3. **OPA does not hot-reload `opa/policies/*.rego`.** The compose
>    command has no `-w`/watch flag, so an OPA container keeps running
>    whatever Rego it loaded at its own startup — a `docker-compose up
>    -d --build backend` does nothing for OPA. After editing any `.rego`
>    file: `docker restart poc_opa_1`, then restart the backend too (it's
>    what re-pushes `policy_data` into the freshly-emptied OPA).
> 4. **A new Airflow demo user needs a container recreate, not a
>    restart.** `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS` is baked in at
>    container creation; `docker restart` reuses the old environment.
>    Use `docker-compose up -d airflow-apiserver airflow-scheduler
>    airflow-dag-processor` (recreates, doesn't just restart) after
>    changing that list.
> 5. **`minio-init` is safe to re-run anytime**, on a fresh volume or not
>    — every step in `minio/init.sh` is idempotent (`--ignore-existing`,
>    or guarded with `|| true`). If you add a new org's bucket/user/sample
>    files later, just `docker-compose up -d minio-init` again rather than
>    hand-rolling `mc` commands.

## Suggested walkthrough

**Through the app — the backend's own check:**

1. Open the frontend as **Alice** (default). All of `sales` is green, all of
   `hr` is red. Click `customers` — real rows come back through Trino.
2. Switch to **Bob**. Only `sales.customers` is green — `orders` and
   `products` are red even though they're the same schema, because Bob's
   grant is direct on one table, not inherited from a group.
3. Switch to **Dave**. Everything is red — no policy, no access.

**The same policy, enforced again natively inside Trino:**

4. Still as **Alice**, click `orders`. Her grant covers the *whole* `sales`
   schema, but she only sees 4 rows, all `region = 'APAC'` — Trino applied
   the row filter regardless of her table-level grant. Switch to **Bob**
   (`region = EMEA`) and click `orders` again — different rows, same
   table, same query.
5. Switch to **Carol** and click `salaries` — real salary values, because
   the policy marks her as HR.
6. Scroll to **Live demo: grant / revoke**. Grant `dave` → `hr.salaries` →
   `select`, then click `hr.salaries` on the left immediately — no wait
   needed. It's green now, but every `salary` value comes back `NULL` —
   masking applies independently of the grant that just let him in.
   Revoke it and the row goes back to denied entirely, also instantly.
7. The **Policy inspector** panel always shows the live authz database
   contents backing every decision above.

**Bypassing the app entirely — this is the part that changed:**

Connect straight to Trino, skipping the backend and its OPA check
completely. Table-level access is still enforced, because Trino asks OPA
itself now:

```bash
docker exec -it poc_backend_1 python3 -c "
import trino
conn = trino.dbapi.connect(host='trino', port=8080, user='dave', catalog='postgres', schema='hr')
cur = conn.cursor()
cur.execute('SELECT * FROM hr.salaries')
print(cur.fetchall())
"
# PERMISSION_DENIED — dave has no grant on hr.salaries, and Trino no
# longer needs the backend to know that.
```

Try the same with `user='carol'` — she's granted `hr` access via her
group and is marked HR, so it succeeds with real salary values. Try
`user='alice'` against `sales.orders` — succeeds, but still only her
region's rows.

**Same story, one engine over — the Airflow Jobs tab:**

8. Click the **Airflow Jobs** tab. As **Alice**, `sales_etl` shows all
   four permission chips green (`view`, `trigger`, `view_logs`,
   `view_code`) — her `grp_data_eng` grant. `hr_payroll_sync` and
   `customer_export` are all red.
9. Switch to **Bob** — only `customer_export`'s `view` chip is green, a
   direct per-Dag grant, same shape as his `sales.customers` grant in
   Trino. Switch to **Dave** — everything red, same default deny.
10. Use **Live demo: grant / revoke** on this tab to grant `dave` →
    `airflow.dag.hr_payroll_sync` → `trigger`, then look at Dave's row
    again — instant, same mechanism as the Trino tab's grant form.
11. This tab previews the *policy*, not Airflow itself — for the real
    thing, open the **Airflow UI** (http://localhost:8082) and log in as
    two different users. The Dag list you see *is* filtered by these same
    grants, decided natively inside Airflow by `opa_auth_manager` on
    every request — not by anything this frontend or backend did.

**Organization isolation — the sharpest test in this whole demo:**

12. Switch to **Erin** (org-002 — note the org tag next to "Acting as"
    now reads `Globex Logistics (org-002)`, not `Acme Retail (org-001)`).
    On the **Data Catalog** tab, `org2_sales` is green; `sales` and `hr`
    are both red. On the **Airflow Jobs** tab, `org2_etl` is green with
    all four chips; the other three Dags are all red.
13. Here's the part that actually proves isolation rather than just
    "no grant exists": **Erin is in `grp_data_eng` — the exact same group
    as Alice** (check the Policy inspector's `g` table). That group *is*
    granted `sales` (org-001) as well as `org2_sales` (org-002). If you
    switch back to Alice, `org2_sales` is still red despite her group
    membership nominally matching that grant too — the `same_org()` check
    in `authz.rego` blocks it independently of the subject/resource match,
    the same way a row filter or column mask applies independently of a
    table grant.
14. This isn't just a policy-preview effect — confirm it against the real
    engines. Direct-to-Trino, bypassing the backend entirely:
    ```bash
    docker exec -it poc_backend_1 python3 -c "
    import trino
    conn = trino.dbapi.connect(host='trino', port=8080, user='alice', catalog='postgres', schema='org2_sales')
    cur = conn.cursor()
    cur.execute('SELECT * FROM org2_sales.customers')
    print(cur.fetchall())
    "
    # PERMISSION_DENIED — alice's grp_data_eng grant on org2_sales exists,
    # but Trino's own OPA check still enforces the org boundary.
    ```
    Swap `user='erin'` and it succeeds with real rows. Same asymmetry
    holds logging into the real Airflow UI as each user — Erin sees only
    `org2_etl`, Alice sees only `sales_etl`, even though both are
    `grp_data_eng`.

**Storage isolation — a different mechanism, on purpose:**

15. Click the **Storage** tab. As Alice, the bucket name (`org-001`) and
    its three files (`README.txt`, `q1-sales-summary.csv`,
    `notes/onboarding.md`) both come from a live MinIO `ListBuckets` /
    `ListObjectsV2` call using `org001svc`'s own credentials — nothing
    here is a config lookup. Click any file to preview its real contents.
16. Click **Try the other org's bucket**. The result is a genuine MinIO
    `AccessDenied`, not a "no policy grants this" message — there's no
    OPA involved on this tab at all. Switch to Erin and repeat: same
    button, same denial, opposite direction.
17. Confirm it outside the app entirely — the MinIO Console
    (http://localhost:9001) logged in as root shows both buckets and
    both scoped users, but log into the console as `org001svc` (its
    secret key is in `backend/app/seed.py`'s `ORG_STORAGE`) and `org-002`
    won't even list.

## Project layout

```
poc/
├── docker-compose.yml
├── postgres/init/         # schema + seed data, and the authz/airflow DB bootstrap
├── trino/
│   ├── catalog/                    # postgres.properties — Trino's connector config
│   └── access-control.properties   # wires Trino to the OPA plugin
├── airflow/
│   ├── dags/               # 4 trivial demo Dags (sales_etl, hr_payroll_sync, customer_export, org2_etl)
│   └── plugins/
│       └── opa_auth_manager/
│           └── auth_manager.py   # BaseAuthManager subclass — is_authorized_dag() calls OPA
├── minio/
│   ├── init.sh             # provisions buckets + one scoped IAM user per org + sample files
│   └── seed-files/          # org-001/, org-002/ — the actual files each bucket gets seeded with
├── opa/policies/
│   ├── authz.rego   # shared matching logic — subject/resource hierarchy + same_org() isolation
│   ├── app.rego      # the backend's own check
│   ├── trino.rego    # Trino's check + row filter + column mask
│   └── airflow.rego  # Airflow's check — same authz.allow(), Dag-shaped input
├── backend/
│   └── app/
│       ├── main.py            # API routes, incl. /api/airflow/dags, /api/storage/files
│       ├── db.py              # the authz database — schema, seeding, reads/writes
│       ├── opa_client.py      # calls OPA for decisions; pushes policy_data on writes
│       ├── storage_client.py  # per-org MinIO/S3 clients — no OPA involved, credential isolation only
│       ├── seed.py            # demo users + orgs, resource tree, resource_org, ORG_STORAGE, policies
│       └── trino_client.py    # runs the one allowed query shape
└── frontend/               # static HTML/CSS/JS, no build step — Data Catalog + Airflow Jobs + Storage tabs
```
