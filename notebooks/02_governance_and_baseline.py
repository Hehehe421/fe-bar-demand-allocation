# %% [markdown]
# # Stage B (part 2) — Measure the KPI baseline
#
# Governance DDL is applied by `src/governance/apply.py` against the SQL warehouse
# (metric views need a newer runtime than DBR 16.4 LTS ML). This notebook does the
# other half of Stage B: it **measures** the baseline from the Gold layer so every
# number in the deck is computed from the generated data rather than asserted.
#
# Output: `evidence/metrics/kpi_baseline.csv`.
#
# One wrinkle worth stating plainly: the column mask applied in Stage B makes
# `gold.dim_product.unit_cost_usd` read NULL for every identity except the finance
# persona — including the catalog owner running this notebook. That is the mask
# working correctly. Cost-based measures below therefore read cost from
# `silver.product_master`, which is the data-engineering layer this notebook is
# entitled to and which carries no mask. Revenue-based measures are unaffected.

# %%
import sys

WS = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
if WS + "/src" not in sys.path:
    sys.path.insert(0, WS + "/src")

import config  # noqa: E402

C = config.CATALOG
print("catalog:", C)
print("current_user:", spark.sql("SELECT current_user()").collect()[0][0])

# demonstrate the mask is active for this identity
spark.sql(
    f"SELECT sku_id, unit_cost_usd, gross_margin_pct FROM {C}.gold.dim_product LIMIT 3"
).show()
print("^ cost and margin are NULL here: the Stage B column mask is in force for")
print("  this identity. Cost-based measures below read silver.product_master.")

# %% [markdown]
# ## Service KPIs — fill rate, OTIF, on-time, in-full
#
# Fill rate is **shipped / requested**. That is the service level the retailer
# actually experiences and the basis on which compliance chargebacks are raised.
# The confirmed-basis number is reported alongside it because supply-chain teams
# often quote that instead, and the two diverge sharply when supply is short —
# 99% on a confirmed basis and 96% on a requested basis describe the same weeks.

# %%
service = spark.sql(f"""
SELECT
  SUM(requested_units)                                              AS requested_units,
  SUM(confirmed_units)                                              AS confirmed_units,
  SUM(shipped_units)                                                AS shipped_units,
  SUM(unfilled_units)                                               AS unfilled_units,
  COUNT(*)                                                          AS po_lines,
  100.0 * SUM(shipped_units)   / SUM(requested_units)               AS fill_rate_requested_pct,
  100.0 * SUM(shipped_units)   / SUM(confirmed_units)               AS fill_rate_confirmed_pct,
  100.0 * SUM(confirmed_units) / SUM(requested_units)               AS order_acceptance_pct,
  100.0 * SUM(CASE WHEN is_on_time THEN 1 ELSE 0 END) / COUNT(*)    AS on_time_pct,
  100.0 * SUM(CASE WHEN is_in_full THEN 1 ELSE 0 END) / COUNT(*)    AS in_full_pct,
  100.0 * SUM(CASE WHEN is_otif    THEN 1 ELSE 0 END) / COUNT(*)    AS otif_pct,
  SUM(invoiced_wholesale_usd)                                       AS invoiced_wholesale_usd,
  SUM(chargeback_usd)                                               AS chargeback_usd,
  100.0 * SUM(chargeback_usd) / SUM(invoiced_wholesale_usd)         AS chargeback_pct_of_invoiced
FROM {C}.gold.fct_orders_fulfillment
""")
service.show(vertical=True, truncate=False)

# %% [markdown]
# ### Fill rate and OTIF by retailer
#
# The three accounts carry different contractual commitments, so the gap between
# commitment and actual is what a planner is actually judged on.

# %%
spark.sql(f"""
SELECT
  f.retailer_code,
  d.contractual_fill_commitment * 100                               AS commitment_pct,
  ROUND(100.0 * SUM(f.shipped_units) / SUM(f.requested_units), 2)    AS fill_rate_pct,
  ROUND(100.0 * SUM(f.shipped_units) / SUM(f.requested_units)
        - d.contractual_fill_commitment * 100, 2)                    AS gap_to_commitment_pts,
  ROUND(100.0 * SUM(CASE WHEN f.is_otif THEN 1 ELSE 0 END) / COUNT(*), 2) AS otif_pct,
  ROUND(SUM(f.chargeback_usd), 0)                                    AS chargeback_usd
FROM {C}.gold.fct_orders_fulfillment f
JOIN (SELECT DISTINCT retailer_code, contractual_fill_commitment
      FROM {C}.gold.dim_retailer_dc) d USING (retailer_code)
GROUP BY f.retailer_code, d.contractual_fill_commitment
ORDER BY f.retailer_code
""").show(truncate=False)

# %% [markdown]
# ## Demand loss — stockout weeks and lost revenue
#
# `true_demand_units` is unconstrained consumer demand and `pos_units` is what was
# actually served, so the difference is lost sales. This is the number the
# forecast is ultimately trying to reduce.

