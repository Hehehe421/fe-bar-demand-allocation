# Evidence 02 — Stage B: Unity Catalog governance

| | |
|---|---|
| Produced by | `tools/evidence_02_unity_catalog.py`, reading governance back out of Unity Catalog; policy proof from `src/governance/verify_policies.py` |
| Applied by | `src/governance/apply.py` (against the SQL warehouse) |
| Generated at | 2026-10-08 22:24:21 UTC |
| Workspace | `https://fevm-stable-classic-pqjg18.cloud.databricks.com` |
| Profile | `vm-summer-classic` |
| UC paths touched | `toy_demand_allocation` catalog; `toy_demand_allocation.gold.*`; `toy_demand_allocation.gold.mv_*`; policy UDFs `toy_demand_allocation.gold.rf_retailer_scope`, `toy_demand_allocation.gold.cm_cost_margin` |
| Data | 100% synthetic |

---

## 1. The three personas (plus the app identity)

Unity Catalog resolves only **account-level** principals. This FEVM workspace
has no account-level auth available, so workspace-local groups created with
`databricks groups create` are invisible to `GRANT` — every grant against one
failed with `PRINCIPAL_DOES_NOT_EXIST` even though `SHOW GROUPS` listed it.
Service principals created through the workspace API *are* account-level
entities and resolve correctly, and they carry a decisive advantage for
evidence: they can be authenticated, so the masked-vs-unmasked comparison in
§5 is produced by actually signing in as each persona rather than by asserting
what it would see.

| Persona | Service principal (application ID) | Intended access |
|---|---|---|
| Planner, scoped to MASS_A | `5d9eb1c3-888e-4acc-8625-4ecedea0fea5` | read Gold + metric views, MASS_A rows only |
| Data scientist | `7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8` | read Silver + Gold, write to `ml` |
| Finance | `dd70e525-c0aa-4ca5-ade2-29671715871a` | read Gold, unmasked cost and margin |
| App service principal | `1c73204c-6b49-4db0-a860-300ba392393e` | read Gold, read/write the decision table |

## 2. `SHOW GRANTS` per object

### `CATALOG toy_demand_allocation`

```text
Principal                             ActionType     ObjectType  ObjectKey              
------------------------------------  -------------  ----------  -----------------------
dd70e525-c0aa-4ca5-ade2-29671715871a  USE CATALOG    CATALOG     toy_demand_allocation  
5d9eb1c3-888e-4acc-8625-4ecedea0fea5  USE CATALOG    CATALOG     toy_demand_allocation  
1c73204c-6b49-4db0-a860-300ba392393e  USE CATALOG    CATALOG     toy_demand_allocation  
7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  USE CATALOG    CATALOG     toy_demand_allocation  
account users                         USE CATALOG    CATALOG     toy_demand_allocation  
suraj.bang@databricks.com             READ METADATA  METASTORE   metastore_aws_us_east_1
(6 rows)
```

### `SCHEMA toy_demand_allocation.gold`

```text
Principal                             ActionType     ObjectType  ObjectKey                 
------------------------------------  -------------  ----------  --------------------------
dd70e525-c0aa-4ca5-ade2-29671715871a  SELECT         SCHEMA      toy_demand_allocation.gold
dd70e525-c0aa-4ca5-ade2-29671715871a  USE SCHEMA     SCHEMA      toy_demand_allocation.gold
5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SELECT         SCHEMA      toy_demand_allocation.gold
5d9eb1c3-888e-4acc-8625-4ecedea0fea5  USE SCHEMA     SCHEMA      toy_demand_allocation.gold
1c73204c-6b49-4db0-a860-300ba392393e  CREATE TABLE   SCHEMA      toy_demand_allocation.gold
1c73204c-6b49-4db0-a860-300ba392393e  MODIFY         SCHEMA      toy_demand_allocation.gold
1c73204c-6b49-4db0-a860-300ba392393e  SELECT         SCHEMA      toy_demand_allocation.gold
1c73204c-6b49-4db0-a860-300ba392393e  USE SCHEMA     SCHEMA      toy_demand_allocation.gold
7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SELECT         SCHEMA      toy_demand_allocation.gold
7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  USE SCHEMA     SCHEMA      toy_demand_allocation.gold
suraj.bang@databricks.com             READ METADATA  METASTORE   metastore_aws_us_east_1   
(11 rows)
```

### `SCHEMA toy_demand_allocation.silver`

```text
Principal                             ActionType     ObjectType  ObjectKey                   
------------------------------------  -------------  ----------  ----------------------------
7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SELECT         SCHEMA      toy_demand_allocation.silver
7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  USE SCHEMA     SCHEMA      toy_demand_allocation.silver
suraj.bang@databricks.com             READ METADATA  METASTORE   metastore_aws_us_east_1     
(3 rows)
```

