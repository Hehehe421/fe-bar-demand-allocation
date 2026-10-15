# Evidence 02b — Lineage, landing file to Lakebase

| | |
|---|---|
| Produced by | `tools/evidence_lineage.py` |
| Workspace | https://fevm-stable-classic-pqjg18.cloud.databricks.com |
| Generated | 2026-10-15 23:10 UTC |
| Unity Catalog | `toy_demand_allocation` |

Unity Catalog records table-to-table lineage. The **model** and **Lakebase** hops are not table
lineage, so each is shown from the object that carries it, and every number is queried live.

```text
landing volume files  ->  bronze (streaming tables)  ->  silver  ->  gold facts and dims
   -> ml.demand_features_weekly (Feature Engineering)  ->  model ml.demand_forecaster @champion
   -> gold.fct_demand_forecast  ->  gold.fct_stockout_risk
   -> gold.svc_* projections  ->  Lakebase synced tables  ->  app  ->  allocation_decisions (Lakebase)
   -> write-back  ->  gold.fct_allocation_decisions  ->  Genie
```

## 1. Landing file to bronze

Auto Loader reads files from the landing volume. Each bronze row carries the file it came from,
so a row can be traced to its file without any lineage tooling.

```text
bronze_table   landing_file                                                             rows 
-------------  -----------------------------------------------------------------------  -----
bronze.orders  /Volumes/toy_demand_allocation/bronze/landing/orders/orders_part000.csv  31956
bronze.orders  /Volumes/toy_demand_allocation/bronze/landing/orders/orders_part001.csv  31956
bronze.orders  /Volumes/toy_demand_allocation/bronze/landing/orders/orders_part002.csv  31956
(3 rows)
```

## 2. Bronze → silver → gold → features → forecast → risk (table lineage)

Distinct edges from `system.access.table_lineage` (the rows with no source table are the
file ingests in section 1).

```text
source                         target                     
-----------------------------  ---------------------------
gold.dim_retailer_dc           gold.agg_fill_rate_summary 
gold.fct_orders_fulfillment    gold.agg_fill_rate_summary 
silver.product_master          gold.dim_product           
silver.pos_weekly              gold.dim_retailer_dc       
ml.demand_features_weekly      gold.fct_demand_forecast   
gold.dim_product               gold.fct_demand_weekly     
silver.pos_weekly              gold.fct_demand_weekly     
silver.inbound_shipments       gold.fct_inbound_eta       
silver.inventory_snapshots     gold.fct_inventory_position
gold.dim_product               gold.fct_markdown          
silver.markdown_clearance      gold.fct_markdown          
gold.dim_product               gold.fct_orders_fulfillment
silver.orders                  gold.fct_orders_fulfillment
gold.dim_retailer_dc           gold.fct_stockout_risk     
gold.fct_demand_forecast       gold.fct_stockout_risk     
gold.fct_inbound_eta           gold.fct_stockout_risk     
gold.fct_inventory_position    gold.fct_stockout_risk     
silver.product_master          gold.fct_stockout_risk     
gold.fct_orders_fulfillment    gold.mv_fill_rate          
gold.fct_demand_forecast_eval  gold.mv_forecast_accuracy  
gold.fct_orders_fulfillment    gold.mv_otif               
gold.dim_product               gold.mv_weeks_of_supply    
gold.fct_inventory_position    gold.mv_weeks_of_supply    
gold.agg_fill_rate_summary     gold.svc_fill_rate_summary 
gold.dim_product               gold.svc_product_lookup    
gold.fct_stockout_risk         gold.v_stockout_risk       
gold.dim_retailer_dc           ml.demand_features_weekly  
gold.fct_demand_weekly         ml.demand_features_weekly  
silver.product_master          ml.demand_features_weekly  
bronze.inbound_shipments       silver.inbound_shipments   
bronze.inventory_snapshots     silver.inventory_snapshots 
bronze.markdown_clearance      silver.markdown_clearance  
bronze.orders                  silver.orders              
bronze.pos_weekly              silver.pos_weekly          
bronze.product_master          silver.pos_weekly          
bronze.product_master          silver.product_master      
(36 rows)
```

Read it top to bottom: `bronze.*` → `silver.*` → `gold.*` → `ml.demand_features_weekly` →
`gold.fct_demand_forecast` → `gold.fct_stockout_risk`; the metric views read the facts; the two
`svc_*` projections read `dim_product` and `agg_fill_rate_summary`.

## 3. Feature table → model

Unity Catalog model registry, `toy_demand_allocation.ml.demand_forecaster@champion` = version **13**:

```text
run_id                : 66faa49606d6458a92d9218ad6012337
model_version_dependencies (tables the model reads at inference):
  - toy_demand_allocation.ml.demand_features_weekly
```

The model was logged with `fe.log_model` and a `FeatureLookup`, so the dependency is recorded by
Feature Engineering rather than asserted here. The same edge is in the system table (section 2):
`ml.demand_features_weekly` → `gold.fct_demand_forecast`.

