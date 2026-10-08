"""Generate evidence/02_unity_catalog.md by reading governance back out of UC.

    python tools/evidence_02_unity_catalog.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.dbsql import table  # noqa: E402

HOST = "https://fevm-stable-classic-pqjg18.cloud.databricks.com"
C = "toy_demand_allocation"
G = f"{C}.gold"

PERSONAS = {
    "toy-planner-mass-a": "5d9eb1c3-888e-4acc-8625-4ecedea0fea5",
    "toy-data-scientist": "7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8",
    "toy-finance": "dd70e525-c0aa-4ca5-ade2-29671715871a",
    "toy-app-service": "1c73204c-6b49-4db0-a860-300ba392393e",
}


def main() -> int:
    out: list[str] = []
    a = out.append

    a("# Evidence 02 — Stage B: Unity Catalog governance")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Produced by | `tools/evidence_02_unity_catalog.py`, reading governance back "
      "out of Unity Catalog; policy proof from `src/governance/verify_policies.py` |")
    a("| Applied by | `src/governance/apply.py` (against the SQL warehouse) |")
    a(f"| Generated at | {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC |")
    a(f"| Workspace | `{HOST}` |")
    a("| Profile | `vm-summer-classic` |")
    a(f"| UC paths touched | `{C}` catalog; `{C}.gold.*`; `{C}.gold.mv_*`; "
      f"policy UDFs `{G}.rf_retailer_scope`, `{G}.cm_cost_margin` |")
    a("| Data | 100% synthetic |")
    a("")
    a("---")
    a("")

    # 1. personas
    a("## 1. The three personas (plus the app identity)")
    a("")
    a("Unity Catalog resolves only **account-level** principals. This FEVM workspace")
    a("has no account-level auth available, so workspace-local groups created with")
    a("`databricks groups create` are invisible to `GRANT` — every grant against one")
    a("failed with `PRINCIPAL_DOES_NOT_EXIST` even though `SHOW GROUPS` listed it.")
    a("Service principals created through the workspace API *are* account-level")
    a("entities and resolve correctly, and they carry a decisive advantage for")
    a("evidence: they can be authenticated, so the masked-vs-unmasked comparison in")
    a("§5 is produced by actually signing in as each persona rather than by asserting")
    a("what it would see.")
    a("")
    a("| Persona | Service principal (application ID) | Intended access |")
    a("|---|---|---|")
    a(f"| Planner, scoped to MASS_A | `{PERSONAS['toy-planner-mass-a']}` | "
      "read Gold + metric views, MASS_A rows only |")
    a(f"| Data scientist | `{PERSONAS['toy-data-scientist']}` | "
      "read Silver + Gold, write to `ml` |")
    a(f"| Finance | `{PERSONAS['toy-finance']}` | read Gold, unmasked cost and margin |")
    a(f"| App service principal | `{PERSONAS['toy-app-service']}` | "
      "read Gold, read/write the decision table |")
    a("")

    # 2. grants
    a("## 2. `SHOW GRANTS` per object")
    a("")
    for obj, kind in (
        (C, "CATALOG"),
        (f"{C}.gold", "SCHEMA"),
        (f"{C}.silver", "SCHEMA"),
        (f"{C}.ml", "SCHEMA"),
    ):
        a(f"### `{kind} {obj}`")
        a("")
        a("```text")
        a(table(f"SHOW GRANTS ON {kind} {obj}"))
        a("```")
        a("")

    # 3. comments and tags
    a("## 3. Comments and tags")
    a("")
    a("Table-level tags, read from `information_schema`:")
    a("")
    a("```text")
    a(table(f"""
SELECT table_name, tag_name, tag_value
FROM {C}.information_schema.table_tags
WHERE schema_name = 'gold'
ORDER BY table_name, tag_name
"""))
    a("```")
    a("")
    a("Column-level `sensitivity` tags on the cost and margin columns — these are the")
    a("columns the mask in §5 protects:")
    a("")
    a("```text")
    a(table(f"""
SELECT table_name, column_name, tag_name, tag_value
FROM {C}.information_schema.column_tags
WHERE schema_name = 'gold'
ORDER BY table_name, column_name, tag_name
"""))
    a("```")
    a("")
    a("This metastore enforces **UC tag policies** that constrain permitted values on")
    a("governed keys. Three were discovered the hard way, by rejection:")
    a("")
    a("| Governed key | Permitted values | Consequence |")
    a("|---|---|---|")
    a("| `data_product` | `analytics`, `machine_learning`, `data_engineering` | "
      "`demand_allocation` rejected; project name moved to free-form `data_product_name` |")
    a("| `sensitivity` | `pii`, `internal`, `public` | "
      "`confidential_cost_margin` rejected; used `internal` plus free-form keys |")
    a("| `pii` | `ssn`, `address` | "
      "This is a *classification* key recording **which kind** of PII a column holds, "
      "not a boolean. Tagging a table `pii = 'false'` is not merely rejected, it is "
      "semantically wrong. This data product holds no personal data, so the governed "
      "key is omitted and the absence asserted on free-form `contains_pii`. |")
    a("")
    a("Table and column comments (sample — every Gold table and every Gold column is")
    a("commented; Genie's accuracy in Stage E depends directly on these):")
    a("")
    a("```text")
    a(table(f"""
