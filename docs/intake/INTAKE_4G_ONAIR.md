# Intake Agent — 4G On-Air · data model & flow (real client data)

> **Status:** design / reference only. This documents the **data model** and the **logical flow** for the
> 4G intake, derived from the real client files. It is *not* an ingestion spec — the two Excel files are
> treated purely as the **schema reference** for our tables. Nothing here inserts or stores data yet.
>
> **Scope:** 4G only (5G intake + ACS sheets are out of scope for now).
> **Supersedes:** the synthetic single-CSV model (`ns_site` / `ns_site_cell` / `ns_cluster_site`) — see
> *§6 Migration*.

---

## 1. What changed

Previously the whole intake came from **one synthetic CSV** (`…CellPlan_flat.csv`, one row per cell, all
fields in one file). The real client data is **two files that must be joined**:

| Reference file | Grain | Role |
|---|---|---|
| **4G On-Air** — `4G_On-Air_15th_Jul_2026_v1.xlsx` → sheet **`4G`** | one row per **site** (`SARF ID`) | *which sites are new & on-air* |
| **ECGI** — `ECGI_21th Jul'2026.xlsx` → sheet **`ECGI`** | one row per **cell** (`ECGI`) | *the cells + full RF detail of each site* |

The 4G file has the **site**, the ECGI file has its **cells**. The join is `SARF ID` (4G) = `Site ID` (ECGI).
So the intake model becomes **two tables** (site + cell) instead of the old one.

> ⚠️ **Format note:** the "4G" file is actually an **`.xlsb`** (Excel *binary*) with an `.xlsx` extension —
> it needs `pyxlsb`, not `openpyxl`. The ECGI file is a normal `.xlsx`.

---

## 2. The flow

![4G On-Air intake flow](intake_4g_flow.png)

> Diagram source: `intake_4g_flow.mmd` — re-render with
> `mmdc -i intake_4g_flow.mmd -o intake_4g_flow.png -b white -s 2`

**In words:** model each file as a table → filter the site table to the **latest-date, On-Air** sites →
for each, pull its cells from the cell table by `SARF ID = Site ID` → rename each cell to `Sec<CellID>` →
hand the assembled **site + cells** to the New On-Air Site **Lock Flow**.

**Which columns do the work** (every other column is stored as info on the row — see §4/§5):

| Role in the flow | File 1 · 4G On-Air (sheet `4G`) | File 2 · ECGI (sheet `ECGI`) |
|---|---|---|
| **Filter** | `Date of Intg` (keep latest) · `Status` (keep `On-Air`) | — |
| **Join key** | `SARF ID` | `Site ID`  *(= SARF ID)* |
| **Transform** | — | `CellID` → `'Sec' + CellID` |
| **Everything else → stored on the row** | `GC Name, Site Type, Bandwith, Locked status, Donor SARF ID, Phase, …` | `ECGI, PCI, RSI, Azimuth, TAC, DL-EARFCN, Mech/Elec Tilt, Ant Height, Lat/Long, …` |

So the **4G file decides *which* sites** (filter + key) and supplies the **site info**; the **ECGI file supplies
the cells** (keyed back by `Site ID`) and every per-cell RF field, with `CellID` reshaped to `Sec1/Sec2/…`.

**Storage is cell-wise.** The result is stored **one row per cell**: a site with 3 cells → **3 rows**, and
each row carries that site's 4G info **plus** that cell's ECGI info. (There is no separate "validate" or
"NE Status" step — the flow is just: filter sites → find their cells → one row per cell.)

> **Common-column note:** `GC Name` exists in **both** files and **disagrees for ~11% of sites** (6,377 of
> 57,622 — spelling variants like `Shimonoishiki` vs `Shimonoisshiki`, and different names like `Nagoyaharbor`
> vs `Nagoyaminato`). Since both files land on the same row, keep **both** (`gc_name_4g`, `gc_name_ecgi`);
> treat the **4G** value as authoritative for the site. Other same-concept/different-name pairs:
> `SARF ID`↔`Site ID`, `Bandwith`(`20Mhz`)↔`BANDWIDTH`(`twentyMHz`), `Status`↔`NE Status`, `Site Type`↔`TYPE`.

