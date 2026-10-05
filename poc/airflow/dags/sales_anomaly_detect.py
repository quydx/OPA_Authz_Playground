"""Demo DAG — daily revenue anomaly detection via the anomaly service.

extract -> detect -> save:
  1. read sales.daily_revenue (demo DB) as a {ds, y} series,
  2. POST it to the anomaly service (/detect, scope=series — it trains and
     scores in the same request; contract in
     anomaly_detection_docker_and_doc/anomaly-service_0.1.0_openapi.json),
  3. replace sales.revenue_anomalies with the points it flagged.

Like every other DAG here, who can view/trigger it is decided by OPA via
opa_auth_manager, not by this file — see the grp_data_eng grants for
airflow.dag.sales_anomaly_detect in backend/app/seed.py.

Source data comes from postgres/init/05-demo-timeseries.sql. An existing
demo DB (initialised before that file existed) needs it applied once:
  docker compose exec -T postgres psql -U poc -d demo < postgres/init/05-demo-timeseries.sql
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import psycopg2
import requests
from airflow.sdk import dag, get_current_context, task

log = logging.getLogger(__name__)

DEMO_DB_URL = os.environ.get("DEMO_DB_URL", "postgresql://poc:pocpass@postgres:5432/demo")
ANOMALY_SERVICE_URL = os.environ.get("ANOMALY_SERVICE_URL", "http://anomaly:8000")
SERIES_ID = "sales.daily_revenue"


@dag(
    dag_id="sales_anomaly_detect",
    description="Detects anomalies in sales.daily_revenue with the anomaly service",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    # Unlike the other demo Dags (which only exercise authorization), this one
    # actually runs. schedule=None means unpaused still runs only on trigger;
    # unpausing is "edit", which no demo user is granted.
    is_paused_upon_creation=False,
    default_args={"retries": 1},
    tags=["sales", "anomaly", "idma-demo"],
)
def sales_anomaly_detect():
    @task
    def extract() -> list[dict]:
        with psycopg2.connect(DEMO_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_char(ds, 'YYYY-MM-DD'), revenue::float FROM sales.daily_revenue ORDER BY ds")
            series = [{"ds": ds, "y": y} for ds, y in cur.fetchall()]
        if len(series) < 2:
            raise ValueError(f"{SERIES_ID} has {len(series)} rows; the anomaly service needs at least 2")
        log.info("Extracted %d points (%s .. %s)", len(series), series[0]["ds"], series[-1]["ds"])
        return series

    @task
    def detect(series: list[dict]) -> dict:
        ctx = get_current_context()
        resp = requests.post(
            f"{ANOMALY_SERVICE_URL}/detect",
            json={
                "series_id": SERIES_ID,
                "series": series,
                "scope": "series",
                "metadata": {"dag_id": ctx["dag"].dag_id, "dag_run_id": ctx["run_id"]},
            },
            headers={"X-API-Version": "1"},  # pin the API major; 406 if unsupported
            timeout=120,
        )
        if resp.status_code != 200:
            # Every 4xx/5xx shares {error, detail, request_id} — surface it in the task log.
            raise RuntimeError(f"anomaly service returned {resp.status_code}: {resp.text[:500]}")
        body = resp.json()
        summary = body["result"]["summary"]
        log.info(
            "run_id=%s timing_ms=%s n_anomalies=%s/%s dominant_reason=%s",
            body["run_id"], body["timing_ms"], summary["n_anomalies"], summary["n_points"],
            summary["dominant_reason"],
        )
        anomalies = [
            {
                "ds": p["ds"],
                "y": p["y"],
                "expected": (p.get("bounds") or {}).get("yhat"),
                "severity": p["severity"],
                "direction": p["direction"],
                "deviation_pct": p["deviation_pct"],
                "reasons": ",".join(r["reason_code"] for r in p["reasons"]),
            }
            for p in body["result"]["results"]
            if p["is_anomaly"]
        ]
        return {"run_id": body["run_id"], "summary": summary, "anomalies": anomalies}

    @task
    def save(result: dict) -> None:
        with psycopg2.connect(DEMO_DB_URL) as conn, conn.cursor() as cur:
            # One transaction: readers never see the table half-replaced.
            cur.execute("DELETE FROM sales.revenue_anomalies")
            cur.executemany(
                "INSERT INTO sales.revenue_anomalies "
                "(ds, revenue, expected, severity, direction, deviation_pct, reasons, run_id) "
                "VALUES (%(ds)s, %(y)s, %(expected)s, %(severity)s, %(direction)s, "
                "%(deviation_pct)s, %(reasons)s, %(run_id)s)",
                [{**a, "run_id": result["run_id"]} for a in result["anomalies"]],
            )
        log.info("Saved %d anomalies to sales.revenue_anomalies", len(result["anomalies"]))
        log.info("Summary: %s", json.dumps(result["summary"], ensure_ascii=False))

    save(detect(extract()))


sales_anomaly_detect()
