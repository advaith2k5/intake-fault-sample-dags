"""Fault · live alarm ingestion + on-air gold match.

    check_references ─► extract_load_alarms ─► alarm_gold

schedule=None: this DAG must not run against a stale intake set, so it is
started only by the trigger_fault task at the end of ingest_intake_4g.

check_references guards a real landmine: when ran.alarm_vs_kpi has no
producer, alarm_gold.sql's `LEFT JOIN ... COALESCE(vk.flag_block_progress,
false)` makes every alarm non-blocking -- rows intact, the blocking decision
silently flattened to a constant. A green DAG that decides nothing is worse
than a DAG that fails loudly, so this asserts both reference tables are
non-empty before touching the live feed.
"""

from __future__ import annotations

from pathlib import Path

import pendulum
from airflow.decorators import task
from airflow.exceptions import AirflowFailException
from airflow.models.dag import DAG

import _common

SQL_DIR = Path(__file__).parent / "sql"


def _scalar(sql: str) -> int:
    conn = _common.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()[0]
    finally:
        conn.close()


with DAG(
    dag_id="ingest_fault_alarm_monitoring",
    description="Fault: load the live alarm feed and rebuild the on-air-matched gold table.",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["fault", "ingestion"],
) as dag:

    @task(task_id="check_references")
    def check_references() -> None:
        node_level_rows = _scalar("SELECT count(*) FROM ran.alarm_node_level")
        if node_level_rows == 0:
            raise AirflowFailException(
                "ran.alarm_node_level is empty -- run ingest_fault_alarm_equipment_type "
                "before ingest_fault_alarm_monitoring, or the node-level join in "
                "alarm_gold.sql will drop every alarm."
            )
        vs_kpi_rows = _scalar("SELECT count(*) FROM ran.alarm_vs_kpi")
        if vs_kpi_rows == 0:
            raise AirflowFailException(
                "ran.alarm_vs_kpi is empty -- run ingest_fault_alarm_library before "
                "ingest_fault_alarm_monitoring, or alarm_gold.sql's LEFT JOIN will "
                "silently flatten every alarm's flag_block_progress to false."
            )

    @task(task_id="extract_load_alarms")
    def extract_load_alarms() -> int:
        return _common.run_extract_load(
            "fault_alarm_monitoring",
            timestamp_columns=["event_time", "close_time", "last_occurrence_time"],
        )

    @task(task_id="alarm_gold")
    def alarm_gold(_alarms: int) -> int:
        sql = (SQL_DIR / "alarm_gold.sql").read_text(encoding="utf-8")
        conn = _common.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                affected = cur.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    check_references() >> alarm_gold(extract_load_alarms())
