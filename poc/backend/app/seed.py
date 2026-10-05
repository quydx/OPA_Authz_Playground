"""Demo identities, the resource tree, and the initial policy set.

This mirrors the worked examples from the IDMA authorization model at a
small scale: a direct grant, a group-inherited grant, resource-tree
inheritance, and a user with no policy at all (default deny).

These are plain data structures — the authz database (see db.py) is the
actual system of record once the backend starts; this module only supplies
the initial seed and the static demo-user directory shown in the UI.
"""

# Two organizations — separate tenants, not just separate resources. Every
# resource below (schemas, Airflow Dags) is tagged with exactly one of
# these in RESOURCE_ORG, and authz.rego's same_org() check enforces the
# boundary independently of whatever subject/resource grant might
# otherwise match — see the org isolation walkthrough in README.md.
ORGANIZATIONS = {
    "org-001": "Acme Retail",
    "org-002": "Globex Logistics",
}

DEMO_USERS = {
    "alice": {"label": "Alice Nguyen", "title": "Data Engineer", "groups": ["grp_data_eng"], "region": "APAC", "hr": False, "org": "org-001"},
    "bob": {"label": "Bob Tran", "title": "Analyst", "groups": [], "region": "EMEA", "hr": False, "org": "org-001"},
    "carol": {"label": "Carol Le", "title": "HR Admin", "groups": ["grp_hr"], "region": "APAC", "hr": True, "org": "org-001"},
    "dave": {"label": "Dave Pham", "title": "Guest", "groups": [], "region": "LATAM", "hr": False, "org": "org-001"},
    # erin is deliberately in the SAME group as alice (grp_data_eng) despite
    # being in a different organization — a realistic naming collision
    # ("data engineer" is a common role title across companies) that
    # demonstrates the org boundary holds even when a group grant would
    # otherwise reach across tenants.
    "erin": {"label": "Erin Solberg", "title": "Data Engineer", "groups": ["grp_data_eng"], "region": "EMEA", "hr": False, "org": "org-002"},
}

# schema -> tables. Also doubles as the tree the frontend renders.
# org2_sales is org-002's own schema — same shape as sales, fully separate
# tenant, used to demonstrate organization isolation (see RESOURCE_ORG).
CATALOG = {
    "sales": ["customers", "orders", "products", "daily_revenue", "revenue_anomalies"],
    "hr": ["employees", "salaries"],
    "org2_sales": ["customers", "orders"],
}

# g: user -> group
GROUP_MEMBERSHIP = [
    ("alice", "grp_data_eng"),
    ("carol", "grp_hr"),
    ("erin", "grp_data_eng"),
]

# Which organization each schema/Dag (resource-tree root) belongs to.
# Tables inherit their schema's org via the one-hop resource tree, same as
# resource_matches — see authz.rego's org_of(). Every root resource must
# be listed here, or access to it is denied (fail closed, same as an
# unknown user or unpushed policy_data).
RESOURCE_ORG = {
    "sales": "org-001",
    "hr": "org-001",
    "org2_sales": "org-002",
    "airflow.dag.sales_etl": "org-001",
    "airflow.dag.hr_payroll_sync": "org-001",
    "airflow.dag.customer_export": "org-001",
    "airflow.dag.org2_etl": "org-002",
    "airflow.dag.sales_anomaly_detect": "org-001",
}

# Storage isolation — the one guarantee in this POC that OPA never
# touches. Each org has its own MinIO user, scoped to its own bucket(s)
# by IAM policy (see minio/init.sh), matching IDMA's own storage-
# isolation model: "each Storage Box mints its own access key and
# secret." A credential boundary, independent of the policy engine
# everything else here shares — see README.md's "Storage isolation".
#
# Deliberately NOT storing a bucket name here: which bucket(s) an org
# can see is discovered live from MinIO's own ListBuckets response (see
# storage_client.list_buckets) using that org's own credential below —
# not assumed from a naming convention that could drift from what
# minio/init.sh actually provisioned. Credentials are the one thing
# that genuinely can't be discovered from MinIO itself (see README) and
# so are the only thing kept here.
ORG_STORAGE = {
    "org-001": {"access_key": "org001svc", "secret_key": "org001SecretKey123"},
    "org-002": {"access_key": "org002svc", "secret_key": "org002SecretKey456"},
}

