"""Real team identities, the resource tree, and the initial policy set.

Was five fictional demo identities (alice/bob/carol/dave/erin); replaced
with the actual iHub team (see "iHub users.xlsx") and five role-based
groups matching its Group column. Same worked-example shape as before —
group-inherited grants, resource-tree inheritance, and a deliberate
separation-of-duties case (grp_de can operate hr_payroll_sync without a
`select` grant on the `hr` schema itself, i.e. maintain the pipeline
without being able to query salary data through it) — just populated
with this team's own people instead of a textbook scenario.

These are plain data structures — the authz database (see db.py) is the
actual system of record once the backend starts; this module only supplies
the initial seed. Users created live through the Users tab (see main.py's
/api/users CRUD and keycloak_admin.py) live only in the authz database and
Keycloak — never here, since this file ships with the image and isn't
something a running container can write back to.
"""

# Two organizations — separate tenants, not just separate resources. Every
# resource below (schemas, Airflow Dags) is tagged with exactly one of
# these in RESOURCE_ORG, and authz.rego's same_org() check enforces the
# boundary independently of whatever subject/resource grant might
# otherwise match — see the org isolation walkthrough in README.md.
#
# org-001 relabeled from "Acme Retail" to "VNPTAI" (the actual team's own
# company) when the user base below switched from the fictional demo
# identities to the real one — the id stays "org-001" so RESOURCE_ORG,
# the MinIO credentials in ORG_STORAGE, and the Trino catalog below don't
# need to change along with it. org-002 ("Globex Logistics") is left as
# unused second-tenant demo content — nobody on the real team belongs to
# it, so same_org() still denies it to everyone below, same guarantee as
# before.
ORGANIZATIONS = {
    "org-001": "VNPTAI",
    "org-002": "Globex Logistics",
}

