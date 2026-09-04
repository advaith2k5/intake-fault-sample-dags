-- Intake gold join: on-air sites (ran.raw_intake_site) x their cells
-- (ran.site_db), keyed on site_id. Cell-wise: a 3-cell site becomes 3 rows.
-- cell_id is the certification-facing "Sec<n>" form; cell_id_num keeps the raw
-- ECGI CellID for traceability. Upsert, not truncate-reload -- a later run that
-- sees the same site again should refresh its cell facts in place.

INSERT INTO ran.intake_site (
    org_id, workspace_id, site_id, cell_id, cell_id_num, cell_name,
    site_type, status, pod, vcu_name, enodebid, ecgi
)
SELECT
    r.org_id,
    r.workspace_id,
    r.site_id,
    'Sec' || s.cellid AS cell_id,
    s.cellid           AS cell_id_num,
    s.cell_name,
    r.site_type,
    r.status,
    r.pod,
    s.vcu_name,
    s.enodebid,
    s.ecgi
FROM ran.raw_intake_site r
JOIN ran.site_db s ON s.site_id = r.site_id
WHERE s.cellid IS NOT NULL
ON CONFLICT (site_id, cell_id) DO UPDATE SET
    cell_id_num = EXCLUDED.cell_id_num,
    cell_name   = EXCLUDED.cell_name,
    site_type   = EXCLUDED.site_type,
    status      = EXCLUDED.status,
    pod         = EXCLUDED.pod,
    vcu_name    = EXCLUDED.vcu_name,
    enodebid    = EXCLUDED.enodebid,
    ecgi        = EXCLUDED.ecgi,
    modified_at = now();
