"""Fault · Equipment Type -> Node Level reference load.

Single task: reads Alarm_Equipment_Type.xlsx (20 rows: Equipment Type + Tech ->
Node Level) and upserts ran.alarm_node_level on (equipment_type, tech). The
file's `Equipment ID` column is prose instructions, not data -- it has no
target in sources.yaml's expected_columns and is dropped by apply_schema.
"""

from __future__ import annotations

import pendulum
from airflow.decorators import task
from airflow.models.dag import DAG

import _common

with DAG(
    dag_id="ingest_fault_alarm_equipment_type",
    description="Fault: load the Equipment Type -> Node Level (site|cell) reference table.",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["fault", "ingestion", "reference"],
) as dag:

    @task(task_id="extract_load")
    def extract_load() -> int:
        return _common.run_extract_load("fault_alarm_node_level")

    extract_load()
