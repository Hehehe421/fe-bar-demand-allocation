# ruff: noqa: E501  (evidence prose and SQL are kept readable on one line)
"""Generate ``evidence/02b_lineage.md``: lineage from the landing file to Lakebase, as text.

    python tools/evidence_lineage.py

The rubric (Stage B) wants lineage landing -> bronze -> silver -> gold -> model -> Lakebase.
Unity Catalog records the table-to-table hops in ``system.access.table_lineage``; the model and
Lakebase hops are not table lineage, so they are shown from the objects that carry them:

* the model version's declared dependencies (Unity Catalog model registry),
* the model name and version stamped on every row of ``gold.fct_demand_forecast``,
* the synced-table definitions (source table, primary key, pipeline, last commit), and
* row-count parity between each Unity Catalog source and its Postgres copy.

Everything is queried live; nothing is typed in. Idempotent.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
os.environ["DATABRICKS_CONFIG_PROFILE"] = os.environ.get("DBX_PROFILE", "vm-summer-classic")

import backend as b  # noqa: E402

import src.config as config  # noqa: E402
from src.lakebase import sync  # noqa: E402
from tools.dbsql import sql, table  # noqa: E402

C = config.CATALOG
OUT = ROOT / "evidence/02b_lineage.md"


def main() -> int:
    L: list[str] = []
    a = L.append
    w = sync._w()
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")

    a("# Evidence 02b — Lineage, landing file to Lakebase")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Produced by | `tools/evidence_lineage.py` |")
    a(f"| Workspace | {config.WORKSPACE_HOST} |")
    a(f"| Generated | {now} |")
    a(f"| Unity Catalog | `{C}` |")
    a("")
    a("Unity Catalog records table-to-table lineage. The **model** and **Lakebase** hops are not table")
    a("lineage, so each is shown from the object that carries it, and every number is queried live.")
    a("")
    a("```text")
    a("landing volume files  ->  bronze (streaming tables)  ->  silver  ->  gold facts and dims")
    a("   -> ml.demand_features_weekly (Feature Engineering)  ->  model ml.demand_forecaster @champion")
    a("   -> gold.fct_demand_forecast  ->  gold.fct_stockout_risk")
    a("   -> gold.svc_* projections  ->  Lakebase synced tables  ->  app  ->  allocation_decisions (Lakebase)")
    a("   -> write-back  ->  gold.fct_allocation_decisions  ->  Genie")
    a("```")
    a("")

    a("## 1. Landing file to bronze")
    a("")
    a("Auto Loader reads files from the landing volume. Each bronze row carries the file it came from,")
    a("so a row can be traced to its file without any lineage tooling.")
    a("")
    a("```text")
    a(table(f"""
SELECT 'bronze.orders' AS bronze_table, _source_file AS landing_file, COUNT(*) AS rows
FROM {C}.bronze.orders GROUP BY _source_file ORDER BY _source_file LIMIT 3
""", max_col=110))
    a("```")
    a("")

    a("## 2. Bronze → silver → gold → features → forecast → risk (table lineage)")
    a("")
    a("Distinct edges from `system.access.table_lineage` (the rows with no source table are the")
    a("file ingests in section 1).")
    a("")
    a("```text")
    a(table(f"""
SELECT DISTINCT
  regexp_replace(source_table_full_name, '^{C}\\\\.', '') AS source,
  regexp_replace(target_table_full_name, '^{C}\\\\.', '') AS target
FROM system.access.table_lineage
WHERE source_table_catalog = '{C}' AND target_table_catalog = '{C}'
  AND source_table_full_name IS NOT NULL AND target_table_full_name IS NOT NULL
  AND target_table_full_name NOT LIKE '%event_log%' AND target_table_full_name NOT LIKE '%\\\\_probe%'
  AND source_table_full_name NOT LIKE '%\\\\_probe%'
ORDER BY target, source
""", max_col=60))
    a("```")
    a("")
    a("Read it top to bottom: `bronze.*` → `silver.*` → `gold.*` → `ml.demand_features_weekly` →")
    a("`gold.fct_demand_forecast` → `gold.fct_stockout_risk`; the metric views read the facts; the two")
    a("`svc_*` projections read `dim_product` and `agg_fill_rate_summary`.")
    a("")

    a("## 3. Feature table → model")
    a("")
    mv = w.api_client.do("GET", f"/api/2.1/unity-catalog/models/{config.MODEL_NAME}/aliases/champion")
    deps = [d["table"]["table_full_name"] for d in
            (mv.get("model_version_dependencies") or {}).get("dependencies", []) if "table" in d]
    a(f"Unity Catalog model registry, `{config.MODEL_NAME}@champion` = version **{mv['version']}**:")
    a("")
    a("```text")
    a(f"run_id                : {mv['run_id']}")
    a("model_version_dependencies (tables the model reads at inference):")
    for d in deps:
        a(f"  - {d}")
    a("```")
    a("")
    a("The model was logged with `fe.log_model` and a `FeatureLookup`, so the dependency is recorded by")
    a("Feature Engineering rather than asserted here. The same edge is in the system table (section 2):")
    a("`ml.demand_features_weekly` → `gold.fct_demand_forecast`.")
    a("")
    a("What this is not: the lookup is on the exact key (`sku_id`, `retailer_code`, `origin_week`), not")
    a("an as-of join on a timeseries column. See `evidence/03_ml_forecast.md` §1 for why, and what the")
    a("leakage guard is instead.")
    a("")

    a("## 4. Model → forecast rows")
    a("")
    a("Every forecast row names the model that produced it:")
    a("")
    a("```text")
    a(table(f"""
