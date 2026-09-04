-- Fault gold rebuild: ran.alarm_monitoring_raw -> ran.alarm_monitoring.
-- Ports brain/ingestion/plugins/ingestion/transforms/alarm_gold.py (populate_alarm_monitoring).
--
-- 1. INNER join ran.alarm_node_level on (equipment_type, tech-reconciled LTE=4G/NR=5G)
--    -- this is a real filter, not a formality: ~48% of alarm rows (FEMTO_CELL,
--    ODSC_CELL, IDSC_CELL, DRAN_CELL, ...) have no row in the 20-entry reference
--    table and drop out here. That is the real, intended behaviour.
-- 2. JOIN LATERAL, two arms UNIONed, both scoped to the on-air set
--    (site_id IN ran.intake_site):
--      node_level = 'cell' -> equipment_id  = site_db.cell_name  -> cell_id = 'Sec'||cellid
--      node_level = 'site' -> enbid, parsed from Equipment Sub ID
--                             ('/ENB[id/enbid=280182]/...' -> 280182) = site_db.enodebid -> cell_id = ''
-- 3. LEFT JOIN ran.alarm_vs_kpi for the block/notify flags. LEFT, not INNER: an
--    alarm with no library entry still lands (flag_block_progress defaults false),
--    which is the whole reason check_references guards this DAG -- an empty
--    alarm_vs_kpi would silently flatten every alarm to non-blocking instead of
--    failing loudly.
-- 4. flag_notify_alarm = NOT COALESCE(flag_block_progress, false).
-- 5. DISTINCT ON (site_id, cell_id, alarm_code) matches the gold PK; ties broken
--    by most recent event_time.
--
-- Full rebuild (DELETE + INSERT), not an upsert: this is the live-feed gold table,
-- so a cleared alarm from the previous run should disappear, not linger.

DELETE FROM ran.alarm_monitoring;

INSERT INTO ran.alarm_monitoring (
    org_id, workspace_id, site_id, cell_id, alarm_code, equipment_type, technology,
    percieved_severity, classification, status, event_time, close_time,
    last_occurrence_time, occurrence_count, ageing, service_affected,
    flag_block_progress, flag_notify_alarm
)
SELECT DISTINCT ON (m.site_id, m.cell_id, r.alarm_code)
    r.org_id,
    r.workspace_id,
    m.site_id,
    m.cell_id,
    r.alarm_code,
    r.equipment_type,
    r.technology,
    r.perceived_severity,
    r.classification,
    r.status,
    r.event_time,
    r.close_time,
    r.last_occurrence_time,
    r.occurrence_count,
    r.ageing,
    r.service_affected,
    COALESCE(vk.flag_block_progress, false)     AS flag_block_progress,
    NOT COALESCE(vk.flag_block_progress, false) AS flag_notify_alarm
FROM ran.alarm_monitoring_raw r
JOIN ran.alarm_node_level nl
  ON nl.equipment_type = r.equipment_type
 AND upper(nl.tech) = CASE upper(COALESCE(r.technology, ''))
                            WHEN 'LTE' THEN '4G'
                            WHEN 'NR'  THEN '5G'
                            ELSE upper(COALESCE(r.technology, ''))
                       END
JOIN LATERAL (
    SELECT s.site_id, 'Sec' || s.cellid AS cell_id
    FROM ran.site_db s
    WHERE lower(nl.node_level) = 'cell'
      AND s.cell_name = r.equipment_id
      AND s.site_id IN (SELECT site_id FROM ran.intake_site)

    UNION ALL

    SELECT s.site_id, '' AS cell_id
    FROM ran.site_db s
    WHERE lower(nl.node_level) = 'site'
      AND s.enodebid = (regexp_match(COALESCE(r.equipment_sub_id, ''), 'enbid=(\d+)'))[1]
      AND s.site_id IN (SELECT site_id FROM ran.intake_site)
) m ON true
LEFT JOIN ran.alarm_vs_kpi vk ON vk.alarm_code = r.alarm_code
ORDER BY m.site_id, m.cell_id, r.alarm_code, r.event_time DESC NULLS LAST;