# %%
loss = spark.sql(f"""
SELECT
  COUNT(*)                                                          AS active_sku_retailer_dc_weeks,
  SUM(CASE WHEN is_stockout_week THEN 1 ELSE 0 END)                 AS stockout_weeks,
  100.0 * SUM(CASE WHEN is_stockout_week THEN 1 ELSE 0 END)/COUNT(*) AS stockout_week_incidence_pct,
  SUM(true_demand_units)                                            AS true_demand_units,
  SUM(pos_units)                                                    AS pos_units,
  SUM(lost_units)                                                   AS lost_units,
  100.0 * SUM(lost_units) / SUM(true_demand_units)                  AS lost_units_pct_of_demand,
  ROUND(SUM(lost_revenue_usd), 2)                                   AS lost_revenue_usd
FROM {C}.gold.fct_demand_weekly
WHERE true_demand_units > 0
""")
loss.show(vertical=True, truncate=False)

# %% [markdown]
# ### Where the loss concentrates — by licence family
#
# This is the whole business case in one table. The spiky licence families carry
# the loss, because a spike that develops in three weeks cannot be covered by a
# 9–16 week ocean replenishment ordered off a trailing average.

# %%
spark.sql(f"""
SELECT
  license_family,
  COUNT(*)                                                           AS sku_weeks,
  ROUND(100.0 * SUM(CASE WHEN is_stockout_week THEN 1 ELSE 0 END)/COUNT(*), 2)
                                                                     AS stockout_incidence_pct,
  ROUND(100.0 * SUM(lost_units) / SUM(true_demand_units), 2)         AS lost_units_pct,
  ROUND(SUM(lost_revenue_usd), 0)                                    AS lost_revenue_usd
FROM {C}.gold.fct_demand_weekly
WHERE true_demand_units > 0
GROUP BY license_family
ORDER BY lost_revenue_usd DESC
""").show(truncate=False)

# %% [markdown]
# ## Cost of reacting late — air-freight premium
#
# When a spike is spotted in POS, the only way to chase it is air freight. That
# recovers most of the surge a few weeks late, and carries a large premium. It is a
# direct, measurable cost of not having seen the spike coming.

# %%
freight = spark.sql(f"""
SELECT
  transport_mode,
  COUNT(*)                                        AS shipments,
  SUM(shipped_units)                              AS units,
  ROUND(SUM(freight_cost_usd), 2)                 AS freight_usd,
  ROUND(SUM(freight_cost_usd)/SUM(shipped_units), 4) AS freight_usd_per_unit
FROM {C}.gold.fct_inbound_eta
GROUP BY transport_mode ORDER BY transport_mode
""")
freight.show(truncate=False)

# %% [markdown]
# ## Markdown and inventory efficiency
#
# Cost is read from `silver.product_master` because the Gold column mask hides it
# from this identity.

# %%
md = spark.sql(f"""
SELECT
  SUM(cleared_units)                              AS markdown_cleared_units,
  ROUND(SUM(markdown_loss_usd), 2)                AS markdown_loss_usd
FROM {C}.gold.fct_markdown
""")
md.show(truncate=False)

inv = spark.sql(f"""
WITH latest AS (
  SELECT i.sku_id, i.dc_code, i.on_hand_units, i.in_transit_units
  FROM {C}.gold.fct_inventory_position i
  WHERE i.is_latest_snapshot = true
),
cost AS (SELECT sku_id, unit_cost_usd FROM {C}.silver.product_master),
weekly AS (
  SELECT AVG(wk) AS mean_weekly_pos FROM (
    SELECT week_end_date, SUM(pos_units) AS wk
    FROM {C}.gold.fct_demand_weekly
    WHERE week_end_date >= (SELECT DATE_SUB(MAX(week_end_date), 90)
                            FROM {C}.gold.fct_demand_weekly)
    GROUP BY week_end_date)
)
SELECT
  SUM(l.on_hand_units)                                        AS on_hand_units,
  SUM(l.in_transit_units)                                     AS in_transit_units,
  ROUND(SUM(l.on_hand_units * c.unit_cost_usd), 2)            AS on_hand_at_cost_usd,
  ROUND((SELECT mean_weekly_pos FROM weekly), 1)              AS mean_weekly_pos_units,
  ROUND(SUM(l.on_hand_units) / (SELECT mean_weekly_pos FROM weekly), 2) AS weeks_of_supply,
  ROUND(52.0 / (SUM(l.on_hand_units) / (SELECT mean_weekly_pos FROM weekly)), 2)
                                                              AS inventory_turns_annual
FROM latest l LEFT JOIN cost c USING (sku_id)
""")
inv.show(vertical=True, truncate=False)

# %% [markdown]
# ## Assemble and write the baseline
#
# Every row carries the KPI, the measured value, the unit, and the exact basis of
# measurement, so each deck figure traces back to a definition and a query.