What this is not: the lookup is on the exact key (`sku_id`, `retailer_code`, `origin_week`), not
an as-of join on a timeseries column. See `evidence/03_ml_forecast.md` §1 for why, and what the
leakage guard is instead.

## 4. Model → forecast rows

Every forecast row names the model that produced it:

```text
model_name                                  model_version  model_alias  algorithm  rows   first_week  last_week 
------------------------------------------  -------------  -----------  ---------  -----  ----------  ----------
toy_demand_allocation.ml.demand_forecaster  13             champion     lightgbm   58474  2026-10-03  2026-12-26
(1 row)
```

## 5. Unity Catalog → Lakebase

Four synced tables, each a managed pipeline from a Unity Catalog source to a Postgres table:

```text
postgres table           source (Unity Catalog)                                     primary key                    state
public.stockout_risk_current toy_demand_allocation.gold.fct_stockout_risk               sku_id,retailer_code           ONLINE_NO_PENDING_UPDATE
                         pipeline d93d18bd-9266-47c7-bde8-cbfb41c9eb13  last sync end 2026-10-15T19:10:03.755691Z  source commit v7
public.forecast_next_13w toy_demand_allocation.gold.fct_demand_forecast             sku_id,retailer_code,week_end_date ONLINE_NO_PENDING_UPDATE
                         pipeline 9fd96189-b77e-48f8-bce5-40867c4caf42  last sync end 2026-10-15T19:10:10.122637Z  source commit v7
public.product_lookup    toy_demand_allocation.gold.svc_product_lookup              sku_id                         ONLINE_NO_PENDING_UPDATE
                         pipeline 1b61f37f-d86f-40cb-ae1b-e8742b65df06  last sync end 2026-10-15T22:42:16.945512Z  source commit v0
public.fill_rate_summary toy_demand_allocation.gold.svc_fill_rate_summary           retailer_code,week_end_date    ONLINE_NO_PENDING_UPDATE
                         pipeline d03a382c-41fa-462d-9fdd-ec508e871201  last sync end 2026-10-15T22:42:25.893529Z  source commit v0
```

Row-count parity, Unity Catalog source against the Postgres copy:

```text
table                       unity catalog   lakebase  match
stockout_risk_current               3,016      3,016  yes
forecast_next_13w                  58,474     58,474  yes
product_lookup                      2,400      2,400  yes
fill_rate_summary                     231        231  yes
```

`product_lookup` and `fill_rate_summary` read `gold.svc_*` projections, not the dimension and
aggregate themselves. Unity Catalog refuses an online copy of a table that has a row filter or
column mask, and `dim_product` carries a mask on cost and margin. The projection drops those
columns (asserted by `src/lakebase/serving_tables.py`), so no governed value reaches Postgres.
Lineage records the projection: `dim_product` → `svc_product_lookup`,
`agg_fill_rate_summary` → `svc_fill_rate_summary`:

```text
source                                            target                                          
------------------------------------------------  ------------------------------------------------
toy_demand_allocation.gold.agg_fill_rate_summary  toy_demand_allocation.gold.svc_fill_rate_summary
toy_demand_allocation.gold.dim_product            toy_demand_allocation.gold.svc_product_lookup   
(2 rows)
```

## 6. The write path back: Lakebase → Unity Catalog

Decisions the app records go to Postgres (`allocation.allocation_decisions`); a write-back job
copies them to `gold.fct_allocation_decisions`, which Genie reads. Table lineage cannot see a
JDBC write, so this hop is shown by counts:

```text
lakebase allocation.allocation_decisions : 26 rows, latest 2026-10-15 22:21:28.171756+00:00
unity catalog gold.fct_allocation_decisions: 25 rows, latest 2026-10-09T23:29:28.626Z
```

**The counts differ by 1.** That is expected, not a defect: a
decision recorded in the app reaches Unity Catalog only when the write-back job next runs, and
one was recorded by hand while testing the app (decision 33, rationale "test") after the last run.

## 7. Weeks of supply: a correction, and its lineage

`mv_weeks_of_supply` originally had no weeks-of-supply measure: it exposed on-hand, in-transit
and total-available units, so a question about weeks of supply had nothing governed to answer
it. It now also carries `Weeks Of Supply` = latest on-hand divided by mean weekly POS units over
the last 13 weeks (`gold.agg_trailing_demand`, built by `src/governance/apply.py`):

```text
weeks_of_supply  on_hand_units  mean_weekly_pos_units
---------------  -------------  ---------------------
17.02            2670349        156928               
(1 row)
```

```text
source                                             target                                       
-------------------------------------------------  ---------------------------------------------
toy_demand_allocation.gold.dim_product             toy_demand_allocation.gold.mv_weeks_of_supply
toy_demand_allocation.gold.fct_inventory_position  toy_demand_allocation.gold.mv_weeks_of_supply
(2 rows)
```

System-table lineage lags by minutes to hours, so a source added today (`agg_trailing_demand`)
may not be listed yet; the metric view definition in `src/governance/apply.py` is the source of
truth and the value above is computed from it.