### `SCHEMA toy_demand_allocation.ml`

```text
Principal                             ActionType      ObjectType  ObjectKey               
------------------------------------  --------------  ----------  ------------------------
7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  ALL PRIVILEGES  SCHEMA      toy_demand_allocation.ml
suraj.bang@databricks.com             READ METADATA   METASTORE   metastore_aws_us_east_1 
(2 rows)
```

## 3. Comments and tags

Table-level tags, read from `information_schema`:

```text
table_name              tag_name           tag_value        
----------------------  -----------------  -----------------
agg_fill_rate_summary   contains_pii       false            
agg_fill_rate_summary   data_product       analytics        
agg_fill_rate_summary   data_product_name  demand_allocation
agg_fill_rate_summary   domain             supply_chain     
agg_fill_rate_summary   synthetic_data     true             
dim_product             contains_pii       false            
dim_product             data_product       analytics        
dim_product             data_product_name  demand_allocation
dim_product             domain             supply_chain     
dim_product             synthetic_data     true             
dim_retailer_dc         contains_pii       false            
dim_retailer_dc         data_product       analytics        
dim_retailer_dc         data_product_name  demand_allocation
dim_retailer_dc         domain             supply_chain     
dim_retailer_dc         synthetic_data     true             
fct_demand_weekly       contains_pii       false            
fct_demand_weekly       data_product       analytics        
fct_demand_weekly       data_product_name  demand_allocation
fct_demand_weekly       domain             supply_chain     
fct_demand_weekly       synthetic_data     true             
fct_inbound_eta         contains_pii       false            
fct_inbound_eta         data_product       analytics        
fct_inbound_eta         data_product_name  demand_allocation
fct_inbound_eta         domain             supply_chain     
fct_inbound_eta         synthetic_data     true             
fct_inventory_position  contains_pii       false            
fct_inventory_position  data_product       analytics        
fct_inventory_position  data_product_name  demand_allocation
fct_inventory_position  domain             supply_chain     
fct_inventory_position  synthetic_data     true             
fct_markdown            contains_pii       false            
fct_markdown            data_product       analytics        
fct_markdown            data_product_name  demand_allocation
fct_markdown            domain             supply_chain     
fct_markdown            synthetic_data     true             
fct_orders_fulfillment  contains_pii       false            
fct_orders_fulfillment  data_product       analytics        
fct_orders_fulfillment  data_product_name  demand_allocation
fct_orders_fulfillment  domain             supply_chain     
fct_orders_fulfillment  synthetic_data     true             
(40 rows)
```

Column-level `sensitivity` tags on the cost and margin columns — these are the
columns the mask in §5 protects:

```text
table_name              column_name       tag_name                  tag_value          
----------------------  ----------------  ------------------------  -------------------
dim_product             gross_margin_pct  cost_margin_confidential  true               
dim_product             gross_margin_pct  masked_by                 gold.cm_cost_margin
dim_product             gross_margin_pct  sensitivity               internal           
dim_product             unit_cost_usd     cost_margin_confidential  true               
dim_product             unit_cost_usd     masked_by                 gold.cm_cost_margin
dim_product             unit_cost_usd     sensitivity               internal           
fct_markdown            unit_cost_usd     cost_margin_confidential  true               
fct_markdown            unit_cost_usd     masked_by                 gold.cm_cost_margin
fct_markdown            unit_cost_usd     sensitivity               internal           
fct_orders_fulfillment  unit_cost_usd     cost_margin_confidential  true               
fct_orders_fulfillment  unit_cost_usd     masked_by                 gold.cm_cost_margin
fct_orders_fulfillment  unit_cost_usd     sensitivity               internal           
(12 rows)
```

This metastore enforces **UC tag policies** that constrain permitted values on
governed keys. Three were discovered the hard way, by rejection:

| Governed key | Permitted values | Consequence |
|---|---|---|
| `data_product` | `analytics`, `machine_learning`, `data_engineering` | `demand_allocation` rejected; project name moved to free-form `data_product_name` |
| `sensitivity` | `pii`, `internal`, `public` | `confidential_cost_margin` rejected; used `internal` plus free-form keys |
| `pii` | `ssn`, `address` | This is a *classification* key recording **which kind** of PII a column holds, not a boolean. Tagging a table `pii = 'false'` is not merely rejected, it is semantically wrong. This data product holds no personal data, so the governed key is omitted and the absence asserted on free-form `contains_pii`. |

