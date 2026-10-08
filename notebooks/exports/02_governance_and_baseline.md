# 02 governance and baseline

> Executed by `tools/dbx_notebook.py` on Databricks cluster `1008-205814-qhbfm7tc` (02_governance_and_baseline), profile `vm-summer-classic`.
> Started 2026-10-08 22:21:45 UTC, finished 2026-10-08 22:22:41 UTC. Outputs below are captured verbatim from the cluster.

# Stage B (part 2) — Measure the KPI baseline

Governance DDL is applied by `src/governance/apply.py` against the SQL warehouse
(metric views need a newer runtime than DBR 16.4 LTS ML). This notebook does the
other half of Stage B: it **measures** the baseline from the Gold layer so every
number in the deck is computed from the generated data rather than asserted.

Output: `evidence/metrics/kpi_baseline.csv`.

One wrinkle worth stating plainly: the column mask applied in Stage B makes
`gold.dim_product.unit_cost_usd` read NULL for every identity except the finance
persona — including the catalog owner running this notebook. That is the mask
working correctly. Cost-based measures below therefore read cost from
`silver.product_master`, which is the data-engineering layer this notebook is
entitled to and which carries no mask. Revenue-based measures are unaffected.

```python
import sys

WS = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
if WS + "/src" not in sys.path:
    sys.path.insert(0, WS + "/src")

import config  # noqa: E402

C = config.CATALOG
print("catalog:", C)
print("current_user:", spark.sql("SELECT current_user()").collect()[0][0])  # noqa: F821

# demonstrate the mask is active for this identity
spark.sql(  # noqa: F821
    f"SELECT sku_id, unit_cost_usd, gross_margin_pct FROM {C}.gold.dim_product LIMIT 3"
).show()
print("^ cost and margin are NULL here: the Stage B column mask is in force for")
print("  this identity. Cost-based measures below read silver.product_master.")
```

_Output:_

```text
catalog: toy_demand_allocation
current_user: sixuan.he@databricks.com
+---------+-------------+----------------+
|   sku_id|unit_cost_usd|gross_margin_pct|
+---------+-------------+----------------+
|SKU-00001|         NULL|            NULL|
|SKU-00002|         NULL|            NULL|
|SKU-00003|         NULL|            NULL|
+---------+-------------+----------------+

^ cost and margin are NULL here: the Stage B column mask is in force for
  this identity. Cost-based measures below read silver.product_master.
```

## Service KPIs — fill rate, OTIF, on-time, in-full

Fill rate is **shipped / requested**. That is the service level the retailer
actually experiences and the basis on which compliance chargebacks are raised.
The confirmed-basis number is reported alongside it because supply-chain teams
often quote that instead, and the two diverge sharply when supply is short —
99% on a confirmed basis and 96% on a requested basis describe the same weeks.

```python
service = spark.sql(f"""                                                   -- noqa: F821
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
```

_Output:_

```text
-RECORD 0------------------------------------------
 requested_units            | 11469434             
 confirmed_units            | 11134178             
 shipped_units              | 11041016             
 unfilled_units             | 428418               
 po_lines                   | 255147               
 fill_rate_requested_pct    | 96.26469797899356    
 fill_rate_confirmed_pct    | 99.16327904942781    
 order_acceptance_pct       | 97.07696125196762    
 on_time_pct                | 92.66462078723246    
 in_full_pct                | 92.96523180754624    
 otif_pct                   | 86.14406596981348    
 invoiced_wholesale_usd     | 1.0153915549999347E8 
 chargeback_usd             | 1260717.8200000026   
 chargeback_pct_of_invoiced | 1.2416075491193974
```

### Fill rate and OTIF by retailer

The three accounts carry different contractual commitments, so the gap between
commitment and actual is what a planner is actually judged on.

```python
spark.sql(f"""                                                             -- noqa: F821
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
```

_Output:_

```text
+-------------+--------------+-------------+---------------------+--------+--------------+
|retailer_code|commitment_pct|fill_rate_pct|gap_to_commitment_pts|otif_pct|chargeback_usd|
+-------------+--------------+-------------+---------------------+--------+--------------+
|ETAIL_C      |93.0          |96.46        |3.46                 |86.17   |427083.0      |
|MASS_A       |97.0          |96.34        |-0.66                |86.14   |388869.0      |
|MASS_B       |95.0          |96.01        |1.01                 |86.12   |444766.0      |
+-------------+--------------+-------------+---------------------+--------+--------------+
```

## Demand loss — stockout weeks and lost revenue

`true_demand_units` is unconstrained consumer demand and `pos_units` is what was
actually served, so the difference is lost sales. This is the number the
forecast is ultimately trying to reduce.

```python
loss = spark.sql(f"""                                                      -- noqa: F821
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
```

_Output:_

```text
-RECORD 0-----------------------------------------
 active_sku_retailer_dc_weeks | 480183            
 stockout_weeks               | 74789             
 stockout_week_incidence_pct  | 15.57510365839690 
 true_demand_units            | 10009195          
 pos_units                    | 9838048           
 lost_units                   | 585575            
 lost_units_pct_of_demand     | 5.85037058424778  
 lost_revenue_usd             | 5505413.36
```

### Where the loss concentrates — by licence family

This is the whole business case in one table. The spiky licence families carry
the loss, because a spike that develops in three weeks cannot be covered by a
9–16 week ocean replenishment ordered off a trailing average.

```python
spark.sql(f"""                                                             -- noqa: F821
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
```

_Output:_