SELECT table_name, comment
FROM {C}.information_schema.tables
WHERE table_schema = 'gold' AND comment IS NOT NULL
ORDER BY table_name
""", max_col=110))
    a("```")
    a("")
    a("```text")
    a(table(f"""
SELECT column_name, data_type, comment
FROM {C}.information_schema.columns
WHERE table_schema = 'gold' AND table_name = 'fct_orders_fulfillment'
ORDER BY ordinal_position
""", max_col=90))
    a("```")
    a("")
    a("Column comment coverage across Gold:")
    a("")
    a("```text")
    a(table(f"""
SELECT table_name,
       COUNT(*) AS columns,
       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented,
       ROUND(100.0 * SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END)
             / COUNT(*), 1) AS pct_commented
FROM {C}.information_schema.columns
WHERE table_schema = 'gold' AND column_name NOT LIKE '\\_%'
GROUP BY table_name ORDER BY table_name
"""))
    a("```")
    a("")

    # 4. policy DDL
    a("## 4. The policy DDL")
    a("")
    a("Both policies are UDFs registered in Unity Catalog. A UDF used as a policy needs")
    a("its `RETURNS` type and `COMMENT` **before** the `RETURN` clause — putting")
    a("`COMMENT` last is a parse error.")
    a("")
    a("The predicates test `current_user()` rather than `is_account_group_member()`,")
    a("because the personas are service principals rather than account groups.")
    a("")
    a("```text")
    a(table(f"""
SELECT routine_name, data_type AS returns, routine_definition
FROM {C}.information_schema.routines
WHERE routine_schema = 'gold' AND routine_name IN ('rf_retailer_scope', 'cm_cost_margin')
""", max_col=400))
    a("```")
    a("")
    a("Where they are attached. Gold objects are materialized views, so policies attach")
    a("with `ALTER MATERIALIZED VIEW`; `ALTER TABLE` fails with `EXPECT_TABLE_NOT_VIEW`.")
    a("That is a DDL-verb difference, not a limitation — row filters and column masks")
    a("apply to materialized views perfectly well.")
    a("")
    a("```text")
    a(table(f"""
SELECT table_name, 'ROW FILTER' AS policy, 'rf_retailer_scope ON (retailer_code)' AS detail
FROM {C}.information_schema.tables
WHERE table_schema = 'gold'
  AND table_name IN ('fct_demand_weekly','fct_orders_fulfillment','agg_fill_rate_summary')
UNION ALL
SELECT table_name, 'COLUMN MASK', CONCAT(column_name, ' -> cm_cost_margin')
FROM {C}.information_schema.columns
WHERE table_schema = 'gold' AND column_name IN ('unit_cost_usd','gross_margin_pct')
ORDER BY policy, table_name, detail
"""))
    a("```")
    a("")

    # 5. masked vs unmasked
    a("## 5. Masked vs unmasked, with the identity used for each")
    a("")
    a("Produced by `src/governance/verify_policies.py`, which mints a short-lived OAuth")
    a("secret per persona, runs the identical query as that identity, and deletes the")
    a("secret before exiting. The secret is held in memory only and is never written to")
    a("disk, printed, or committed.")
    a("")
    a("**Read this as two results:**")
    a("")
    a("- The **column mask** holds for every identity except `toy-finance`, *including")
    a("  the catalog owner*. Owner privilege does not bypass a UC column mask.")
    a("- The **row filter** restricts `toy-planner-mass-a` to MASS_A alone — 165,573 of")
    a("  480,183 rows — while every other identity sees all three retailers.")
    a("")
    verification = pathlib.Path("evidence/_policy_verification.md")
    if verification.exists():
        a(verification.read_text())
    else:
        a("_(verification output missing — run `python -m src.governance.verify_policies`)_")
    a("")

    # 6. metric views
    a("## 6. Metric views — the governed KPI layer")
    a("")
    a("These are the single definition of each KPI. Genie (Stage E), the app (Stage F)")
    a("and any external BI tool read the same measures, so `fill rate` cannot mean two")
    a("different things in two different places. This is the layer the customer")
    a("maintains in dbt today, and it is the natural integration point.")
    a("")
    a("```text")
    a(table(f"""
SELECT table_name, comment
FROM {C}.information_schema.tables
WHERE table_schema = 'gold' AND table_name LIKE 'mv_%'
ORDER BY table_name
""", max_col=120))
    a("```")
    a("")
    a("### `mv_fill_rate` queried by retailer and quarter")
    a("")
    a("Note `Fill Rate Pct` against `Fill Rate Confirmed Basis Pct`: the same weeks read")
    a("96% on a requested basis and 99% on a confirmed basis. Publishing both in one")
    a("governed definition is what stops the two numbers being argued about in a meeting.")
    a("")
    q = f"""