# g2: child resource -> parent resource (table -> schema)
RESOURCE_TREE = [
    (f"{schema}.{table}", schema)
    for schema, tables in CATALOG.items()
    for table in tables
]

# Airflow demo Dags — same isolation story as the Trino catalog, one
# layer up: grp_data_eng runs sales_etl, grp_hr runs the sensitive
# hr_payroll_sync, bob gets a direct per-Dag grant on customer_export
# (same shape as his direct sales.customers grant), dave gets nothing.
AIRFLOW_DAGS = {
    "sales_etl": {
        "label": "Sales ETL",
        "description": "Nightly load of sales.orders from the source system",
    },
    "hr_payroll_sync": {
        "label": "HR Payroll Sync",
        "description": "Syncs payroll data into hr.salaries",
    },
    "customer_export": {
        "label": "Customer Export",
        "description": "Exports sales.customers to the partner feed",
    },
    "org2_etl": {
        "label": "Org2 ETL",
        "description": "Nightly load of org2_sales.orders from the source system",
    },
    "sales_anomaly_detect": {
        "label": "Sales Anomaly Detect",
        "description": "Detects anomalies in sales.daily_revenue with the anomaly service",
    },
}

# The action vocabulary opa_auth_manager maps Airflow's (method,
# access_entity) pairs onto — see airflow/plugins/opa_auth_manager.
AIRFLOW_ACTIONS = ["view", "trigger", "view_logs", "view_code"]

# p: subject (user or group), resource, action
INITIAL_POLICIES = [
    ("grp_data_eng", "sales", "select"),   # group grant, inherited by every table in sales
    ("bob", "sales.customers", "select"),  # direct grant, table only
    ("grp_hr", "hr", "select"),            # group grant, inherited by every table in hr
    # dave gets nothing — demonstrates default deny until granted live in the UI

    ("grp_data_eng", "airflow.dag.sales_etl", "view"),
    ("grp_data_eng", "airflow.dag.sales_etl", "trigger"),
    ("grp_data_eng", "airflow.dag.sales_etl", "view_logs"),
    ("grp_data_eng", "airflow.dag.sales_etl", "view_code"),

    ("grp_data_eng", "airflow.dag.sales_anomaly_detect", "view"),
    ("grp_data_eng", "airflow.dag.sales_anomaly_detect", "trigger"),
    ("grp_data_eng", "airflow.dag.sales_anomaly_detect", "view_logs"),
    ("grp_data_eng", "airflow.dag.sales_anomaly_detect", "view_code"),

    ("grp_hr", "airflow.dag.hr_payroll_sync", "view"),
    ("grp_hr", "airflow.dag.hr_payroll_sync", "trigger"),
    ("grp_hr", "airflow.dag.hr_payroll_sync", "view_logs"),

    ("bob", "airflow.dag.customer_export", "view"),  # direct grant, view only
    # dave gets nothing here either — same default deny, one layer up

    # org-002's own grants — same group name (grp_data_eng) as org-001's,
    # same resource shape (a "sales" schema + its own ETL Dag), fully
    # separate tenant. Without the org check in authz.rego, this would
    # also hand alice (also grp_data_eng, but org-001) access to org-002's
    # data — that's exactly what same_org() exists to prevent.
    ("grp_data_eng", "org2_sales", "select"),
    ("grp_data_eng", "airflow.dag.org2_etl", "view"),
    ("grp_data_eng", "airflow.dag.org2_etl", "trigger"),
    ("grp_data_eng", "airflow.dag.org2_etl", "view_logs"),
    ("grp_data_eng", "airflow.dag.org2_etl", "view_code"),
]


def all_known_subjects():
    groups = sorted({g for _, g in GROUP_MEMBERSHIP})
    return list(DEMO_USERS.keys()) + groups


def all_known_resources():
    resources = list(CATALOG.keys())
    resources += [f"{schema}.{table}" for schema, tables in CATALOG.items() for table in tables]
    return resources


def all_known_airflow_resources():
    return [f"airflow.dag.{dag_id}" for dag_id in AIRFLOW_DAGS]
