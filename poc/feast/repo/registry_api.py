"""idma-feast-admin — the only thing in this stack with Feast SDK write
access to the registry. A tiny FastAPI app (not `feast serve`/`feast
ui`; those are data-plane and read-only respectively) exposing one
endpoint, POST /feature-views, that the Airflow DAG's luu_tru_dac_trung
task calls over HTTP whenever a wizard user checks "save this
transformation as a Feature View" and names it — create-or-update by
name, via Feast's own store.apply(), which already resolves that
distinction internally; nothing here branches on "does it exist yet".

Also owns the Postgres DDL for each feature view's own table
(fv_<name>), since Feast's apply() only ever touches the *registry* — it
never creates the backing table a PostgreSQLSource points at. Table
columns are additive-only on re-save (ADD COLUMN IF NOT EXISTS), never
dropped, so re-saving an existing name with fewer columns than before
can't silently lose what's already in old rows.

Runs as its own service (idma-feast-admin) rather than a second process
inside idma-feast's own container — see poc/docker-compose.yml /
poc/k8s/11-feast.yaml, both of which just override this image's command.
ClusterIP-only in k8s: nothing outside this stack's own Airflow DAG ever
needs to reach it.
"""
import os
import re
from datetime import timedelta

import psycopg
from fastapi import FastAPI, HTTPException
from feast import FeatureStore, FeatureView, Field
from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import (
    PostgreSQLSource,
)
from feast.types import Bool, Float64, Int64, String
from features import row_id
from pydantic import BaseModel

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# pandas dtype string (as automl_wizard_pipeline.py's luu_tru_dac_trung
# sends it, from str(work[c].dtype)) -> (Feast type, Postgres column type).
DTYPE_MAP = {
    "float64": (Float64, "DOUBLE PRECISION"),
    "int64": (Int64, "BIGINT"),
    "object": (String, "TEXT"),
    "bool": (Bool, "BOOLEAN"),
}

FEAST_DB_HOST = os.environ.get("FEAST_DB_HOST", "postgres")
FEAST_DB_DSN = f"postgresql://poc:pocpass@{FEAST_DB_HOST}:5432/feast"

app = FastAPI()
store = FeatureStore(repo_path="/app/repo")


class ColumnSpec(BaseModel):
    name: str
    dtype: str  # one of DTYPE_MAP's keys


class FeatureViewRequest(BaseModel):
    name: str
    columns: list[ColumnSpec]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/feature-views")
def create_or_update_feature_view(req: FeatureViewRequest):
    if not IDENT_RE.match(req.name):
        raise HTTPException(status_code=400, detail=f"feature view name {req.name!r} must be a plain identifier")
    if not req.columns:
        raise HTTPException(status_code=400, detail="at least one column is required")
    for col in req.columns:
        if not IDENT_RE.match(col.name):
            raise HTTPException(status_code=400, detail=f"column name {col.name!r} must be a plain identifier")
        if col.dtype not in DTYPE_MAP:
            raise HTTPException(
                status_code=400,
                detail=f"column {col.name!r}: unsupported dtype {col.dtype!r} — expected one of {sorted(DTYPE_MAP)}",
            )

    table = f"fv_{req.name}"

    # 1. The table this feature view's data actually lives in. Feast's
    # apply() below only touches the registry, never this. Identifiers
    # are validated above before any f-string interpolation into raw
    # SQL — same "validate first, then build the statement" pattern
    # already used for SQL-derived columns elsewhere in this repo.
    conn = psycopg.connect(FEAST_DB_DSN)
    try:
        with conn.cursor() as cur:
            col_defs = ", ".join(f'"{c.name}" {DTYPE_MAP[c.dtype][1]}' for c in req.columns)
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" ('
                f"entity_id INT NOT NULL, "
                f"event_timestamp TIMESTAMP NOT NULL, "
                f"created TIMESTAMP NOT NULL DEFAULT now(), "
                f"{col_defs}, "
                f"PRIMARY KEY (entity_id, event_timestamp))"
            )
            for c in req.columns:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{c.name}" {DTYPE_MAP[c.dtype][1]}')
        conn.commit()
    finally:
        conn.close()

    # 2. The registry — create-or-update by name is just apply(); Feast
    # resolves which one this is internally, no branching needed here.
    source = PostgreSQLSource(
        name=f"{req.name}_source",
        query=f"SELECT * FROM {table}",
        timestamp_field="event_timestamp",
        created_timestamp_column="created",
    )
    feature_view = FeatureView(
        name=req.name,
        entities=[row_id],
        # Demo-scale lab data, not a real freshness SLA — same reasoning
        # as the ttl the old static sales_orders_features view used.
        ttl=timedelta(days=3650),
        schema=[Field(name=c.name, dtype=DTYPE_MAP[c.dtype][0]) for c in req.columns],
        online=True,
        source=source,
    )
    store.apply([row_id, feature_view])

    return {"feature_view": req.name, "table": table, "columns": [c.name for c in req.columns]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=6568)
