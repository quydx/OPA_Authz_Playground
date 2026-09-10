"""Demo DAG — owned by data engineering, same resource sales.orders feeds.

Authorization for this DAG (who can view it, trigger it, see its logs or
its code) is decided by OPA via opa_auth_manager, not by anything in this
file — see opa/policies/airflow.rego and the grp_data_eng grants in
backend/app/seed.py.
"""
from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(
    dag_id="sales_etl",
    description="Nightly load of sales.orders from the source system",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["sales", "idma-demo"],
) as dag:
    extract = EmptyOperator(task_id="extract")
    transform = EmptyOperator(task_id="transform")
    load = EmptyOperator(task_id="load")

    extract >> transform >> load
