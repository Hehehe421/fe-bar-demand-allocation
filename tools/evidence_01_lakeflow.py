"""Generate evidence/01_lakeflow.md from the real pipeline run.

Every number in the output file is read back out of Unity Catalog or the
pipeline event log at generation time. Nothing is transcribed by hand.

    python tools/evidence_01_lakeflow.py <pipeline_id> <update_id>
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from databricks.sdk import WorkspaceClient  # noqa: E402

from tools.dbsql import table  # noqa: E402

PROFILE = "vm-summer-classic"
HOST = "https://fevm-stable-classic-pqjg18.cloud.databricks.com"
CATALOG = "toy_demand_allocation"
LANDING = f"/Volumes/{CATALOG}/bronze/landing"

BRONZE = [
    "product_master", "pos_weekly", "orders", "inventory_snapshots",
    "inbound_shipments", "markdown_clearance",
]
SILVER = BRONZE
GOLD = [
    "dim_product", "dim_retailer_dc", "fct_demand_weekly", "fct_orders_fulfillment",
    "fct_inbound_eta", "fct_inventory_position", "fct_markdown", "agg_fill_rate_summary",
]


def main() -> int:
    pipeline_id = sys.argv[1]
    update_id = sys.argv[2]
    w = WorkspaceClient(profile=PROFILE)

    p = w.pipelines.get(pipeline_id)
    upd = w.pipelines.get_update(pipeline_id, update_id).update
    event_log = f"{CATALOG}.bronze.event_log_{pipeline_id.replace('-', '_')}"

    out: list[str] = []
    a = out.append

    a("# Evidence 01 — Stage A: Lakeflow declarative pipeline (ingest + medallion)")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Produced by | `tools/evidence_01_lakeflow.py` reading Unity Catalog and the "
      "pipeline event log |")
    a(f"| Generated at | {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC |")
    a(f"| Workspace | `{HOST}` |")
    a("| Profile | `vm-summer-classic` |")
    a(f"| Pipeline | `{p.name}` (`{pipeline_id}`) |")
    a(f"| Update | `{update_id}` |")
    a(f"| UC paths touched | `{CATALOG}.bronze.*`, `{CATALOG}.silver.*`, "
      f"`{CATALOG}.gold.*` |")
    a(f"| Landing volume | `{LANDING}` |")
    a("| Data | 100% synthetic, from `src/datagen/generate.py` (seed 20260408) |")
    a("")
    a("---")
    a("")

    # ---- 1. pipeline identity and final state -------------------------
    a("## 1. Pipeline update: identity and final state")
    a("")
    a("```text")
    a(f"pipeline_id    : {pipeline_id}")
    a(f"pipeline_name  : {p.name}")
    a(f"update_id      : {update_id}")
    a(f"state          : {upd.state}")
    a(f"creation_time  : {upd.creation_time}")
    a(f"full_refresh   : {upd.full_refresh}")
    a(f"cause          : {upd.cause}")
    a(f"serverless     : {p.spec.serverless if p.spec else None}")
    a(f"target catalog : {p.spec.catalog if p.spec else None}")
    a(f"default schema : {p.spec.schema if p.spec else None}")
    a("libraries      :")
    for lib in (p.spec.libraries or []) if p.spec else []:
        a(f"  - {lib.file.path if lib.file else lib}")
    a("configuration  :")
    for k, v in sorted(((p.spec.configuration or {}) if p.spec else {}).items()):
        a(f"  {k} = {v}")
    a("```")
    a("")

    # ---- 2. expectations from the event log ---------------------------
    a("## 2. Data-quality expectations: per-expectation pass / fail / drop counts")
    a("")
    a("Read from the pipeline event log, not asserted. These counts are the evidence")
    a("that governance is actually running: the generator injects negative quantities,")
    a("duplicate grain keys, null on-hand readings and orphan SKU references on")
    a("purpose, and the `_or_drop` expectations below are what remove them before Gold.")
    a("")
    a("Expectation semantics in this pipeline:")
    a("")
    a("- **drop** (`expect_all_or_drop`) — invalid rows, removed. `failed_records` here")
    a("  equals rows dropped.")
    a("- **warn** (`expect_all`) — rows usable but incomplete; counted and *kept*.")
    a("  Dropping a POS row because its on-hand reading is missing would understate")
    a("  demand, so these are deliberately retained.")
    a("")
    a("```sql")
    exp_q = f"""
SELECT
  e.dataset                                   AS dataset,
  e.name                                      AS expectation,
  SUM(e.passed_records)                       AS passed_records,
  SUM(e.failed_records)                       AS failed_records,
  ROUND(100.0 * SUM(e.failed_records)
        / NULLIF(SUM(e.passed_records) + SUM(e.failed_records), 0), 4) AS pct_failed