---

## 3. Filter / join / transform rules (grounded in the actual data)

### 3.1 Filter the site table (from `4G` sheet)
1. **Latest date** — `Date of Intg` is an **Excel serial** (e.g. `46218` = 2026-07-15). Keep the sites at
   the **latest integration date** (the newest on-air batch), not the whole 7-year history.
   *Observed range: 2019-02-03 → 2026-07-15.*
2. **On-Air only** — `Status` == `On-Air`.
   *Observed Status values: **On-Air 57,992**, Decommissioned 1,223, Demand Testing 94, Awaiting Commercial
   On-Air Date 63, Partial 45.*
   > Note: the header is `" Status"` with a **leading space** — trim on read.

### 3.2 Join site → cells
- Key: **`SARF ID`** (site table) = **`Site ID`** (cell table).
- ECGI is a **superset** (120,225 Site IDs — includes 5G/planned); the join filters it to the 4G sites.

### 3.3 Transform the cell id
- ECGI `CellID` is a bare integer (**1…18+**, observed). Certification wants **`Sec<n>`**:
  `1 → Sec1`, `2 → Sec2`, `3 → Sec3`, …
- `cell_id = "Sec" + str(CellID)`. Keep the raw number too (`cell_id_num`) for traceability.

### 3.4 Result — stored cell-wise
For each new on-air site, store **one row per cell**: a site with N cells → **N rows**, each row =
that site's 4G info **+** that cell's ECGI info. That set of rows is the input to the Lock Flow.

---

## 4. Data model — Site (from the `4G` sheet)

`ran.ns_site` — one row per site. **All 30 real columns** of the `4G` sheet (the ~15 trailing numeric
"columns" in the raw sheet are a pivot artifact and are **not** part of the model).

| Column | Source (`4G` sheet) | Type | Notes |
|---|---|---|---|
| `org_id`, `workspace_id` | — | text | tenant scope |
| `sarf_id` | `SARF ID` | text | **PK / join key** (= ECGI `Site ID`) |
| `sr_no` | `Sr.No` | int | |
| `gc_name` | `GC Name` | text | |
| `date_of_intg` | `Date of Intg` | date | convert Excel serial → date; **filter col** |
| `status` | `" Status"` | text | trim leading space; **filter col** (`On-Air`) |
| `partial_reason` | `Partial Reason` | text | |
| `pod` | `POD` | text | |
| `site_type` | `Site Type` | text | RIU / STU / … |
| `rdc_cvim` | `RDC/CVIM` | text | |
| `roaming` | `Roaming (0) / N Roaming (1)` | text | |
| `remark` | `Remark` | text | |
| `mbh` | `MBH` | text | |
| `partial_to_onair_date` | `Partial to On-Air date` | date? | often `-` |
| `new_1_7ghz` | `New 1.7 GHz` | text | |
| `decommissioned_date` | `Decommisioned Date` | date? | |
| `locked_status` | `Locked status` | text | feeds the Lock Flow |
| `locked_decommissioned_reason` | `Locked / Decommisioned Reason` | text | |
| `bandwidth` | `Bandwith` | text | e.g. `20Mhz` |
| `hoto_status` | `HOTO Status` | text | |
| `phase` | `Phase` | text | |
| `donor_sarf_id` | `Donor SARF ID` | text | |
| `cell_count` | `Cell count` | int | # of cells the site declares |
| `bh_ip` | `BH IP` | text | |
| `building_type` | `Building-Type` | text | |
| `rdc` | `RDC` | text | |
| `coverage_type` | `Coverage Type` | text | |
| `inventory` | `Inventory` | text | |
| `b3_uhn` | `B3 UHN` | text | |
| `onair_to_partial` | `On-Air to Partial` | text | |
| `commercial_onair_awaited` | `Commercial On_Air date Awaited to On-Air` | text | |

PK: `(org_id, workspace_id, sarf_id)`.

---

## 5. Data model — Cell (from the `ECGI` sheet)

`ran.ns_site_cell` — one row per cell. **All 42 columns** of the `ECGI` sheet, plus the derived `cell_id`.