```text
+--------------+---------+----------------------+--------------+----------------+
|license_family|sku_weeks|stockout_incidence_pct|lost_units_pct|lost_revenue_usd|
+--------------+---------+----------------------+--------------+----------------+
|EVERGREEN     |372052   |14.88                 |4.22          |2416864.0       |
|SOCIAL_TREND  |33742    |21.12                 |13.84         |1956423.0       |
|FILM_TIE_IN   |74389    |16.53                 |4.74          |1132127.0       |
+--------------+---------+----------------------+--------------+----------------+
```

## Cost of reacting late — air-freight premium

When a spike is spotted in POS, the only way to chase it is air freight. That
recovers most of the surge a few weeks late, and carries a large premium. It is a
direct, measurable cost of not having seen the spike coming.

```python
freight = spark.sql(f"""                                                   -- noqa: F821
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
```

_Output:_

```text
+--------------+---------+--------+-----------+--------------------+
|transport_mode|shipments|units   |freight_usd|freight_usd_per_unit|
+--------------+---------+--------+-----------+--------------------+
|AIR           |3613     |1085487 |2116699.65 |1.95                |
|OCEAN         |49731    |10301894|2884530.32 |0.28                |
+--------------+---------+--------+-----------+--------------------+
```

## Markdown and inventory efficiency

Cost is read from `silver.product_master` because the Gold column mask hides it
from this identity.

```python
md = spark.sql(f"""                                                        -- noqa: F821
SELECT
  SUM(cleared_units)                              AS markdown_cleared_units,
  ROUND(SUM(markdown_loss_usd), 2)                AS markdown_loss_usd
FROM {C}.gold.fct_markdown
""")
md.show(truncate=False)

inv = spark.sql(f"""                                                       -- noqa: F821
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
```

_Output:_

```text
+----------------------+-----------------+
|markdown_cleared_units|markdown_loss_usd|
+----------------------+-----------------+
|2063198               |7940809.19       |
+----------------------+-----------------+

-RECORD 0-------------------------------
 on_hand_units          | 2670349       
 in_transit_units       | 733596        
 on_hand_at_cost_usd    | 1.604601124E7 
 mean_weekly_pos_units  | 156928.1      
 weeks_of_supply        | 17.02         
 inventory_turns_annual | 3.06
```

## Assemble and write the baseline

Every row carries the KPI, the measured value, the unit, and the exact basis of
measurement, so each deck figure traces back to a definition and a query.

```python
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
```

_Output:_

```text
kpi        value         unit                                                                              basis
                retailer_fill_rate_pct 9.626470e+01          pct shipped units / requested units across all PO lines in gold.fct_orders_fulfillment
retailer_fill_rate_confirmed_basis_pct 9.916330e+01          pct     shipped units / confirmed units; reported for comparison, flatters performance
                  order_acceptance_pct 9.707700e+01          pct           confirmed units / requested units; falls when supply cannot cover demand
                              otif_pct 8.614410e+01          pct      PO lines both on-time (within 2-day grace) and in-full, as share of all lines
                           on_time_pct 9.266460e+01          pct                                            PO lines shipped within the 2-day grace
                           in_full_pct 9.296520e+01          pct                                             PO lines shipped complete vs requested
                        stockout_weeks 7.478900e+04        count                SKU x retailer x DC x week cells where demand exceeded units served
           stockout_week_incidence_pct 1.557510e+01          pct                        stockout weeks as share of active SKU x retailer x DC weeks
                            lost_units 5.855750e+05        units                    true_demand_units minus pos_units, summed over all active cells
              lost_units_pct_of_demand 5.850400e+00          pct                               lost units as share of unconstrained consumer demand
                      lost_revenue_usd 5.505413e+06          usd                                               lost units valued at wholesale price
             compliance_chargeback_usd 1.260718e+06          usd                          retailer compliance chargebacks on non-compliant PO lines
 compliance_chargeback_pct_of_invoiced 1.241600e+00          pct                                                chargeback $ / invoiced wholesale $
                markdown_cleared_units 2.063198e+06        units                                 units liquidated at close-out in gold.fct_markdown
                     markdown_loss_usd 7.940809e+06          usd            wholesale margin forgone: cleared units x (wholesale - clearance price)
         markdown_loss_pct_of_invoiced 7.820400e+00          pct                                               markdown loss / invoiced wholesale $
                       weeks_of_supply 1.702000e+01        weeks                latest on-hand units / mean weekly POS units over the last 13 weeks
                inventory_turns_annual 3.060000e+00        turns                                                               52 / weeks of supply
                   on_hand_at_cost_usd 1.604601e+07          usd                    latest on-hand valued at landed cost from silver.product_master
                invoiced_wholesale_usd 1.015392e+08          usd                              shipped units x wholesale price, whole 3-year history
                              po_lines 2.551470e+05        count                                            PO lines in gold.fct_orders_fulfillment
                    air_expedite_units 1.085487e+06        units                               units air-freighted to chase a detected demand surge
                       air_freight_usd 2.116700e+06          usd                                                     air freight spend on expedites
           air_freight_pct_of_invoiced 2.084600e+00          pct                                           air freight spend / invoiced wholesale $
              air_freight_usd_per_unit 1.950000e+00 usd_per_unit                                                              air freight unit rate
        air_vs_ocean_freight_premium_x 6.964300e+00        ratio                                    air freight unit rate / ocean freight unit rate

wrote /Volumes/toy_demand_allocation/bronze/pipeline_meta/kpi_baseline.csv  (26 KPIs)
```
