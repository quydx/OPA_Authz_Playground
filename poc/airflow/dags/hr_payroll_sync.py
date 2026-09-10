"""Demo DAG — the sensitive one. Same isolation story as hr.salaries in
Trino: only grp_hr is granted anything on this DAG (see seed.py); alice
and bob, both outside grp_hr, get denied by OPA before they even see it
listed in the Airflow UI.
"""
from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(
    dag_id="hr_payroll_sync",
    description="Syncs payroll data into hr.salaries",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["hr", "idma-demo"],
) as dag:
    fetch = EmptyOperator(task_id="fetch_payroll_feed")
    reconcile = EmptyOperator(task_id="reconcile")
    write = EmptyOperator(task_id="write_salaries")

    fetch >> reconcile >> write