FROM (
  SELECT EXPLODE(
    FROM_JSON(
      GET_JSON_OBJECT(details, '$.flow_progress.data_quality.expectations'),
      'array<struct<name:string,dataset:string,passed_records:bigint,failed_records:bigint>>'
    )
  ) AS e
  FROM {event_log}
  WHERE event_type = 'flow_progress'
    AND origin.update_id = '{update_id}'
)
WHERE e.name IS NOT NULL
GROUP BY e.dataset, e.name
ORDER BY failed_records DESC, dataset, expectation
""".strip()
    a(exp_q)
    a("```")
    a("")
    a("```text")
    a(table(exp_q))
    a("```")
    a("")

    # expectation totals
    tot_q = f"""
SELECT
  SUM(e.passed_records) AS total_passed,
  SUM(e.failed_records) AS total_failed_or_dropped,
  COUNT(DISTINCT e.name) AS distinct_expectations,
  COUNT(DISTINCT e.dataset) AS datasets_with_expectations
FROM (
  SELECT EXPLODE(
    FROM_JSON(
      GET_JSON_OBJECT(details, '$.flow_progress.data_quality.expectations'),
      'array<struct<name:string,dataset:string,passed_records:bigint,failed_records:bigint>>'
    )
  ) AS e
  FROM {event_log}
  WHERE event_type = 'flow_progress' AND origin.update_id = '{update_id}'
) WHERE e.name IS NOT NULL
""".strip()
    a("Totals across all expectations in this update:")
    a("")
    a("```text")
    a(table(tot_q))
    a("```")
    a("")

    # ---- 3. flow completion ------------------------------------------
    a("## 3. Every flow in the update, with its final state")
    a("")
    flow_q = f"""
SELECT
  origin.flow_name AS flow_name,
  GET_JSON_OBJECT(details, '$.flow_progress.status') AS status,
  MAX(timestamp) AS last_event
FROM {event_log}
WHERE event_type = 'flow_progress'
  AND origin.update_id = '{update_id}'
  AND GET_JSON_OBJECT(details, '$.flow_progress.status') IS NOT NULL
  AND origin.flow_name IS NOT NULL
GROUP BY origin.flow_name, GET_JSON_OBJECT(details, '$.flow_progress.status')
HAVING status IN ('COMPLETED', 'FAILED', 'SKIPPED', 'EXCLUDED')
ORDER BY flow_name
""".strip()
    a("```text")
    a(table(flow_q))
    a("```")
    a("")

    # ---- 4. row counts per layer -------------------------------------
    a("## 4. Row counts per medallion layer")
    a("")
    a("Counted directly against Unity Catalog after the update completed.")
    a("")
    parts = []
    for lyr, tbls in (("bronze", BRONZE), ("silver", SILVER), ("gold", GOLD)):
        for t in tbls:
            parts.append(
                f"SELECT '{lyr}' AS layer, '{t}' AS table_name, "
                f"COUNT(*) AS row_count FROM {CATALOG}.{lyr}.{t}"
            )
    counts_q = "\nUNION ALL\n".join(parts) + "\nORDER BY layer, table_name"
    a("```text")
    a(table(counts_q))
    a("```")
    a("")

    a("### Bronze to Silver attrition (what the expectations removed)")
    a("")
    attr_q = f"""
SELECT
  b.table_name,
  b.bronze_rows,
  s.silver_rows,
  b.bronze_rows - s.silver_rows AS rows_removed,
  ROUND(100.0 * (b.bronze_rows - s.silver_rows) / NULLIF(b.bronze_rows, 0), 4) AS pct_removed
FROM (
  {" UNION ALL ".join(
      f"SELECT '{t}' AS table_name, COUNT(*) AS bronze_rows FROM {CATALOG}.bronze.{t}"
      for t in BRONZE)}
) b
JOIN (
  {" UNION ALL ".join(
      f"SELECT '{t}' AS table_name, COUNT(*) AS silver_rows FROM {CATALOG}.silver.{t}"
      for t in SILVER)}
) s USING (table_name)
ORDER BY rows_removed DESC
""".strip()
    a("```text")
    a(table(attr_q))
    a("```")
    a("")

    # ---- 5. the case-vs-each normalisation ---------------------------
    a("## 5. The unit normalisation, proven")
    a("")
    a("`ETAIL_C` reports POS and on-hand in CASES; every other retailer reports eaches.")
    a("Left unconverted, a third of the portfolio looks permanently stocked out. Silver")
    a("multiplies those rows by the item's case pack and records that it did so.")
    a("")
    norm_q = f"""
