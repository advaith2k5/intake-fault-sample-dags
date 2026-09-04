"""Fault · Alarm Library (ground truth) load.

Single task: reads "Alarm Vs KPI Impact_*.xlsx" and upserts ran.alarm_vs_kpi
on alarm_code. Of the file's 208 rows, 15 carry a literal #N/A alarm code and
are dropped by the loader's NOT-NULL pk guard; of the remaining 193, 8 codes
repeat and collapse on conflict -- 185 rows land. Upsert, not truncate-reload:
a reissued catalogue that omits an alarm must not silently delete a flag
alarm_gold.sql is still joining on.
"""

from __future__ import annotations

import pendulum
from airflow.decorators import task
from airflow.models.dag import DAG

import _common

with DAG(
    dag_id="ingest_fault_alarm_library",
    description="Fault: load the Alarm Vs KPI Impact ground-truth catalogue.",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["fault", "ingestion", "reference"],
) as dag:

    @task(task_id="extract_load")
    def extract_load() -> int:
        return _common.run_extract_load("fault_alarm_library")

    extract_load()
