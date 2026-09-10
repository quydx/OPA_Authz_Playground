"""Demo DAG — direct (non-group) grant, same shape as bob's direct
sales.customers grant in Trino: bob can view this one DAG without being
in any group, granted per-DAG rather than inherited.
"""
from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(
    dag_id="customer_export",
    description="Exports sales.customers to the partner feed",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["sales", "idma-demo"],
) as dag:
    build_export = EmptyOperator(task_id="build_export")
    upload = EmptyOperator(task_id="upload_to_partner")

    build_export >> upload
