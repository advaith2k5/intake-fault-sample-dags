-- Schema for the sample Intake / Fault ingestion DAGs.
--
-- Column types default to TEXT everywhere. This is deliberate, not lazy: the real
-- files hand back ECGI numbers as floats / scientific notation, ageing as free text
-- ("8 Minute(s) 0 Second(s)"), alarm codes as a mix of numeric-looking and prose
-- strings (PM_1010382, "Unknown Host") -- typing any of that as INT/FLOAT breaks on
-- the next file. Only four kinds of column get a real type: timestamps
-- (event_time/close_time/last_occurrence_time), dates (date_of_intg/date_of_on_air),
-- booleans (locked/flag_block_progress/flag_notify_alarm), and alarm_code, which is
-- TEXT on principle (real values: PM_1010382, aniExtAlmContactAlarm_Battery Failure).
--
-- Mounted into /docker-entrypoint-initdb.d/ -- runs once, on first cluster boot.

CREATE SCHEMA IF NOT EXISTS ran;

-- ───────────────────────────────────────────────────────────────────────
-- Bronze
-- ───────────────────────────────────────────────────────────────────────

-- Intake · 4G On-Air site list, filtered (latest Date of Intg, Status = On-Air,
-- Locked status != Yes) but not yet joined to its cells.
CREATE TABLE IF NOT EXISTS ran.raw_intake_site (
    org_id          text NOT NULL DEFAULT 'rakuten',
    workspace_id    text NOT NULL DEFAULT 'rmi',
    site_id         text PRIMARY KEY,
    status          text,
    date_of_intg    date,
    locked          boolean,
    pod             text,
    site_type       text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    modified_at     timestamptz NOT NULL DEFAULT now()
);

