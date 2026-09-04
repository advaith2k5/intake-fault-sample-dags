"""Intake · 4G On-Air ingestion.

    extract_load_4g   ─┐
                        ├─► gold_join ─► trigger_fault
    extract_load_ecgi ─┘

extract_load_4g filters the site list down to the current on-air batch
(latest Date of Intg, Status = On-Air, Locked status != Yes) and upserts
ran.raw_intake_site. extract_load_ecgi truncate-reloads the full ECGI cell
database into ran.site_db, unfiltered. gold_join inner-joins the two on
site_id and upserts the cell-wise result into ran.intake_site -- the on-air
set every other agent (Fault first) scopes to. trigger_fault kicks off the
Fault DAG, which must not run against a stale intake set.
"""

from __future__ import annotations

from pathlib import Path

import pendulum
from airflow.decorators import task
from airflow.models.dag import DAG
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

import _common

SQL_DIR = Path(__file__).parent / "sql"

with DAG(
    dag_id="ingest_intake_4g",
    description="Intake: filter the 4G On-Air site list, load ECGI cells, join to gold ran.intake_site.",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["intake", "ingestion"],
) as dag:

    @task(task_id="extract_load_4g")
    def extract_load_4g() -> int:
        return _common.run_extract_load("intake_4g_on_air", date_columns=["date_of_intg"])

    @task(task_id="extract_load_ecgi")
    def extract_load_ecgi() -> int:
        return _common.run_extract_load("intake_ecgi_site_db", date_columns=["date_of_on_air"])

    @task(task_id="gold_join")
    def gold_join(_4g: int, _ecgi: int) -> int:
        sql = (SQL_DIR / "intake_gold.sql").read_text(encoding="utf-8")
        conn = _common.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                affected = cur.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    join_result = gold_join(extract_load_4g(), extract_load_ecgi())

    trigger_fault = TriggerDagRunOperator(
        task_id="trigger_fault",
        trigger_dag_id="ingest_fault_alarm_monitoring",
    )

    join_result >> trigger_fault
