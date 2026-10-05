-- Time-series demo data for the sales_anomaly_detect Dag (airflow/dags/),
-- which sends sales.daily_revenue to the anomaly service and writes what it
-- flags into sales.revenue_anomalies. Both tables live under the "sales"
-- schema, so the same resource-tree grants (and the same Trino/OPA checks)
-- cover them as every other sales table.
--
-- Idempotent: safe to re-run against an already-initialised demo database
-- (docker-entrypoint-initdb.d only runs on a fresh volume, so an existing
-- install applies this file by hand — see the Dag's docstring).

CREATE TABLE IF NOT EXISTS sales.daily_revenue (
    ds       DATE PRIMARY KEY,
    revenue  NUMERIC(12, 2) NOT NULL
);

-- 90 days, deterministic (no random()): a weekday/weekend pattern plus a
-- small fixed jitter, and two planted anomalies — a spike on 2026-07-14
-- and a drop on 2026-08-05 — for the Dag to find.
INSERT INTO sales.daily_revenue (ds, revenue)
SELECT d::date,
       CASE d::date
           WHEN DATE '2026-07-14' THEN 2600
           WHEN DATE '2026-08-05' THEN 150
           ELSE 1000
                - CASE WHEN EXTRACT(ISODOW FROM d) >= 6 THEN 250 ELSE 0 END
                + ((EXTRACT(DOY FROM d)::int * 37) % 61) - 30
       END
FROM generate_series(DATE '2026-06-01', DATE '2026-08-29', INTERVAL '1 day') AS d
ON CONFLICT (ds) DO NOTHING;

-- Latest detection result only: each Dag run replaces the table's content.
CREATE TABLE IF NOT EXISTS sales.revenue_anomalies (
    ds             DATE PRIMARY KEY,
    revenue        NUMERIC(12, 2) NOT NULL,
    expected       NUMERIC(12, 2),
    severity       DOUBLE PRECISION,
    direction      TEXT,
    deviation_pct  DOUBLE PRECISION,
    reasons        TEXT NOT NULL,
    run_id         TEXT NOT NULL,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