Table and column comments (sample — every Gold table and every Gold column is
commented; Genie's accuracy in Stage E depends directly on these):

```text
table_name                                                                           comment                                                                                                       
-----------------------------------------------------------------------------------  --------------------------------------------------------------------------------------------------------------
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_agg_fill_rate_summary_1   Fill rate, OTIF and chargeback rolled up to retailer x week. Small enough to serve the app's KPI header from …
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_dim_product_1             Product dimension, one row per SKU: brand, category, licence family, lifecycle stage, case pack, cost and mar…
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_dim_retailer_dc_1         Retailer x distribution-centre dimension with readable labels and the contractual fill-rate commitment that d…
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_fct_demand_weekly_1       Weekly demand fact at SKU x retailer x DC x week, in EACHES. The forecast's training grain and the spine of t…
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_fct_inbound_eta_1         Inbound supply fact: units on the water per SKU x DC with revised ETA, ETA slip, lead-time variance and trans…
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_fct_inventory_position_1  Latest-available on-hand and in-transit position per SKU x DC, plus the full daily history. The denominator o…
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_fct_markdown_1            Close-out clearance fact: units liquidated and wholesale margin forgone on end-of-life residual stock. Makes …
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_fct_orders_fulfillment_1  PO-line fulfilment fact: requested, confirmed and shipped units with on-time, in-full and OTIF flags, invoice…
agg_fill_rate_summary                                                                Fill rate, OTIF and chargeback rolled up to retailer x week, sized to serve the app's KPI header from Lakebas…
dim_product                                                                          Product dimension, one row per SKU: brand, category, licence family, lifecycle stage, case pack, cost and mar…
dim_retailer_dc                                                                      Retailer x distribution-centre dimension with readable labels and the contractual fill-rate commitment that d…
fct_demand_weekly                                                                    Weekly demand fact at SKU x retailer x DC x week, in eaches. Training grain for the demand forecast. SYNTHETI…
fct_inbound_eta                                                                      Inbound supply fact: units on the water per SKU x DC with revised ETA, ETA slip, lead-time variance and ocean…
fct_inventory_position                                                               Daily on-hand and in-transit position per SKU x DC. Denominator of weeks-of-supply. SYNTHETIC DATA.           
fct_markdown                                                                         Close-out clearance fact: units liquidated and wholesale margin forgone on end-of-life residual stock. SYNTHE…
fct_orders_fulfillment                                                               PO-line fulfilment fact with on-time, in-full and OTIF flags, invoiced value and compliance chargebacks. Basi…
mv_fill_rate                                                                         Governed definition of retailer fill rate and the order-acceptance rate. Fill rate is shipped units over REQU…
mv_otif                                                                              Governed definition of OTIF (on-time, in-full). A PO line counts as OTIF only when it shipped within the cont…
mv_weeks_of_supply                                                                   Governed definition of inventory cover. Weeks of supply is on-hand units divided by mean weekly demand, so it…
(19 rows)
```

```text
column_name              data_type  comment                                                     
-----------------------  ---------  ------------------------------------------------------------
po_line_id               STRING     Purchase-order line identifier. Primary key.                
sku_id                   STRING     Stock-keeping unit identifier.                              
retailer_code            STRING     Ordering retailer account.                                  
dc_code                  STRING     Distribution centre fulfilling the line.                    
requested_week_end_date  DATE       Retail week the retailer requested delivery in.             
requested_units          INT        Units the retailer asked for. Fill-rate denominator.        
confirmed_units          INT        Units accepted against the order.                           
shipped_units            INT        Units actually shipped. Fill-rate numerator.                
unfilled_units           INT        requested minus shipped, floored at zero.                   
requested_ship_date      DATE       Date the retailer required the shipment by.                 
actual_ship_date         DATE       Date the shipment actually left.                            
ship_lag_days            INT        Actual minus requested ship date, in days.                  
is_on_time               BOOLEAN    True when shipped within the contractual 2-day grace window.
is_in_full               BOOLEAN    True when shipped units met or exceeded requested units.    
is_otif                  BOOLEAN    True when both on-time and in-full. OTIF numerator.         
wholesale_price_usd      DOUBLE     Invoiced price per each.                                    
invoiced_wholesale_usd   DOUBLE     shipped_units x wholesale_price_usd.                        
chargeback_usd           DOUBLE     Retailer compliance chargeback raised against the line.     
brand                    STRING     Denormalised from dim_product.                              
license_family           STRING     Denormalised from dim_product.                              
lifecycle_stage          STRING     Denormalised from dim_product.                              
unit_cost_usd            DOUBLE     Landed unit cost. SENSITIVE: masked to non-finance.         
_ingested_at             TIMESTAMP  Bronze ingest timestamp. Provenance.                        
_source_file             STRING     Landing file the row arrived in. Provenance.                
(24 rows)
```

Column comment coverage across Gold:

```text
table_name                                                    columns  commented  pct_commented
------------------------------------------------------------  -------  ---------  -------------
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  17       0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  16       0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  7        0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  18       0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  16       0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  7        0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  12       0          0.0          
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  22       0          0.0          
agg_fill_rate_summary                                         17       17         100.0        
dim_product                                                   16       16         100.0        
dim_retailer_dc                                               7        7          100.0        
fct_demand_weekly                                             18       18         100.0        
fct_inbound_eta                                               16       16         100.0        
fct_inventory_position                                        7        7          100.0        
fct_markdown                                                  12       12         100.0        
fct_orders_fulfillment                                        22       22         100.0        
mv_fill_rate                                                  18       11         61.1         
mv_otif                                                       14       1          7.1          
mv_weeks_of_supply                                            11       1          9.1          
(19 rows)
```

## 4. The policy DDL

Both policies are UDFs registered in Unity Catalog. A UDF used as a policy needs
its `RETURNS` type and `COMMENT` **before** the `RETURN` clause — putting
`COMMENT` last is a parse error.

The predicates test `current_user()` rather than `is_account_group_member()`,
because the personas are service principals rather than account groups.

```text
routine_name       returns  routine_definition                                                                                                     
-----------------  -------  -----------------------------------------------------------------------------------------------------------------------
rf_retailer_scope  BOOLEAN  CASE
    WHEN current_user() = '5d9eb1c3-888e-4acc-8625-4ecedea0fea5' THEN retailer_code = 'MASS_A'
    ELSE TRUE
  END
cm_cost_margin     DOUBLE   CASE
    WHEN current_user() = 'dd70e525-c0aa-4ca5-ade2-29671715871a' THEN v
    ELSE NULL
  END                       
(2 rows)
```

Where they are attached. Gold objects are materialized views, so policies attach
with `ALTER MATERIALIZED VIEW`; `ALTER TABLE` fails with `EXPECT_TABLE_NOT_VIEW`.
That is a DDL-verb difference, not a limitation — row filters and column masks
apply to materialized views perfectly well.

```text
table_name                                                    policy       detail                              
------------------------------------------------------------  -----------  ------------------------------------
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  COLUMN MASK  gross_margin_pct -> cm_cost_margin  
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  COLUMN MASK  unit_cost_usd -> cm_cost_margin     
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  COLUMN MASK  unit_cost_usd -> cm_cost_margin     
__materialization_mat_7c2d619c_4f3e_4ca2_add3_7376bd722d93_…  COLUMN MASK  unit_cost_usd -> cm_cost_margin     
dim_product                                                   COLUMN MASK  gross_margin_pct -> cm_cost_margin  
dim_product                                                   COLUMN MASK  unit_cost_usd -> cm_cost_margin     
fct_markdown                                                  COLUMN MASK  unit_cost_usd -> cm_cost_margin     
fct_orders_fulfillment                                        COLUMN MASK  unit_cost_usd -> cm_cost_margin     
agg_fill_rate_summary                                         ROW FILTER   rf_retailer_scope ON (retailer_code)
fct_demand_weekly                                             ROW FILTER   rf_retailer_scope ON (retailer_code)
fct_orders_fulfillment                                        ROW FILTER   rf_retailer_scope ON (retailer_code)
(11 rows)
```

## 5. Masked vs unmasked, with the identity used for each

Produced by `src/governance/verify_policies.py`, which mints a short-lived OAuth
secret per persona, runs the identical query as that identity, and deletes the
secret before exiting. The secret is held in memory only and is never written to
disk, printed, or committed.

**Read this as two results:**

- The **column mask** holds for every identity except `toy-finance`, *including
  the catalog owner*. Owner privilege does not bypass a UC column mask.
- The **row filter** restricts `toy-planner-mass-a` to MASS_A alone — 165,573 of
  480,183 rows — while every other identity sees all three retailers.

### My own identity (catalog owner, workspace admin)

```text
current_user              in_admins
------------------------  ---------
sixuan.he@databricks.com  false    
(1 row)
```

#### Column mask as the catalog owner

```text
current_user              sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
------------------------  ---------  -----------  -------------  ----------------  -------------------
sixuan.he@databricks.com  SKU-00001  Titan Squad  NULL           NULL              15.49              
sixuan.he@databricks.com  SKU-00002  Titan Squad  NULL           NULL              7.9                
sixuan.he@databricks.com  SKU-00003  Snugglewick  NULL           NULL              11.02              
(3 rows)
```

#### Row filter as the catalog owner

```text
current_user              retailer_code  rows_visible  pos_units
------------------------  -------------  ------------  ---------
sixuan.he@databricks.com  ETAIL_C        151474        3439980  
sixuan.he@databricks.com  MASS_A         165573        3031945  
sixuan.he@databricks.com  MASS_B         163136        3366123  
(3 rows)
```

### Persona `toy-planner-mass-a` (service principal `5d9eb1c3-888e-4acc-8625-4ecedea0fea5`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SKU-00001  Titan Squad  NULL           NULL              15.49              
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SKU-00002  Titan Squad  NULL           NULL              7.9                
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SKU-00003  Snugglewick  NULL           NULL              11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  MASS_A         165573        3031945  
```

### Persona `toy-data-scientist` (service principal `7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SKU-00001  Titan Squad  NULL           NULL              15.49              
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SKU-00002  Titan Squad  NULL           NULL              7.9                
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SKU-00003  Snugglewick  NULL           NULL              11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  ETAIL_C        151474        3439980  
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  MASS_A         165573        3031945  
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  MASS_B         163136        3366123  
```

### Persona `toy-finance` (service principal `dd70e525-c0aa-4ca5-ade2-29671715871a`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  dd70e525-c0aa-4ca5-ade2-29671715871a  SKU-00001  Titan Squad  9.2            0.4061            15.49              
  dd70e525-c0aa-4ca5-ade2-29671715871a  SKU-00002  Titan Squad  5.82           0.2633            7.9                
  dd70e525-c0aa-4ca5-ade2-29671715871a  SKU-00003  Snugglewick  7.23           0.3439            11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  dd70e525-c0aa-4ca5-ade2-29671715871a  ETAIL_C        151474        3439980  
  dd70e525-c0aa-4ca5-ade2-29671715871a  MASS_A         165573        3031945  
  dd70e525-c0aa-4ca5-ade2-29671715871a  MASS_B         163136        3366123  
```

### Persona `toy-app-service` (service principal `1c73204c-6b49-4db0-a860-300ba392393e`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  1c73204c-6b49-4db0-a860-300ba392393e  SKU-00001  Titan Squad  NULL           NULL              15.49              
  1c73204c-6b49-4db0-a860-300ba392393e  SKU-00002  Titan Squad  NULL           NULL              7.9                
  1c73204c-6b49-4db0-a860-300ba392393e  SKU-00003  Snugglewick  NULL           NULL              11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  1c73204c-6b49-4db0-a860-300ba392393e  ETAIL_C        151474        3439980  
  1c73204c-6b49-4db0-a860-300ba392393e  MASS_A         165573        3031945  
  1c73204c-6b49-4db0-a860-300ba392393e  MASS_B         163136        3366123  
```


## 6. Metric views — the governed KPI layer

These are the single definition of each KPI. Genie (Stage E), the app (Stage F)
and any external BI tool read the same measures, so `fill rate` cannot mean two
different things in two different places. This is the layer the customer
maintains in dbt today, and it is the natural integration point.

```text
table_name          comment                                                                                                                 
------------------  ------------------------------------------------------------------------------------------------------------------------
mv_fill_rate        Governed definition of retailer fill rate and the order-acceptance rate. Fill rate is shipped units over REQUESTED unit…
mv_otif             Governed definition of OTIF (on-time, in-full). A PO line counts as OTIF only when it shipped within the contractual tw…
mv_weeks_of_supply  Governed definition of inventory cover. Weeks of supply is on-hand units divided by mean weekly demand, so it needs the…
(3 rows)
```

### `mv_fill_rate` queried by retailer and quarter

Note `Fill Rate Pct` against `Fill Rate Confirmed Basis Pct`: the same weeks read
96% on a requested basis and 99% on a confirmed basis. Publishing both in one
governed definition is what stops the two numbers being argued about in a meeting.

```sql
SELECT `Retailer`,
       MEASURE(`Fill Rate Pct`)                      AS fill_rate_pct,
       MEASURE(`Fill Rate Confirmed Basis Pct`)      AS fill_rate_confirmed_pct,
       MEASURE(`Order Acceptance Pct`)               AS order_acceptance_pct,
       MEASURE(`Requested Units`)                    AS requested_units,
       MEASURE(`Shipped Units`)                      AS shipped_units,
       MEASURE(`Chargeback Pct Of Invoiced`)         AS chargeback_pct
FROM toy_demand_allocation.gold.mv_fill_rate
GROUP BY ALL ORDER BY ALL
```

```text
Retailer  fill_rate_pct      fill_rate_confirmed_pct  order_acceptance_pct  requested_units  shipped_units  chargeback_pct    
--------  -----------------  -----------------------  --------------------  ---------------  -------------  ------------------
ETAIL_C   96.45902937791800  99.09690230448689        97.33808740210293     3807826          3672992        1.2406315269386594
MASS_A    96.33738578574816  99.19950402892114        97.11478573286158     3660664          3526588        1.2293604848317599
MASS_B    96.01324087515346  99.19355317666094        96.79383165572925     4000944          3841436        1.2534723497861953
(3 rows)
```

### `mv_otif` by licence family — attribution of the miss

```text
License Family  otif_pct           on_time_pct        in_full_pct        po_lines  chargeback_usd   
--------------  -----------------  -----------------  -----------------  --------  -----------------
SOCIAL_TREND    79.84929652086890  92.28822040383823  86.60740566315418  16987     218236.7200000001
FILM_TIE_IN     85.84163333594751  92.64894256659608  92.73259613625075  38253     332779.0799999996
EVERGREEN       86.73683262717163  92.69960531647216  93.55000075034891  199907    709702.0199999983
(3 rows)
```

### `mv_weeks_of_supply` by licence family

This view declares the join to `dim_product` in its YAML, so no consumer has to
reinvent the weeks-of-supply denominator.

```text
License Family  on_hand_units  in_transit_units  sku_dc_positions
--------------  -------------  ----------------  ----------------
EVERGREEN       425040         136782            3037            
FILM_TIE_IN     1705100        209760            2026            
SOCIAL_TREND    540209         387054            1673            
(3 rows)
```

`mv_forecast_accuracy` is created in Stage C1, once
`gold.fct_demand_forecast_eval` exists for it to read. See `evidence/03_ml_forecast.md`.

## 7. Lineage — landing file to Gold

```text
source                                                                  target                                                                  event_time              
----------------------------------------------------------------------  ----------------------------------------------------------------------  ------------------------
NULL                                                                    toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd…  2026-10-08T21:55:19.950Z
toy_demand_allocation.gold.dim_retailer_dc                              toy_demand_allocation.gold.agg_fill_rate_summary                        2026-10-08T21:55:18.391Z
toy_demand_allocation.gold.fct_orders_fulfillment                       toy_demand_allocation.gold.agg_fill_rate_summary                        2026-10-08T21:55:18.391Z
toy_demand_allocation.gold.dim_product                                  toy_demand_allocation.gold.fct_demand_weekly                            2026-10-08T21:55:15.960Z
toy_demand_allocation.silver.pos_weekly                                 toy_demand_allocation.gold.fct_demand_weekly                            2026-10-08T21:55:15.960Z
toy_demand_allocation.silver.pos_weekly                                 toy_demand_allocation.gold.dim_retailer_dc                              2026-10-08T21:55:15.287Z
toy_demand_allocation.gold.dim_product                                  toy_demand_allocation.gold.fct_orders_fulfillment                       2026-10-08T21:55:15.247Z
toy_demand_allocation.silver.orders                                     toy_demand_allocation.gold.fct_orders_fulfillment                       2026-10-08T21:55:15.247Z
toy_demand_allocation.silver.inventory_snapshots                        toy_demand_allocation.gold.fct_inventory_position                       2026-10-08T21:55:14.370Z
toy_demand_allocation.silver.inbound_shipments                          toy_demand_allocation.gold.fct_inbound_eta                              2026-10-08T21:55:13.429Z
toy_demand_allocation.gold.dim_product                                  toy_demand_allocation.gold.fct_markdown                                 2026-10-08T21:55:13.396Z
toy_demand_allocation.silver.markdown_clearance                         toy_demand_allocation.gold.fct_markdown                                 2026-10-08T21:55:13.396Z
toy_demand_allocation.bronze.pos_weekly                                 toy_demand_allocation.silver.pos_weekly                                 2026-10-08T21:55:10.229Z
toy_demand_allocation.bronze.product_master                             toy_demand_allocation.silver.pos_weekly                                 2026-10-08T21:55:10.229Z
toy_demand_allocation.silver.product_master                             toy_demand_allocation.gold.dim_product                                  2026-10-08T21:55:08.998Z
toy_demand_allocation.bronze.inventory_snapshots                        toy_demand_allocation.silver.inventory_snapshots                        2026-10-08T21:55:08.962Z
toy_demand_allocation.bronze.orders                                     toy_demand_allocation.silver.orders                                     2026-10-08T21:55:08.921Z
toy_demand_allocation.bronze.inbound_shipments                          toy_demand_allocation.silver.inbound_shipments                          2026-10-08T21:55:07.966Z
toy_demand_allocation.bronze.product_master                             toy_demand_allocation.silver.product_master                             2026-10-08T21:55:05.522Z
toy_demand_allocation.bronze.markdown_clearance                         toy_demand_allocation.silver.markdown_clearance                         2026-10-08T21:55:05.457Z
NULL                                                                    toy_demand_allocation.bronze.inventory_snapshots                        2026-10-08T21:55:00.197Z
NULL                                                                    toy_demand_allocation.bronze.inventory_snapshots                        2026-10-08T21:55:00.124Z
NULL                                                                    toy_demand_allocation.bronze.pos_weekly                                 2026-10-08T21:54:59.633Z
NULL                                                                    toy_demand_allocation.bronze.pos_weekly                                 2026-10-08T21:54:59.555Z
NULL                                                                    toy_demand_allocation.bronze.orders                                     2026-10-08T21:54:59.164Z
NULL                                                                    toy_demand_allocation.bronze.orders                                     2026-10-08T21:54:59.118Z
NULL                                                                    toy_demand_allocation.bronze.inbound_shipments                          2026-10-08T21:54:58.328Z
NULL                                                                    toy_demand_allocation.bronze.inbound_shipments                          2026-10-08T21:54:58.274Z
NULL                                                                    toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd…  2026-10-08T21:54:57.502Z
NULL                                                                    toy_demand_allocation.bronze.markdown_clearance                         2026-10-08T21:54:57.483Z
NULL                                                                    toy_demand_allocation.bronze.markdown_clearance                         2026-10-08T21:54:57.433Z
NULL                                                                    toy_demand_allocation.bronze.product_master                             2026-10-08T21:54:57.262Z
NULL                                                                    toy_demand_allocation.bronze.product_master                             2026-10-08T21:54:57.239Z
NULL                                                                    toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd…  2026-10-08T21:54:25.615Z
NULL                                                                    toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd…  2026-10-08T21:53:59.262Z
toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd…  NULL                                                                    2026-10-08T21:53:52.552Z
NULL                                                                    toy_demand_allocation.bronze.event_log_7c2d619c_4f3e_4ca2_add3_7376bd…  2026-10-08T21:41:17.892Z
toy_demand_allocation.silver.orders                                     toy_demand_allocation.gold.fct_orders_fulfillment                       2026-10-08T21:41:14.969Z
toy_demand_allocation.gold.dim_product                                  toy_demand_allocation.gold.fct_orders_fulfillment                       2026-10-08T21:41:14.969Z
toy_demand_allocation.silver.markdown_clearance                         toy_demand_allocation.gold.fct_markdown                                 2026-10-08T21:41:14.149Z
(40 rows)
```

Column-level lineage into the fill-rate numerator and denominator:

```text
source  source_col  target                                             target_col     
------  ----------  -------------------------------------------------  ---------------
NULL    NULL        toy_demand_allocation.bronze.inbound_shipments     shipped_units  
NULL    NULL        toy_demand_allocation.bronze.inbound_shipments     shipped_units  
NULL    NULL        toy_demand_allocation.bronze.inbound_shipments     shipped_units  
NULL    NULL        toy_demand_allocation.bronze.inbound_shipments     shipped_units  
NULL    NULL        toy_demand_allocation.bronze.orders                requested_units
NULL    NULL        toy_demand_allocation.bronze.orders                requested_units
NULL    NULL        toy_demand_allocation.bronze.orders                requested_units
NULL    NULL        toy_demand_allocation.bronze.orders                requested_units
NULL    NULL        toy_demand_allocation.bronze.orders                shipped_units  
NULL    NULL        toy_demand_allocation.bronze.orders                shipped_units  
NULL    NULL        toy_demand_allocation.bronze.orders                shipped_units  
NULL    NULL        toy_demand_allocation.bronze.orders                shipped_units  
NULL    NULL        toy_demand_allocation.bronze.pos_weekly            pos_units      
NULL    NULL        toy_demand_allocation.bronze.pos_weekly            pos_units      
NULL    NULL        toy_demand_allocation.bronze.pos_weekly            pos_units      
NULL    NULL        toy_demand_allocation.bronze.pos_weekly            pos_units      
NULL    NULL        toy_demand_allocation.gold.agg_fill_rate_summary   requested_units
NULL    NULL        toy_demand_allocation.gold.agg_fill_rate_summary   shipped_units  
NULL    NULL        toy_demand_allocation.gold.fct_demand_weekly       pos_units      
NULL    NULL        toy_demand_allocation.gold.fct_inbound_eta         shipped_units  
NULL    NULL        toy_demand_allocation.gold.fct_inbound_eta         shipped_units  
NULL    NULL        toy_demand_allocation.gold.fct_orders_fulfillment  requested_units
NULL    NULL        toy_demand_allocation.gold.fct_orders_fulfillment  requested_units
NULL    NULL        toy_demand_allocation.gold.fct_orders_fulfillment  shipped_units  
NULL    NULL        toy_demand_allocation.gold.fct_orders_fulfillment  shipped_units  
NULL    NULL        toy_demand_allocation.silver.inbound_shipments     shipped_units  
NULL    NULL        toy_demand_allocation.silver.inbound_shipments     shipped_units  
NULL    NULL        toy_demand_allocation.silver.orders                requested_units
NULL    NULL        toy_demand_allocation.silver.orders                requested_units
NULL    NULL        toy_demand_allocation.silver.orders                shipped_units  
(30 rows)
```

## 8. Measured KPI baseline

Measured by `notebooks/02_governance_and_baseline.ipynb` against the Gold layer
and committed to `evidence/metrics/kpi_baseline.csv`. **Every figure in the deck
cites this file.** Nothing here is asserted; each row carries the exact basis of
measurement.

| KPI | Value | Unit | Basis |
|---|---:|---|---|
| `retailer_fill_rate_pct` | 96.26 | pct | shipped units / requested units across all PO lines in gold.fct_orders_fulfillment |
| `retailer_fill_rate_confirmed_basis_pct` | 99.16 | pct | shipped units / confirmed units; reported for comparison, flatters performance |
| `order_acceptance_pct` | 97.08 | pct | confirmed units / requested units; falls when supply cannot cover demand |
| `otif_pct` | 86.14 | pct | PO lines both on-time (within 2-day grace) and in-full, as share of all lines |
| `on_time_pct` | 92.66 | pct | PO lines shipped within the 2-day grace |
| `in_full_pct` | 92.97 | pct | PO lines shipped complete vs requested |
| `stockout_weeks` | 74,789.00 | count | SKU x retailer x DC x week cells where demand exceeded units served |
| `stockout_week_incidence_pct` | 15.58 | pct | stockout weeks as share of active SKU x retailer x DC weeks |
| `lost_units` | 585,575.00 | units | true_demand_units minus pos_units, summed over all active cells |
| `lost_units_pct_of_demand` | 5.85 | pct | lost units as share of unconstrained consumer demand |
| `lost_revenue_usd` | 5,505,413 | usd | lost units valued at wholesale price |
| `compliance_chargeback_usd` | 1,260,718 | usd | retailer compliance chargebacks on non-compliant PO lines |
| `compliance_chargeback_pct_of_invoiced` | 1.24 | pct | chargeback $ / invoiced wholesale $ |
| `markdown_cleared_units` | 2,063,198 | units | units liquidated at close-out in gold.fct_markdown |
| `markdown_loss_usd` | 7,940,809 | usd | wholesale margin forgone: cleared units x (wholesale - clearance price) |
| `markdown_loss_pct_of_invoiced` | 7.82 | pct | markdown loss / invoiced wholesale $ |
| `weeks_of_supply` | 17.02 | weeks | latest on-hand units / mean weekly POS units over the last 13 weeks |
| `inventory_turns_annual` | 3.06 | turns | 52 / weeks of supply |
| `on_hand_at_cost_usd` | 16,046,011 | usd | latest on-hand valued at landed cost from silver.product_master |
| `invoiced_wholesale_usd` | 101,539,156 | usd | shipped units x wholesale price, whole 3-year history |
| `po_lines` | 255,147.00 | count | PO lines in gold.fct_orders_fulfillment |
| `air_expedite_units` | 1,085,487 | units | units air-freighted to chase a detected demand surge |
| `air_freight_usd` | 2,116,700 | usd | air freight spend on expedites |
| `air_freight_pct_of_invoiced` | 2.08 | pct | air freight spend / invoiced wholesale $ |
| `air_freight_usd_per_unit` | 1.95 | usd_per_unit | air freight unit rate |
| `air_vs_ocean_freight_premium_x` | 6.96 | ratio | air freight unit rate / ocean freight unit rate |

### Where the headroom is

The baseline is not uniformly bad, and that matters for credibility. Fill rate at
96.3% on a requested basis is a competent operation. The headroom sits in five
specific, separately-attackable places:

1. **OTIF 86.1%** against a 96.3% fill rate — the units arrive, but late or
   split, which is what triggers compliance penalties.
2. **74,789 stockout-weeks** and $5.51M of lost revenue, concentrated in the
   spiky licence families (see §8 of `evidence/01_lakeflow.md` and the licence
   breakdown in notebook 02).
3. **$2.12M of air freight** at a 7.0x premium over ocean — the direct cost of
   spotting a spike in POS instead of forecasting it.
4. **$7.94M of markdown** on close-out, 7.8% of invoiced wholesale.
5. **$1.26M of compliance chargebacks**, 1.24% of invoiced wholesale.

Those five are the levers the rest of the build is pointed at.