SELECT
  retailer_code,
  reported_uom,
  unit_conversion_applied,
  COUNT(*)                        AS rows,
  MIN(conversion_factor)          AS min_factor,
  MAX(conversion_factor)          AS max_factor,
  SUM(pos_units)                  AS pos_units_each
FROM {CATALOG}.silver.pos_weekly
GROUP BY retailer_code, reported_uom, unit_conversion_applied
ORDER BY retailer_code
""".strip()
    a("```sql")
    a(norm_q)
    a("```")
    a("")
    a("```text")
    a(table(norm_q))
    a("```")
    a("")

    # ---- 6. gold sample ----------------------------------------------
    a("## 6. `gold.fct_demand_weekly` — 10-row sample")
    a("")
    a("The weekly demand fact that Stage C1 trains on. `true_demand_units` is")
    a("unconstrained consumer demand, `pos_units` is what was actually served, so the")
    a("difference is lost sales.")
    a("")
    sample_q = f"""
SELECT sku_id, retailer_code, dc_code, week_end_date, pos_units, true_demand_units,
       on_hand_units, lost_units, lost_revenue_usd, is_stockout_week, promo_flag,
       brand, license_family, lifecycle_stage
FROM {CATALOG}.gold.fct_demand_weekly
WHERE is_stockout_week = true
ORDER BY lost_revenue_usd DESC
LIMIT 10
""".strip()
    a("```sql")
    a(sample_q)
    a("```")
    a("")
    a("```text")
    a(table(sample_q))
    a("```")
    a("")

    a("### Provenance columns carried through to Gold")
    a("")
    prov_q = f"""
SELECT sku_id, week_end_date, _ingested_at,
       REGEXP_EXTRACT(_source_file, '[^/]+$', 0) AS source_file
FROM {CATALOG}.gold.fct_demand_weekly
LIMIT 5
""".strip()
    a("```text")
    a(table(prov_q))
    a("```")
    a("")

    a("### Grain uniqueness check on the weekly fact")
    a("")
    grain_q = f"""
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT sku_id, retailer_code, dc_code, week_end_date) AS distinct_grain_keys,
       COUNT(*) - COUNT(DISTINCT sku_id, retailer_code, dc_code, week_end_date) AS duplicates
FROM {CATALOG}.gold.fct_demand_weekly
""".strip()
    a("```text")
    a(table(grain_q))
    a("```")
    a("")

    # ---- 7. landing files -------------------------------------------
    a("## 7. Landing-zone files the pipeline ingested")
    a("")
    files_q = f"""
SELECT
  REGEXP_EXTRACT(_source_file, 'landing/([^/]+)/', 1) AS source_dir,
  COUNT(DISTINCT _source_file) AS files_ingested,
  COUNT(*) AS rows_ingested
FROM {CATALOG}.bronze.pos_weekly
GROUP BY 1
UNION ALL
SELECT 'orders', COUNT(DISTINCT _source_file), COUNT(*) FROM {CATALOG}.bronze.orders
UNION ALL
SELECT 'inventory_snapshots', COUNT(DISTINCT _source_file), COUNT(*)
  FROM {CATALOG}.bronze.inventory_snapshots
UNION ALL
SELECT 'inbound_shipments', COUNT(DISTINCT _source_file), COUNT(*)
  FROM {CATALOG}.bronze.inbound_shipments
UNION ALL
SELECT 'markdown_clearance', COUNT(DISTINCT _source_file), COUNT(*)
  FROM {CATALOG}.bronze.markdown_clearance
UNION ALL
SELECT 'product_master', COUNT(DISTINCT _source_file), COUNT(*)
  FROM {CATALOG}.bronze.product_master
ORDER BY source_dir
""".strip()
    a("```text")
    a(table(files_q))
    a("```")
    a("")

    a("## 8. Production ingest path")
    a("")
    a("In production this stage reads from Microsoft SQL Server through **Lakeflow")
    a("Connect**, landing CDC output into Bronze with the same expectations and the")
    a("same Gold contract downstream. This build ingests committed synthetic flat files")
    a("from a Unity Catalog volume via Auto Loader instead. **No SQL Server connector")
    a("was run here** — the substitution is deliberate and is stated in the README.")
    a("")

    dest = pathlib.Path("evidence/01_lakeflow.md")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text("\n".join(out))
    print(f"wrote {dest} ({len('\n'.join(out))} bytes)")

    # surface the key numbers on stdout too
    print()
    print(table(tot_q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