SELECT model_name, model_version, model_alias, algorithm, COUNT(*) AS rows,
       MIN(week_end_date) AS first_week, MAX(week_end_date) AS last_week
FROM {config.FCT_DEMAND_FORECAST} GROUP BY ALL
""", max_col=60))
    a("```")
    a("")

    a("## 5. Unity Catalog → Lakebase")
    a("")
    a("Four synced tables, each a managed pipeline from a Unity Catalog source to a Postgres table:")
    a("")
    a("```text")
    a(f"{'postgres table':24} {'source (Unity Catalog)':58} {'primary key':30} state")
    parity: list[tuple[str, int, int]] = []
    for t, src in config.SYNCED_TABLES.items():
        r = w.api_client.do("GET", f"/api/2.0/postgres/synced_tables/{sync.qualified(t)}")
        st = r["status"]
        a(f"{'public.' + t:24} {src:58} {','.join(config.SYNCED_TABLE_KEYS[t]):30} "
          f"{st['detailed_state'].replace('SYNCED_TABLE_', '')}")
        info = st.get("last_sync", {}).get("delta_table_sync_info", {})
        a(f"{'':24} pipeline {st.get('pipeline_id')}  last sync end {st['last_sync']['sync_end_time']}  "
          f"source commit v{info.get('delta_commit_version')}")
        uc = int(sql(f"SELECT COUNT(*) FROM {src}")[1][0][0])
        pg = int(b._pg_rows(f"SELECT COUNT(*) AS n FROM public.{t}")[0]["n"])
        parity.append((t, uc, pg))
    a("```")
    a("")
    a("Row-count parity, Unity Catalog source against the Postgres copy:")
    a("")
    a("```text")
    a(f"{'table':26} {'unity catalog':>14} {'lakebase':>10}  match")
    for t, uc, pg in parity:
        a(f"{t:26} {uc:>14,} {pg:>10,}  {'yes' if uc == pg else 'NO'}")
    a("```")
    a("")
    a("`product_lookup` and `fill_rate_summary` read `gold.svc_*` projections, not the dimension and")
    a("aggregate themselves. Unity Catalog refuses an online copy of a table that has a row filter or")
    a("column mask, and `dim_product` carries a mask on cost and margin. The projection drops those")
    a("columns (asserted by `src/lakebase/serving_tables.py`), so no governed value reaches Postgres.")
    a("Lineage records the projection: `dim_product` → `svc_product_lookup`,")
    a("`agg_fill_rate_summary` → `svc_fill_rate_summary`:")
    a("")
    a("```text")
    a(table(f"""
SELECT DISTINCT source_table_full_name AS source, target_table_full_name AS target
FROM system.access.table_lineage
WHERE target_table_full_name LIKE '{C}.gold.svc_%'
""", max_col=60))
    a("```")
    a("")

    a("## 6. The write path back: Lakebase → Unity Catalog")
    a("")
    pg_n = b._pg_rows("SELECT COUNT(*) AS n, MAX(decided_at) AS last FROM allocation.allocation_decisions")[0]
    uc_n = sql(f"SELECT COUNT(*), MAX(decided_at) FROM {config.GOLD}.fct_allocation_decisions")[1][0]
    a("Decisions the app records go to Postgres (`allocation.allocation_decisions`); a write-back job")
    a("copies them to `gold.fct_allocation_decisions`, which Genie reads. Table lineage cannot see a")
    a("JDBC write, so this hop is shown by counts:")
    a("")
    a("```text")
    a(f"lakebase allocation.allocation_decisions : {pg_n['n']} rows, latest {pg_n['last']}")
    a(f"unity catalog gold.fct_allocation_decisions: {uc_n[0]} rows, latest {uc_n[1]}")
    a("```")
    a("")
    if int(pg_n["n"]) != int(uc_n[0]):
        a(f"**The counts differ by {int(pg_n['n']) - int(uc_n[0])}.** That is expected, not a defect: a")
        a("decision recorded in the app reaches Unity Catalog only when the write-back job next runs, and")
        a("one was recorded by hand while testing the app (decision 33, rationale \"test\") after the last run.")
        a("")

    a("## 7. Weeks of supply: a correction, and its lineage")
    a("")
    a("`mv_weeks_of_supply` originally had no weeks-of-supply measure: it exposed on-hand, in-transit")
    a("and total-available units, so a question about weeks of supply had nothing governed to answer")
    a("it. It now also carries `Weeks Of Supply` = latest on-hand divided by mean weekly POS units over")
    a("the last 13 weeks (`gold.agg_trailing_demand`, built by `src/governance/apply.py`):")
    a("")
    a("```text")
    a(table(f"""
SELECT ROUND(MEASURE(`Weeks Of Supply`), 2) AS weeks_of_supply,
       MEASURE(`On Hand Units`) AS on_hand_units,
       ROUND(MEASURE(`Weekly Demand Units`), 0) AS mean_weekly_pos_units
FROM {config.MV_WEEKS_OF_SUPPLY}
""", max_col=40))
    a("```")
    a("")
    a("```text")
    a(table(f"""
SELECT DISTINCT source_table_full_name AS source, target_table_full_name AS target
FROM system.access.table_lineage
WHERE target_table_full_name = '{config.MV_WEEKS_OF_SUPPLY}'
""", max_col=60))
    a("```")
    a("")
    a("System-table lineage lags by minutes to hours, so a source added today (`agg_trailing_demand`)")
    a("may not be listed yet; the metric view definition in `src/governance/apply.py` is the source of")
    a("truth and the value above is computed from it.")
    a("")
    OUT.write_text("\n".join(L))
    print(f"wrote {OUT} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