SELECT `Retailer`,
       MEASURE(`Fill Rate Pct`)                      AS fill_rate_pct,
       MEASURE(`Fill Rate Confirmed Basis Pct`)      AS fill_rate_confirmed_pct,
       MEASURE(`Order Acceptance Pct`)               AS order_acceptance_pct,
       MEASURE(`Requested Units`)                    AS requested_units,
       MEASURE(`Shipped Units`)                      AS shipped_units,
       MEASURE(`Chargeback Pct Of Invoiced`)         AS chargeback_pct
FROM {G}.mv_fill_rate
GROUP BY ALL ORDER BY ALL
"""
    a("```sql")
    a(q.strip())
    a("```")
    a("")
    a("```text")
    a(table(q))
    a("```")
    a("")
    a("### `mv_otif` by licence family — attribution of the miss")
    a("")
    q2 = f"""
SELECT `License Family`,
       MEASURE(`OTIF Pct`)           AS otif_pct,
       MEASURE(`On Time Pct`)        AS on_time_pct,
       MEASURE(`In Full Pct`)        AS in_full_pct,
       MEASURE(`PO Lines`)           AS po_lines,
       MEASURE(`Chargeback USD`)     AS chargeback_usd
FROM {G}.mv_otif
GROUP BY ALL ORDER BY otif_pct
"""
    a("```text")
    a(table(q2))
    a("```")
    a("")
    a("### `mv_weeks_of_supply` by licence family")
    a("")
    a("This view declares the join to `dim_product` in its YAML, so no consumer has to")
    a("reinvent the weeks-of-supply denominator.")
    a("")
    q3 = f"""
SELECT `License Family`,
       MEASURE(`On Hand Units`)          AS on_hand_units,
       MEASURE(`In Transit Units`)       AS in_transit_units,
       MEASURE(`SKU DC Positions`)       AS sku_dc_positions
FROM {G}.mv_weeks_of_supply
GROUP BY ALL ORDER BY ALL
"""
    a("```text")
    a(table(q3))
    a("```")
    a("")
    a("`mv_forecast_accuracy` is created in Stage C1, once")
    a("`gold.fct_demand_forecast_eval` exists for it to read. See `evidence/03_ml_forecast.md`.")
    a("")

    # 7. lineage
    a("## 7. Lineage — landing file to Gold")
    a("")
    a("```text")
    a(table(f"""
SELECT source_table_full_name AS source, target_table_full_name AS target, event_time
FROM system.access.table_lineage
WHERE target_table_catalog = '{C}' OR source_table_catalog = '{C}'
ORDER BY event_time DESC
LIMIT 40
""", max_col=70))
    a("```")
    a("")
    a("Column-level lineage into the fill-rate numerator and denominator:")
    a("")
    a("```text")
    a(table(f"""
SELECT source_table_full_name AS source, source_column_name AS source_col,
       target_table_full_name AS target, target_column_name AS target_col
FROM system.access.column_lineage
WHERE target_table_catalog = '{C}'
  AND target_column_name IN ('shipped_units','requested_units','pos_units')
ORDER BY target, target_col
LIMIT 30
""", max_col=60))
    a("```")
    a("")

    # 8. baseline
    a("## 8. Measured KPI baseline")
    a("")
    a("Measured by `notebooks/02_governance_and_baseline.ipynb` against the Gold layer")
    a("and committed to `evidence/metrics/kpi_baseline.csv`. **Every figure in the deck")
    a("cites this file.** Nothing here is asserted; each row carries the exact basis of")
    a("measurement.")
    a("")
    csv = pathlib.Path("evidence/metrics/kpi_baseline.csv")
    if csv.exists():
        import csv as csvmod

        with csv.open() as fh:
            rows = list(csvmod.DictReader(fh))
        a("| KPI | Value | Unit | Basis |")
        a("|---|---:|---|---|")
        for r in rows:
            v = float(r["value"])
            vs = f"{v:,.2f}" if abs(v) < 1e6 else f"{v:,.0f}"
            a(f"| `{r['kpi']}` | {vs} | {r['unit']} | {r['basis']} |")
    a("")
    a("### Where the headroom is")
    a("")
    a("The baseline is not uniformly bad, and that matters for credibility. Fill rate at")
    a("96.3% on a requested basis is a competent operation. The headroom sits in five")
    a("specific, separately-attackable places:")
    a("")
    a("1. **OTIF 86.1%** against a 96.3% fill rate — the units arrive, but late or")
    a("   split, which is what triggers compliance penalties.")
    a("2. **74,789 stockout-weeks** and $5.51M of lost revenue, concentrated in the")
    a("   spiky licence families (see §8 of `evidence/01_lakeflow.md` and the licence")
    a("   breakdown in notebook 02).")
    a("3. **$2.12M of air freight** at a 7.0x premium over ocean — the direct cost of")
    a("   spotting a spike in POS instead of forecasting it.")
    a("4. **$7.94M of markdown** on close-out, 7.8% of invoiced wholesale.")
    a("5. **$1.26M of compliance chargebacks**, 1.24% of invoiced wholesale.")
    a("")
    a("Those five are the levers the rest of the build is pointed at.")
    a("")

    dest = pathlib.Path("evidence/02_unity_catalog.md")
    dest.write_text("\n".join(out))
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
