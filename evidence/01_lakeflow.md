# Evidence 01 — Stage A: Lakeflow declarative pipeline (ingest + medallion)

| | |
|---|---|
| Produced by | `tools/evidence_01_lakeflow.py` reading Unity Catalog and the pipeline event log |
| Generated at | 2026-10-08 21:58:19 UTC |
| Workspace | `https://fevm-stable-classic-pqjg18.cloud.databricks.com` |
| Profile | `vm-summer-classic` |
| Pipeline | `toy-demand-allocation-medallion` (`7c2d619c-4f3e-4ca2-add3-7376bd722d93`) |
| Update | `95162037-84c7-4d9d-b01f-3eab305de31a` |
| UC paths touched | `toy_demand_allocation.bronze.*`, `toy_demand_allocation.silver.*`, `toy_demand_allocation.gold.*` |
| Landing volume | `/Volumes/toy_demand_allocation/bronze/landing` |
| Data | 100% synthetic, from `src/datagen/generate.py` (seed 20260408) |

---

## 1. Pipeline update: identity and final state

```text
pipeline_id    : 7c2d619c-4f3e-4ca2-add3-7376bd722d93
pipeline_name  : toy-demand-allocation-medallion
update_id      : 95162037-84c7-4d9d-b01f-3eab305de31a
state          : UpdateInfoState.COMPLETED
creation_time  : 1791496420793
full_refresh   : True
cause          : UpdateInfoCause.API_CALL
serverless     : True
target catalog : toy_demand_allocation
default schema : bronze
libraries      :
  - /Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/src/pipelines/bronze.py
  - /Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/src/pipelines/silver.py
  - /Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/src/pipelines/gold.py
configuration  :
  landing_path = /Volumes/toy_demand_allocation/bronze/landing
  schema_location_base = /Volumes/toy_demand_allocation/bronze/pipeline_meta/schemas
  target_catalog = toy_demand_allocation
```

## 2. Data-quality expectations: per-expectation pass / fail / drop counts

Read from the pipeline event log, not asserted. These counts are the evidence
that governance is actually running: the generator injects negative quantities,
duplicate grain keys, null on-hand readings and orphan SKU references on
purpose, and the `_or_drop` expectations below are what remove them before Gold.

Expectation semantics in this pipeline:

- **drop** (`expect_all_or_drop`) — invalid rows, removed. `failed_records` here
  equals rows dropped.
- **warn** (`expect_all`) — rows usable but incomplete; counted and *kept*.
  Dropping a POS row because its on-hand reading is missing would understate
  demand, so these are deliberately retained.

```sql
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
  FROM toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd722d93
  WHERE event_type = 'flow_progress'
    AND origin.update_id = '95162037-84c7-4d9d-b01f-3eab305de31a'
)
WHERE e.name IS NOT NULL
GROUP BY e.dataset, e.name
ORDER BY failed_records DESC, dataset, expectation
```