-- Intake · ECGI cell database. Superset of the on-air site set (also carries
-- 5G/planned sites) -- the gold join is what scopes it down. No natural PK: ECGI
-- would be the obvious key but the loader truncate-reloads wholesale, so an
-- identity column is simpler than worrying about upsert semantics here.
CREATE TABLE IF NOT EXISTS ran.site_db (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id                      text NOT NULL DEFAULT 'rakuten',
    workspace_id                text NOT NULL DEFAULT 'rmi',
    rrh_serial_number           text,
    ecgi                        text,
    cellid                      text,
    riu_serial_number           text,
    bandwidth                   text,
    dl_earfcn                   text,
    site_id                     text,
    clutter_type                text,
    cell_name                   text,
    date_of_on_air              date,
    enodebid                    text,
    vdu_name                    text,
    vcu_name                    text,
    gc_name                     text,
    gc_code                     text,
    pci                         text,
    rsi                         text,
    azimuth                     text,
    ant_height                  text,
    ant_name                    text,
    ant_vender                  text,
    vcu_id                      text,
    vdu_id                      text,
    riu_number                  text,
    ems_name                    text,
    software_build              text,
    tac                         text,
    mech_antenna_tilt           text,
    electrical_antenna_tilt     text,
    latitude                    text,
    longitude                   text,
    type                        text,
    cluster                     text,
    zone                        text,
    city                        text,
    prefecture                  text,
    subregion                   text,
    region                      text,
    region_manager              text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    modified_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_site_db_site_id ON ran.site_db (site_id);
CREATE INDEX IF NOT EXISTS ix_site_db_cell_name ON ran.site_db (cell_name);
CREATE INDEX IF NOT EXISTS ix_site_db_enodebid ON ran.site_db (enodebid);

-- Fault · live alarm feed, unfiltered (raw keeps every alarm, duplicate codes and
-- all -- the on-air match happens downstream in alarm_gold).
CREATE TABLE IF NOT EXISTS ran.alarm_monitoring_raw (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id                  text NOT NULL DEFAULT 'rakuten',
    workspace_id            text NOT NULL DEFAULT 'rmi',
    event_time              timestamptz,
    close_time              timestamptz,
    classification          text,
    perceived_severity      text,
    occurrence_count        text,
    last_occurrence_time    timestamptz,
    domain                  text,
    vendor                  text,
    region_product          text,
    prefecture_cluster      text,
    city_namespace          text,
    rf_cluster_node         text,
    gc_cdc_name             text,
    equipment_type          text,
    equipment_id            text,
    equipment_sub_id        text,
    equipment_id_status     text,
    alarm_type              text,
    alarm_code              text,
    alarm_name              text,
    ageing                  text,
    technology              text,
    alarm_description       text,
    probable_cause          text,
    service_affected        text,
    ems                     text,
    correlation_type        text,
    incident_id             text,
    entity_family           text,
    reported_severity       text,
    status                  text,
    alarm_hierarchy         text,
    ticket_id               text,
    planned_event_name      text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    modified_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_alarm_monitoring_raw_equipment_id ON ran.alarm_monitoring_raw (equipment_id);
CREATE INDEX IF NOT EXISTS ix_alarm_monitoring_raw_alarm_code ON ran.alarm_monitoring_raw (alarm_code);

-- ───────────────────────────────────────────────────────────────────────
-- Reference
-- ───────────────────────────────────────────────────────────────────────

-- Fault · Equipment Type + Tech -> Node Level ('Cell' | 'Site' | 'Femto Cell').
-- Drives which arm of the alarm_gold LATERAL join an alarm is matched through.
CREATE TABLE IF NOT EXISTS ran.alarm_node_level (
    org_id          text NOT NULL DEFAULT 'rakuten',
    workspace_id    text NOT NULL DEFAULT 'rmi',
    equipment_type  text NOT NULL,
    tech            text NOT NULL,
    node_level      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    modified_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (equipment_type, tech)
);

-- Fault · alarm code catalogue -- ground truth for severity + the block-progress
-- gate. `impact` has no source in the 9-Apr-26 issue of the workbook; it loads NULL
-- until a reissue carries it.
CREATE TABLE IF NOT EXISTS ran.alarm_vs_kpi (
    org_id                      text NOT NULL DEFAULT 'rakuten',
    workspace_id                text NOT NULL DEFAULT 'rmi',
    alarm_code                  text PRIMARY KEY,
    flag_block_progress         boolean,
    impact                      text,
    alarm_name                  text,
    count                       text,
    technology                  text,
    vendor                      text,
    alarm_description           text,
    probable_cause              text,
    classification              text,
    perceived_severity          text,
    availability                text,
    accessibility                text,
    retainability                text,
    intra_hosr                  text,
    inter_hosr                  text,
    rlf                         text,
    user_thpt                   text,
    x2_rate                     text,
    ho_attempt_towards_kddi     text,
    remarks                     text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    modified_at                 timestamptz NOT NULL DEFAULT now()
);

-- ───────────────────────────────────────────────────────────────────────
-- Gold
-- ───────────────────────────────────────────────────────────────────────

-- Intake · on-air site x cell, cell-wise (a 3-cell site is 3 rows). Everything
-- downstream (Fault included) scopes to on-air via `site_id IN (SELECT site_id
-- FROM ran.intake_site)`.
CREATE TABLE IF NOT EXISTS ran.intake_site (
    org_id          text NOT NULL DEFAULT 'rakuten',
    workspace_id    text NOT NULL DEFAULT 'rmi',
    site_id         text NOT NULL,
    cell_id         text NOT NULL,
    cell_id_num     text,
    cell_name       text,
    site_type       text,
    status          text,
    pod             text,
    vcu_name        text,
    enodebid        text,
    ecgi            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    modified_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, cell_id)
);

-- Fault · on-air-matched, classified alarms. `percieved_severity` is spelled with
-- the platform DDL's typo on purpose -- alarm_gold.sql depends on it; the raw table
-- above spells it correctly. `cell_id` is '' (not null) for a site-level match, so
-- it can sit in the PK next to a cell-level `Sec<n>` value.
CREATE TABLE IF NOT EXISTS ran.alarm_monitoring (
    org_id                  text NOT NULL DEFAULT 'rakuten',
    workspace_id            text NOT NULL DEFAULT 'rmi',
    site_id                 text NOT NULL,
    cell_id                 text NOT NULL,
    alarm_code              text NOT NULL,
    equipment_type          text,
    technology              text,
    percieved_severity      text,
    classification          text,
    status                  text,
    event_time              timestamptz,
    close_time              timestamptz,
    last_occurrence_time    timestamptz,
    occurrence_count        text,
    ageing                  text,
    service_affected        text,
    flag_block_progress     boolean,
    flag_notify_alarm       boolean,
    created_at              timestamptz NOT NULL DEFAULT now(),
    modified_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, cell_id, alarm_code)
);