# Region is "APAC" for everyone (not the xlsx's literal "Vietnam") so the
# row-filter policy on sales.orders (filters to the caller's own region)
# still matches real seeded rows — Vietnam is geographically APAC, and
# none of CATALOG's seed data uses a "Vietnam" region string.
DEMO_USERS = {
    "admin": {"label": "Admin", "title": "Admin", "groups": ["grp_admin"], "region": "APAC", "hr": False, "org": "org-001"},
    "quydx": {"label": "Quy", "title": "SA", "groups": ["grp_admin"], "region": "APAC", "hr": False, "org": "org-001"},
    "bambootran": {"label": "Cuong Tran", "title": "Boss", "groups": ["grp_admin"], "region": "APAC", "hr": False, "org": "org-001"},
    "thanhngoc": {"label": "Ngoc", "title": "PM", "groups": ["grp_pm"], "region": "APAC", "hr": False, "org": "org-001"},
    "nmtgiang": {"label": "Giang", "title": "SA", "groups": ["grp_sa"], "region": "APAC", "hr": False, "org": "org-001"},
    "m1nhd3n": {"label": "Minh", "title": "DS", "groups": ["grp_ds"], "region": "APAC", "hr": False, "org": "org-001"},
    "hainamng192": {"label": "Nam", "title": "DS", "groups": ["grp_ds"], "region": "APAC", "hr": False, "org": "org-001"},
    "du0lg": {"label": "Duong", "title": "DE", "groups": ["grp_de"], "region": "APAC", "hr": False, "org": "org-001"},
    "dedev": {"label": "DE Dev", "title": "DE", "groups": ["grp_de"], "region": "APAC", "hr": False, "org": "org-001"},
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
    ("admin", "grp_admin"),
    ("quydx", "grp_admin"),
    ("bambootran", "grp_admin"),
    ("thanhngoc", "grp_pm"),
    ("nmtgiang", "grp_sa"),
    ("m1nhd3n", "grp_ds"),
    ("hainamng192", "grp_ds"),
    ("du0lg", "grp_de"),
    ("dedev", "grp_de"),
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
    "airflow.dag.train_sample_model": "org-001",
    "airflow.dag.train_eval_pipeline": "org-001",
    "airflow.dag.automl_wizard_pipeline": "org-001",
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
# layer up. See INITIAL_POLICIES below for which group operates which Dag.
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
    "train_sample_model": {
        "label": "Train Sample Model",
        "description": "Trains a sample iris classifier and registers it in MLflow",
    },
    "train_eval_pipeline": {
        "label": "Train/Eval Pipeline",
        "description": "Configurable end-to-end ML pipeline: extract, split, train, evaluate, quality-gated register, optional promote",
    },
    "automl_wizard_pipeline": {
        "label": "AutoML Wizard Pipeline",
        "description": "8-step AutoML wizard: choose problem, connect data, select data, clean, select target, create features, configure & run, results",
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
#
# Role design (five groups, matching "iHub users.xlsx"'s Group column):
#   grp_admin (Admin/Boss/senior SA) — full access to every org-001
#     schema and Dag, including hr_payroll_sync and hr itself.
#   grp_pm (Project Manager) — read-only visibility: sales data, plus
#     view/view_logs (not trigger/view_code) on the non-HR Dags. Tracks
#     delivery status without operating pipelines or seeing payroll.
#   grp_sa (Solution Architect) — same operational reach as admin on the
#     non-HR Dags (view/trigger/view_logs/view_code + sales select), but
#     no hr/hr_payroll_sync — architects design the non-sensitive
#     pipelines, payroll stays admin-only.
#   grp_ds (Data Scientist) — owns train_sample_model outright; read-only
#     visibility into the ETL Dags that feed its training data; sales
#     select for building datasets. No hr access.
#   grp_de (Data Engineer) — owns sales_etl/customer_export outright, AND
#     can operate hr_payroll_sync (view/trigger/view_logs/view_code) —
#     deliberately WITHOUT a `select` grant on the `hr` schema itself.
#     Separation of duties: maintaining the pipeline doesn't require
#     being able to query the salary data it moves. Read-only visibility
#     into train_sample_model, reciprocal to grp_ds's ETL visibility.
#
# org-002 (Globex Logistics) gets no grants here — nobody on the real
# team belongs to it, and same_org() would deny it regardless (see
# RESOURCE_ORG above); its Dag/schema content is unused demo filler.
INITIAL_POLICIES = [
    ("grp_admin", "sales", "select"),
    ("grp_admin", "hr", "select"),
    ("grp_pm", "sales", "select"),
    ("grp_sa", "sales", "select"),
    ("grp_ds", "sales", "select"),
    ("grp_de", "sales", "select"),

    ("grp_admin", "airflow.dag.sales_etl", "view"),
    ("grp_admin", "airflow.dag.sales_etl", "trigger"),
    ("grp_admin", "airflow.dag.sales_etl", "view_logs"),
    ("grp_admin", "airflow.dag.sales_etl", "view_code"),
    ("grp_sa", "airflow.dag.sales_etl", "view"),
    ("grp_sa", "airflow.dag.sales_etl", "trigger"),
    ("grp_sa", "airflow.dag.sales_etl", "view_logs"),
    ("grp_sa", "airflow.dag.sales_etl", "view_code"),
    ("grp_de", "airflow.dag.sales_etl", "view"),
    ("grp_de", "airflow.dag.sales_etl", "trigger"),
    ("grp_de", "airflow.dag.sales_etl", "view_logs"),
    ("grp_de", "airflow.dag.sales_etl", "view_code"),
    ("grp_pm", "airflow.dag.sales_etl", "view"),
    ("grp_pm", "airflow.dag.sales_etl", "view_logs"),
    ("grp_ds", "airflow.dag.sales_etl", "view"),
    ("grp_ds", "airflow.dag.sales_etl", "view_logs"),

    ("grp_admin", "airflow.dag.customer_export", "view"),
    ("grp_admin", "airflow.dag.customer_export", "trigger"),
    ("grp_admin", "airflow.dag.customer_export", "view_logs"),
    ("grp_admin", "airflow.dag.customer_export", "view_code"),
    ("grp_sa", "airflow.dag.customer_export", "view"),
    ("grp_sa", "airflow.dag.customer_export", "trigger"),
    ("grp_sa", "airflow.dag.customer_export", "view_logs"),
    ("grp_sa", "airflow.dag.customer_export", "view_code"),
    ("grp_de", "airflow.dag.customer_export", "view"),
    ("grp_de", "airflow.dag.customer_export", "trigger"),
    ("grp_de", "airflow.dag.customer_export", "view_logs"),
    ("grp_de", "airflow.dag.customer_export", "view_code"),
    ("grp_pm", "airflow.dag.customer_export", "view"),
    ("grp_pm", "airflow.dag.customer_export", "view_logs"),
    ("grp_ds", "airflow.dag.customer_export", "view"),
    ("grp_ds", "airflow.dag.customer_export", "view_logs"),

    # hr_payroll_sync: admin (full) and grp_de (operates the pipeline,
    # no hr schema select — see the role-design note above). Nobody else.
    ("grp_admin", "airflow.dag.hr_payroll_sync", "view"),
    ("grp_admin", "airflow.dag.hr_payroll_sync", "trigger"),
    ("grp_admin", "airflow.dag.hr_payroll_sync", "view_logs"),
    ("grp_admin", "airflow.dag.hr_payroll_sync", "view_code"),
    ("grp_de", "airflow.dag.hr_payroll_sync", "view"),
    ("grp_de", "airflow.dag.hr_payroll_sync", "trigger"),
    ("grp_de", "airflow.dag.hr_payroll_sync", "view_logs"),
    ("grp_de", "airflow.dag.hr_payroll_sync", "view_code"),

    ("grp_admin", "airflow.dag.train_sample_model", "view"),
    ("grp_admin", "airflow.dag.train_sample_model", "trigger"),
    ("grp_admin", "airflow.dag.train_sample_model", "view_logs"),
    ("grp_admin", "airflow.dag.train_sample_model", "view_code"),
    ("grp_ds", "airflow.dag.train_sample_model", "view"),
    ("grp_ds", "airflow.dag.train_sample_model", "trigger"),
    ("grp_ds", "airflow.dag.train_sample_model", "view_logs"),
    ("grp_ds", "airflow.dag.train_sample_model", "view_code"),
    ("grp_sa", "airflow.dag.train_sample_model", "view"),
    ("grp_sa", "airflow.dag.train_sample_model", "trigger"),
    ("grp_sa", "airflow.dag.train_sample_model", "view_logs"),
    ("grp_sa", "airflow.dag.train_sample_model", "view_code"),
    ("grp_pm", "airflow.dag.train_sample_model", "view"),
    ("grp_pm", "airflow.dag.train_sample_model", "view_logs"),
    ("grp_de", "airflow.dag.train_sample_model", "view"),
    ("grp_de", "airflow.dag.train_sample_model", "view_logs"),

    # Same shape as train_sample_model just above — grp_ds owns it, same
    # visibility-only reach for everyone else that already applies there.
    ("grp_admin", "airflow.dag.train_eval_pipeline", "view"),
    ("grp_admin", "airflow.dag.train_eval_pipeline", "trigger"),
    ("grp_admin", "airflow.dag.train_eval_pipeline", "view_logs"),
    ("grp_admin", "airflow.dag.train_eval_pipeline", "view_code"),
    ("grp_ds", "airflow.dag.train_eval_pipeline", "view"),
    ("grp_ds", "airflow.dag.train_eval_pipeline", "trigger"),
    ("grp_ds", "airflow.dag.train_eval_pipeline", "view_logs"),
    ("grp_ds", "airflow.dag.train_eval_pipeline", "view_code"),
    ("grp_sa", "airflow.dag.train_eval_pipeline", "view"),
    ("grp_sa", "airflow.dag.train_eval_pipeline", "trigger"),
    ("grp_sa", "airflow.dag.train_eval_pipeline", "view_logs"),
    ("grp_sa", "airflow.dag.train_eval_pipeline", "view_code"),
    ("grp_pm", "airflow.dag.train_eval_pipeline", "view"),
    ("grp_pm", "airflow.dag.train_eval_pipeline", "view_logs"),
    ("grp_de", "airflow.dag.train_eval_pipeline", "view"),
    ("grp_de", "airflow.dag.train_eval_pipeline", "view_logs"),

    # Same shape again — grp_ds owns it, same visibility-only reach for
    # everyone else.
    ("grp_admin", "airflow.dag.automl_wizard_pipeline", "view"),
    ("grp_admin", "airflow.dag.automl_wizard_pipeline", "trigger"),
    ("grp_admin", "airflow.dag.automl_wizard_pipeline", "view_logs"),
    ("grp_admin", "airflow.dag.automl_wizard_pipeline", "view_code"),
    ("grp_ds", "airflow.dag.automl_wizard_pipeline", "view"),
    ("grp_ds", "airflow.dag.automl_wizard_pipeline", "trigger"),
    ("grp_ds", "airflow.dag.automl_wizard_pipeline", "view_logs"),
    ("grp_ds", "airflow.dag.automl_wizard_pipeline", "view_code"),
    ("grp_sa", "airflow.dag.automl_wizard_pipeline", "view"),
    ("grp_sa", "airflow.dag.automl_wizard_pipeline", "trigger"),
    ("grp_sa", "airflow.dag.automl_wizard_pipeline", "view_logs"),
    ("grp_sa", "airflow.dag.automl_wizard_pipeline", "view_code"),
    ("grp_pm", "airflow.dag.automl_wizard_pipeline", "view"),
    ("grp_pm", "airflow.dag.automl_wizard_pipeline", "view_logs"),
    ("grp_de", "airflow.dag.automl_wizard_pipeline", "view"),
    ("grp_de", "airflow.dag.automl_wizard_pipeline", "view_logs"),
]


def all_known_resources():
    resources = list(CATALOG.keys())
    resources += [f"{schema}.{table}" for schema, tables in CATALOG.items() for table in tables]
    return resources


def all_known_airflow_resources():
    return [f"airflow.dag.{dag_id}" for dag_id in AIRFLOW_DAGS]