```text
dataset                                           expectation                    passed_records  failed_records  pct_failed
------------------------------------------------  -----------------------------  --------------  --------------  ----------
toy_demand_allocation.silver.pos_weekly           reported_within_8_days         472273          9619            1.9961    
toy_demand_allocation.silver.pos_weekly           on_hand_present                474696          7196            1.4933    
toy_demand_allocation.silver.pos_weekly           pos_units_non_negative         480900          992             0.2059    
toy_demand_allocation.silver.pos_weekly           sku_resolves_to_master         481171          721             0.1496    
toy_demand_allocation.silver.orders               confirmed_not_above_requested  255147          500             0.1956    
toy_demand_allocation.silver.orders               requested_units_positive       255147          500             0.1956    
toy_demand_allocation.silver.pos_weekly           units_normalised_to_each       481681          211             0.0438    
toy_demand_allocation.silver.inbound_shipments    asn_id_present                 53344           0               0.0000    
toy_demand_allocation.silver.inbound_shipments    eta_not_before_order           53344           0               0.0000    
toy_demand_allocation.silver.inbound_shipments    ordered_units_positive         53344           0               0.0000    
toy_demand_allocation.silver.inbound_shipments    shipped_units_non_negative     53344           0               0.0000    
toy_demand_allocation.silver.inbound_shipments    transport_mode_known           53344           0               0.0000    
toy_demand_allocation.silver.inventory_snapshots  in_transit_non_negative        1212480         0               0.0000    
toy_demand_allocation.silver.inventory_snapshots  on_hand_non_negative           1212480         0               0.0000    
toy_demand_allocation.silver.inventory_snapshots  snapshot_date_present          1212480         0               0.0000    
toy_demand_allocation.silver.markdown_clearance   clearance_below_wholesale      11322           0               0.0000    
toy_demand_allocation.silver.markdown_clearance   clearance_price_non_negative   11322           0               0.0000    
toy_demand_allocation.silver.markdown_clearance   cleared_units_positive         11322           0               0.0000    
toy_demand_allocation.silver.orders               confirmed_not_negative         255647          0               0.0000    
toy_demand_allocation.silver.orders               po_line_id_present             255647          0               0.0000    
toy_demand_allocation.silver.orders               shipped_not_above_confirmed    255647          0               0.0000    
toy_demand_allocation.silver.orders               shipped_not_negative           255647          0               0.0000    
toy_demand_allocation.silver.pos_weekly           grain_key_complete             481892          0               0.0000    
toy_demand_allocation.silver.pos_weekly           uom_recognised                 481892          0               0.0000    
toy_demand_allocation.silver.product_master       case_pack_positive             2400            0               0.0000    
toy_demand_allocation.silver.product_master       cost_non_negative              2400            0               0.0000    
toy_demand_allocation.silver.product_master       margin_in_range                2400            0               0.0000    
toy_demand_allocation.silver.product_master       msrp_above_cost                2400            0               0.0000    
toy_demand_allocation.silver.product_master       sku_id_present                 2400            0               0.0000    
(29 rows)
```

Totals across all expectations in this update:

```text
total_passed  total_failed_or_dropped  distinct_expectations  datasets_with_expectations
------------  -----------------------  ---------------------  --------------------------
8837513       19739                    29                     6                         
(1 row)
```

## 3. Every flow in the update, with its final state

```text
flow_name                                          status     last_event              
-------------------------------------------------  ---------  ------------------------
toy_demand_allocation.bronze.inbound_shipments     COMPLETED  2026-10-08T21:54:58.323Z
toy_demand_allocation.bronze.inventory_snapshots   COMPLETED  2026-10-08T21:55:00.193Z
toy_demand_allocation.bronze.markdown_clearance    COMPLETED  2026-10-08T21:54:57.480Z
toy_demand_allocation.bronze.orders                COMPLETED  2026-10-08T21:54:59.158Z
toy_demand_allocation.bronze.pos_weekly            COMPLETED  2026-10-08T21:54:59.629Z
toy_demand_allocation.bronze.product_master        COMPLETED  2026-10-08T21:54:57.258Z
toy_demand_allocation.gold.agg_fill_rate_summary   COMPLETED  2026-10-08T21:55:18.387Z
toy_demand_allocation.gold.dim_product             COMPLETED  2026-10-08T21:55:08.995Z
toy_demand_allocation.gold.dim_retailer_dc         COMPLETED  2026-10-08T21:55:15.282Z
toy_demand_allocation.gold.fct_demand_weekly       COMPLETED  2026-10-08T21:55:15.952Z
toy_demand_allocation.gold.fct_inbound_eta         COMPLETED  2026-10-08T21:55:13.426Z
toy_demand_allocation.gold.fct_inventory_position  COMPLETED  2026-10-08T21:55:14.367Z
toy_demand_allocation.gold.fct_markdown            COMPLETED  2026-10-08T21:55:13.392Z
toy_demand_allocation.gold.fct_orders_fulfillment  COMPLETED  2026-10-08T21:55:15.243Z
toy_demand_allocation.silver.inbound_shipments     COMPLETED  2026-10-08T21:55:07.961Z
toy_demand_allocation.silver.inventory_snapshots   COMPLETED  2026-10-08T21:55:08.959Z
toy_demand_allocation.silver.markdown_clearance    COMPLETED  2026-10-08T21:55:05.444Z
toy_demand_allocation.silver.orders                COMPLETED  2026-10-08T21:55:08.917Z
toy_demand_allocation.silver.pos_weekly            COMPLETED  2026-10-08T21:55:10.211Z
toy_demand_allocation.silver.product_master        COMPLETED  2026-10-08T21:55:05.516Z
(20 rows)
```