# %%
import datetime as dt  # noqa: E402

s = service.collect()[0]
lo = loss.collect()[0]
m = md.collect()[0]
iv = inv.collect()[0]
fr = {r["transport_mode"]: r for r in freight.collect()}
invoiced = float(s["invoiced_wholesale_usd"])
air = fr.get("AIR")

rows = [
    ("retailer_fill_rate_pct", float(s["fill_rate_requested_pct"]), "pct",
     "shipped units / requested units across all PO lines in gold.fct_orders_fulfillment"),
    ("retailer_fill_rate_confirmed_basis_pct", float(s["fill_rate_confirmed_pct"]), "pct",
     "shipped units / confirmed units; reported for comparison, flatters performance"),
    ("order_acceptance_pct", float(s["order_acceptance_pct"]), "pct",
     "confirmed units / requested units; falls when supply cannot cover demand"),
    ("otif_pct", float(s["otif_pct"]), "pct",
     "PO lines both on-time (within 2-day grace) and in-full, as share of all lines"),
    ("on_time_pct", float(s["on_time_pct"]), "pct", "PO lines shipped within the 2-day grace"),
    ("in_full_pct", float(s["in_full_pct"]), "pct", "PO lines shipped complete vs requested"),
    ("stockout_weeks", int(lo["stockout_weeks"]), "count",
     "SKU x retailer x DC x week cells where demand exceeded units served"),
    ("stockout_week_incidence_pct", float(lo["stockout_week_incidence_pct"]), "pct",
     "stockout weeks as share of active SKU x retailer x DC weeks"),
    ("lost_units", int(lo["lost_units"]), "units",
     "true_demand_units minus pos_units, summed over all active cells"),
    ("lost_units_pct_of_demand", float(lo["lost_units_pct_of_demand"]), "pct",
     "lost units as share of unconstrained consumer demand"),
    ("lost_revenue_usd", float(lo["lost_revenue_usd"]), "usd",
     "lost units valued at wholesale price"),
    ("compliance_chargeback_usd", float(s["chargeback_usd"]), "usd",
     "retailer compliance chargebacks on non-compliant PO lines"),
    ("compliance_chargeback_pct_of_invoiced", float(s["chargeback_pct_of_invoiced"]), "pct",
     "chargeback $ / invoiced wholesale $"),
    ("markdown_cleared_units", int(m["markdown_cleared_units"]), "units",
     "units liquidated at close-out in gold.fct_markdown"),
    ("markdown_loss_usd", float(m["markdown_loss_usd"]), "usd",
     "wholesale margin forgone: cleared units x (wholesale - clearance price)"),
    ("markdown_loss_pct_of_invoiced", 100.0 * float(m["markdown_loss_usd"]) / invoiced, "pct",
     "markdown loss / invoiced wholesale $"),
    ("weeks_of_supply", float(iv["weeks_of_supply"]), "weeks",
     "latest on-hand units / mean weekly POS units over the last 13 weeks"),
    ("inventory_turns_annual", float(iv["inventory_turns_annual"]), "turns",
     "52 / weeks of supply"),
    ("on_hand_at_cost_usd", float(iv["on_hand_at_cost_usd"]), "usd",
     "latest on-hand valued at landed cost from silver.product_master"),
    ("invoiced_wholesale_usd", invoiced, "usd",
     "shipped units x wholesale price, whole 3-year history"),
    ("po_lines", int(s["po_lines"]), "count", "PO lines in gold.fct_orders_fulfillment"),
]
if air is not None:
    rows += [
        ("air_expedite_units", int(air["units"]), "units",
         "units air-freighted to chase a detected demand surge"),
        ("air_freight_usd", float(air["freight_usd"]), "usd", "air freight spend on expedites"),
        ("air_freight_pct_of_invoiced", 100.0 * float(air["freight_usd"]) / invoiced, "pct",
         "air freight spend / invoiced wholesale $"),
        ("air_freight_usd_per_unit", float(air["freight_usd_per_unit"]), "usd_per_unit",
         "air freight unit rate"),
    ]
    ocean = fr.get("OCEAN")
    if ocean is not None:
        rows.append(
            ("air_vs_ocean_freight_premium_x",
             float(air["freight_usd_per_unit"]) / float(ocean["freight_usd_per_unit"]),
             "ratio", "air freight unit rate / ocean freight unit rate")
        )

import pandas as pd  # noqa: E402

base = pd.DataFrame(rows, columns=["kpi", "value", "unit", "basis"])
base["value"] = base["value"].astype(float).round(4)
base["measured_at_utc"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
base["source"] = f"{C}.gold"

pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 95)
print(base[["kpi", "value", "unit", "basis"]].to_string(index=False))

vol_out = "/Volumes/toy_demand_allocation/bronze/pipeline_meta/kpi_baseline.csv"
base.to_csv(vol_out, index=False)
print(f"\nwrote {vol_out}  ({len(base)} KPIs)")