| Column | Source (`ECGI` sheet) | Type | Notes |
|---|---|---|---|
| `org_id`, `workspace_id` | — | text | tenant scope |
| `ecgi` | `ECGI` | text | globally unique cell id — **PK** |
| `site_id` | `Site ID` | text | **FK → `ns_site.sarf_id`** |
| `cell_id_num` | `CellID` | int | raw sector number (1…18+) |
| `cell_id` | *derived* | text | **`'Sec' + CellID`** (Sec1/Sec2/…) |
| `cell_name` | `Cell Name` | text | e.g. `ENA1201000013_RIUD_1_4` |
| `ne_status` | `NE Status` | text | ONAIR / PLANNED |
| `date_of_onair` | `Date of On Air` | date | |
| `enodeb_id` | `enodeBid` | bigint | |
| `pci` | `PCI` | int | |
| `rsi` | `RSI` | int | root sequence index |
| `azimuth` | `Azimuth` | double | antenna bearing |
| `dl_earfcn` | `DL-EARFCN` | int | |
| `bandwidth` | `BANDWIDTH` | text | e.g. `twentyMHz` |
| `tac` | `TAC` | int | |
| `mech_antenna_tilt` | `Mech Antenna Tilt` | double | |
| `electrical_antenna_tilt` | `Electrical Antenna Tilt` | double | |
| `ant_height` | `Ant. Height` | double | |
| `ant_name` | `Ant. Name` | text | |
| `ant_vendor` | `Ant. Vender` | text | |
| `latitude` | `Latitude` | double | |
| `longitude` | `Longitude` | double | |
| `rrh_serial_number` | `RRH_serial number` | text | |
| `riu_serial_number` | `RIU_serial number` | text | |
| `riu_number` | `RIU Number` | int | |
| `clutter_type` | `Clutter TYPE` | text | |
| `vdu_name` | `vDU name` | text | |
| `vcu_name` | `vCU name` | text | |
| `vcu_id` | `vCU ID` | text | |
| `vdu_id` | `vDU ID` | text | |
| `gc_name` | `GC Name` | text | |
| `gc_code` | `GC Code` | text | |
| `ems_name` | `EMS Name` | text | |
| `software_build` | `Software Build` | text | |
| `type` | `TYPE` | text | RIUD_RRH / … |
| `cluster` | `Cluster` | text | |
| `zone` | `Zone` | text | |
| `city` | `City` | text | |
| `prefecture` | `Prefecture` | text | |
| `subregion` | `SubRegion` | text | |
| `region` | `Region` | text | |
| `region_manager` | `Region Manager` | text | |
| `zone_manager` | `Zone Manager` | text | |
| `cluster_owner` | `Cluster Owner` | text | |

PK: `(org_id, workspace_id, ecgi)`; index on `(org_id, workspace_id, site_id)` for the site→cells lookup.

**Relationship:** `ns_site (1) ──< ns_site_cell (N)` on `sarf_id = site_id`.

---

## 6. Migration from the synthetic schema

The old synthetic tables were shaped by the single CSV. The real ECGI cell data is actually **richer**, so
most old cell fields map cleanly; the site side changes more.