## 4. Row counts per medallion layer

Counted directly against Unity Catalog after the update completed.

```text
layer   table_name              row_count
------  ----------------------  ---------
bronze  inbound_shipments       53344    
bronze  inventory_snapshots     1212480  
bronze  markdown_clearance      11322    
bronze  orders                  255647   
bronze  pos_weekly              484779   
bronze  product_master          2400     
gold    agg_fill_rate_summary   231      
gold    dim_product             2400     
gold    dim_retailer_dc         10       
gold    fct_demand_weekly       480183   
gold    fct_inbound_eta         53344    
gold    fct_inventory_position  1212480  
gold    fct_markdown            11322    
gold    fct_orders_fulfillment  255147   
silver  inbound_shipments       53344    
silver  inventory_snapshots     1212480  
silver  markdown_clearance      11322    
silver  orders                  255147   
silver  pos_weekly              480183   
silver  product_master          2400     
(20 rows)
```

### Bronze to Silver attrition (what the expectations removed)

```text
table_name           bronze_rows  silver_rows  rows_removed  pct_removed
-------------------  -----------  -----------  ------------  -----------
pos_weekly           484779       480183       4596          0.9481     
orders               255647       255147       500           0.1956     
product_master       2400         2400         0             0.0000     
inbound_shipments    53344        53344        0             0.0000     
markdown_clearance   11322        11322        0             0.0000     
inventory_snapshots  1212480      1212480      0             0.0000     
(6 rows)
```

## 5. The unit normalisation, proven

`ETAIL_C` reports POS and on-hand in CASES; every other retailer reports eaches.
Left unconverted, a third of the portfolio looks permanently stocked out. Silver
multiplies those rows by the item's case pack and records that it did so.

```sql
SELECT
  retailer_code,
  reported_uom,
  unit_conversion_applied,
  COUNT(*)                        AS rows,
  MIN(conversion_factor)          AS min_factor,
  MAX(conversion_factor)          AS max_factor,
  SUM(pos_units)                  AS pos_units_each
FROM toy_demand_allocation.silver.pos_weekly
GROUP BY retailer_code, reported_uom, unit_conversion_applied
ORDER BY retailer_code
```

```text
retailer_code  reported_uom  unit_conversion_applied  rows    min_factor  max_factor  pos_units_each
-------------  ------------  -----------------------  ------  ----------  ----------  --------------
ETAIL_C        CASE          true                     151474  4           24          3439980       
MASS_A         EACH          false                    165573  1           1           3031945       
MASS_B         EACH          false                    163136  1           1           3366123       
(3 rows)
```

## 6. `gold.fct_demand_weekly` — 10-row sample

The weekly demand fact that Stage C1 trains on. `true_demand_units` is
unconstrained consumer demand, `pos_units` is what was actually served, so the
difference is lost sales.

```sql
SELECT sku_id, retailer_code, dc_code, week_end_date, pos_units, true_demand_units,
       on_hand_units, lost_units, lost_revenue_usd, is_stockout_week, promo_flag,
       brand, license_family, lifecycle_stage
FROM toy_demand_allocation.gold.fct_demand_weekly
WHERE is_stockout_week = true
ORDER BY lost_revenue_usd DESC
LIMIT 10
```

