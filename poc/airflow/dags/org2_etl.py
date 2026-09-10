"""Demo DAG — org-002's own ETL job. Same shape as sales_etl, deliberately
granted to the same group name (grp_data_eng) to demonstrate that
organization isolation holds even when a group grant would otherwise
reach across tenants — see backend/app/seed.py's comments on erin.
"""
from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(
    dag_id="org2_etl",
    description="Nightly load of org2_sales.orders from the source system",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["org-002", "idma-demo"],
) as dag:
    extract = EmptyOperator(task_id="extract")
    transform = EmptyOperator(task_id="transform")
    load = EmptyOperator(task_id="load")

    extract >> transform >> load