| Old (synthetic) | New source | Status |
|---|---|---|
| `ns_site_cell.pci` | ECGI `PCI` | ✅ direct |
| `ns_site_cell.root_seq_index` | ECGI `RSI` | ✅ direct |
| `ns_site_cell.antenna_bearing` | ECGI `Azimuth` | ✅ direct |
| `ns_site_cell.tac` | ECGI `TAC` | ✅ direct |
| `ns_site_cell.ret_tilt` / `mech_tilt` | ECGI `Electrical` / `Mech Antenna Tilt` | ✅ direct |
| `ns_site_cell.latitude/longitude` | ECGI `Latitude/Longitude` | ✅ direct |
| `ns_site_cell.cell_name` | ECGI `Cell Name` | ✅ direct |
| `ns_site_cell.enb_id` | ECGI `enodeBid` | ✅ direct |
| `ns_site_cell.earfcn_dl` | ECGI `DL-EARFCN` | ✅ direct |
| `ns_site_cell.height_m` | ECGI `Ant. Height` | ✅ direct |
| `ns_site_cell.cell_id` | `'Sec' + ECGI CellID` | ✅ transform |
| `ns_site_cell.pci_mod3` | `PCI % 3` | 🔁 derive |
| `ns_site_cell.bandwidth_mhz` | parse `BANDWIDTH` (`twentyMHz`→20) | 🔁 derive |
| `ns_site_cell.earfcn_ul` | — | ❌ ECGI has **DL only** |
| `ns_site_cell.max_tx_power` | — | ❌ not in ECGI |
| `ns_cluster_site` (map geo + bearings) | ECGI `Latitude/Longitude` + `Azimuth` | 🔁 fully derivable from ECGI |
| `ns_site.*` (planSiteId, siteType New-Coverage/Capacity…) | 4G sheet (`SARF ID`, `Site Type`=RIU/STU…) | ⚠️ **redesigned** — different columns & meanings |

**Net:** `ns_site_cell` and `ns_cluster_site` are largely a **remap** onto ECGI (plus 2 gaps). `ns_site` is a
**redesign** to the 4G sheet's columns. `ns_workflow` / `ns_workflow_event` (the pipeline state machine) are
**unaffected** — they key off `site_id`, which is now `SARF ID`.

**Old tables to drop/replace:** the synthetic `ns_site` / `ns_site_cell` / `ns_cluster_site` column sets are
replaced by §4 / §5. Decision to confirm: **redesign in place** (keep the names, swap columns) vs **new
tables** (`ns_site_4g` / `ns_cell_ecgi`) — see open questions.

---

## 7. Where this fits — `workflow_id` & Airflow

Intake is the **entry point** of the pipeline, so it establishes the two keys every later agent joins on
(full picture: `docs/DATA_MODEL/AGENT_DATA_RELATIONSHIPS.md`):

- **`site_id`** (= `SARF ID` = ECGI `Site ID`) — the site **entity**. This is the anchor Fault, Config and
  KPI-RCA all key back to for the same physical site. `cell_id` (`Sec1/Sec2…`) is the shared sub-grain.
- **`workflow_id`** — Intake **opens one `ns_workflow` row per new on-air site** and stamps it
  `current_stage = intake · passed`. That row is the **thread** the rest of the pipeline advances, and the
  gate the next stage reads (Fault picks up sites at `intake · passed`).

**Under Airflow** (the intended governor): Intake is the **first task** of the per-site DAG run, and
**`workflow_id` = the Airflow DAG run** for that site (store `airflow_run_id` on `ns_workflow`). Intake mints
the site + cells (§4/§5), opens the workflow, and hands off; Airflow then drives Fault → Config → KPI-RCA
with the state-gated dependencies (park-and-re-trigger). So Intake's job is: **turn the two files into the
site + cell rows and open the workflow that threads everything downstream.**

## 8. Data-quality flags & open questions

1. **563 On-Air sites are missing from ECGI** (57,992 On-Air → 57,429 matched). How should a site with **no
   cells** be handled — skip, park, or flag for the client?
2. **"Latest date" definition** — does it mean sites at `max(Date of Intg)` exactly, or "on/after the file's
   cutoff date"? This decides how many sites enter the Lock Flow per cycle.
3. **`GC Name` conflict** — differs for ~11% of sites between the two files. Confirmed approach: keep both
   (`gc_name_4g`, `gc_name_ecgi`), 4G authoritative. OK, or pick just one?
4. **Two ECGI gaps** — `earfcn_ul` and `max_tx_power` don't exist in ECGI. Are they needed downstream
   (fault/config/KPI), and if so, from which file?
5. **Storage shape** — one **cell-wise** table (site info repeated on each cell row), or two tables
   (site + cell) joined on read? The flow above assumes cell-wise per your note.
6. **Out of scope now:** 5G intake file, the `ACS`/`Phase_01` sheets, and the ECGI `Summary.` sheet.

---

*Next agents (fault, config, KPI-RCA) will get the same treatment — model from the real client files, then
map/replace the synthetic schema.*