```text
sku_id     retailer_code  dc_code   week_end_date  pos_units  true_demand_units  on_hand_units  lost_units  lost_revenue_usd  is_stockout_week  promo_flag  brand           license_family  lifecycle_stage
---------  -------------  --------  -------------  ---------  -----------------  -------------  ----------  ----------------  ----------------  ----------  --------------  --------------  ---------------
SKU-02186  MASS_A         DC_EAST   2026-05-02     0          2226               0              2226        22215.48          true              0           Snugglewick     SOCIAL_TREND    EOL            
SKU-01089  ETAIL_C        DC_WEST   2026-08-08     6          2710               0              2704        15277.6           true              0           Glowberry Pets  SOCIAL_TREND    MATURE         
SKU-00955  MASS_A         DC_WEST   2026-07-04     0          801                0              801         15170.94          true              0           Titan Squad     SOCIAL_TREND    DECLINE        
SKU-00027  ETAIL_C        DC_EAST   2026-03-28     30         973                96             943         14663.65          true              1           Titan Squad     SOCIAL_TREND    EOL            
SKU-01815  ETAIL_C        DC_EAST   2026-09-05     8          1110               0              1102        14270.9           true              0           Titan Squad     SOCIAL_TREND    MATURE         
SKU-01815  ETAIL_C        DC_WEST   2026-09-05     8          933                0              925         11978.75          true              0           Titan Squad     SOCIAL_TREND    MATURE         
SKU-01438  ETAIL_C        DC_WEST   2026-02-28     168        1047               276            879         11848.92          true              0           Titan Squad     SOCIAL_TREND    DECLINE        
SKU-01180  MASS_B         DC_SOUTH  2026-04-25     832        2456               1639           1624        11741.52          true              1           Snugglewick     FILM_TIE_IN     MATURE         
SKU-00454  MASS_A         DC_EAST   2026-06-20     0          875                0              875         11690.0           true              1           Titan Squad     FILM_TIE_IN     MATURE         
SKU-02034  ETAIL_C        DC_EAST   2026-07-25     1736       4328               2180           2592        10782.72          true              0           Glowberry Pets  SOCIAL_TREND    MATURE         
(10 rows)
```

### Provenance columns carried through to Gold

```text
sku_id     week_end_date  _ingested_at              source_file           
---------  -------------  ------------------------  ----------------------
SKU-00001  2023-10-14     2026-10-08T21:54:49.577Z  pos_weekly_part006.csv
SKU-00001  2024-05-25     2026-10-08T21:54:49.577Z  pos_weekly_part007.csv
SKU-00001  2024-08-10     2026-10-08T21:54:49.577Z  pos_weekly_part002.csv
SKU-00001  2025-08-30     2026-10-08T21:54:49.577Z  pos_weekly_part002.csv
SKU-00001  2024-01-20     2026-10-08T21:54:49.577Z  pos_weekly_part002.csv
(5 rows)
```

### Grain uniqueness check on the weekly fact

```text
total_rows  distinct_grain_keys  duplicates
----------  -------------------  ----------
480183      480183               0         
(1 row)
```

## 7. Landing-zone files the pipeline ingested

```text
source_dir           files_ingested  rows_ingested
-------------------  --------------  -------------
inbound_shipments    6               53344        
inventory_snapshots  10              1212480      
markdown_clearance   4               11322        
orders               8               255647       
pos_weekly           12              484779       
product_master       1               2400         
(6 rows)
```

## 8. Production ingest path

In production this stage reads from Microsoft SQL Server through **Lakeflow
Connect**, landing CDC output into Bronze with the same expectations and the
same Gold contract downstream. This build ingests committed synthetic flat files
from a Unity Catalog volume via Auto Loader instead. **No SQL Server connector
was run here** — the substitution is deliberate and is stated in the README.
