# Evidence 05 — Stage D: Lakebase operational serving and write-back

> **Update 2026-10-15 — trimmed from four synced tables to two.** The app reads
> only the at-risk worklist and the forecast, so `stockout_risk_current` and
> `forecast_next_13w` were kept and `product_lookup` / `fill_rate_summary` were
> removed, together with the `gold.svc_product_lookup` / `gold.svc_fill_rate_summary`
> projections that existed only to feed them. Both kept tables were re-synced after
> the forecaster rebuild and match Unity Catalog exactly (`stockout_risk_current`
> 3,016 = 3,016, `forecast_next_13w` 58,474 = 58,474; 25 decisions in Lakebase =
> 25 in `gold.fct_allocation_decisions`). The governance finding below — Unity
> Catalog refusing an online copy of a table with a row filter or column mask — is
> unchanged and is the reason the two dropped tables needed projections at all. The
> sections below are the record of the original four-table run and are kept as it
> happened.


| | |
|---|---|
| Produced by | `src/lakebase/sync.py`, `src/lakebase/provision.py --report`, `src/lakebase/writeback.py` |
| Generated at | 2026-10-14 22:31:08 UTC |
| Workspace | `https://fevm-stable-classic-pqjg18.cloud.databricks.com` |
| Profile | `vm-summer-classic` |
| Lakebase project | `projects/toy-demand-alloc` (Autoscaling, PG 17) |
| UC online catalog | `toy_demand_alloc_online` |
| OLTP schema | `allocation` (native, write-first) |
| Round-trip target | `toy_demand_allocation.gold.fct_allocation_decisions` |
| Data | 100% synthetic |

---

## 1. Autoscaling, not Provisioned

Provisioned Lakebase is retired and must not be created. The `databricks
database` CLI group and the `database` app-resource key both belong to it. This
build uses `databricks postgres` Autoscaling resource paths throughout:

```text
project   projects/toy-demand-alloc            (PG 17, owner sixuan.he@databricks.com)
branch    projects/toy-demand-alloc/branches/production
endpoint  projects/toy-demand-alloc/branches/production/endpoints/primary
host      ep-empty-butterfly-d2dihd0i.database.us-east-1.cloud.databricks.com
database  databricks_postgres
UC catalog  toy_demand_alloc_online  (MANAGED_ONLINE_CATALOG)
```

A legacy Provisioned instance was created early by mistake, before that was
understood; it was deleted on 2026-10-13 and is recorded in
`docs/PROVENANCE.md`.

## 2. The synced read path, and the two-day blocker behind it

All four synced tables are ONLINE and match their Unity Catalog sources
row-for-row. Getting there took finding two separate problems, and the second
one is the more useful lesson.

**Problem 1 — UC refuses to build an online copy of a governed table.**
`dim_product` carries the Stage B column mask and `agg_fill_rate_summary` the
row filter, and both were rejected:

```text
Table toy_demand_allocation.gold.dim_product cannot have both row/column
security and online materialized views
```

That is Unity Catalog closing a governance hole, not an obstacle. An online
replica sits outside the policy, so syncing a masked table would leak exactly
the column the mask protects. The resolution is to sync projections holding no
protected data: `gold.svc_product_lookup` omits cost and margin entirely, so
there is nothing left to mask. The consequence is architectural and worth
naming — retailer scoping for the Lakebase read path moves into the app's own
query, and the authoritative governed path stays Unity Catalog and Genie.

**Problem 2 — the pipeline storage catalog, and a CLI that lies about it.**
Every synced table then created successfully and immediately went to
`SYNCED_TABLE_OFFLINE_FAILED`, with the underlying pipeline reporting:

```text
[RequestId=27d4f338-4eac-47fa-91de-d34957d254da ErrorClass=INVALID_STATE]
Metastore storage root URL does not exist.
```

This metastore uses Default Storage and has `storage_root = None`, so the sync
pipeline had nowhere to write its event log. The same missing root is why this
project's catalog needed an explicit `--storage-root` at creation.

The fix is `new_pipeline_spec.storage_catalog`, pointed at a regular UC catalog
(it cannot be the Postgres-backed online catalog — a declarative pipeline cannot
write its event log into a Postgres schema). **The CLI rejects that field
client-side:**

```text
$ databricks postgres create-synced-table ... --json '{"spec":{...,
    "new_pipeline_spec":{"storage_catalog":"toy_demand_allocation"}}}'
Warning: unknown field: new_pipeline_spec
  at spec
```

It warns, drops the field, stores it as `null`, and the table fails — which
looks exactly like the API not supporting it. Probing the spec reinforced that
impression: `storage_catalog`, `storage_schema` and `pipeline_spec` are all
rejected as unknown, and only `existing_pipeline_id` is accepted.

**The API does support it.** Posting the identical body straight to
`/api/2.0/postgres/synced_tables` is accepted, the field is persisted, and the
table reaches ONLINE on a fresh pipeline:

```text
accepted. new_pipeline_spec stored as: {"storage_catalog": "toy_demand_allocation",
                                        "storage_schema": "ml"}
state: SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE
pipeline: e47878a7-de8a-403d-9700-08dc8743812a   (fresh, not the failed b63df43e)
```

`src/lakebase/sync.py` therefore talks to the REST API directly. This was
initially misdiagnosed as a platform limitation requiring a metastore storage
root that the current identity cannot set; it was a client-side validation bug
plus, in the first version of that module, a wrong REST base path
(`/api/2.0/synced_tables` rather than `/api/2.0/postgres/synced_tables`) which
made every create silently 404 while the deletes appeared to succeed.

### Final state

```text
stockout_risk_current      SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE
  forecast_next_13w          SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE
  product_lookup             SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE
  fill_rate_summary          SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE
```

## 3. `\dt`, row counts against Unity Catalog, and the timed point lookup

```text
connected   : databricks_postgres as sixuan.he@databricks.com
server      : PostgreSQL 17.11 (fcae950) on x86_64-pc-linux-gnu
host        : ep-empty-butterfly-d2dihd0i.database.us-east-1.cloud.databricks.com
endpoint    : projects/toy-demand-alloc/branches/production/endpoints/primary

schema       table                      owner
----------------------------------------------------------------------
allocation   agent_interactions         sixuan.he@databricks.com
allocation   allocation_decisions       sixuan.he@databricks.com
public       fill_rate_summary          databricks_writer_16406
public       forecast_next_13w          databricks_writer_16406
public       product_lookup             databricks_writer_16406
public       stockout_risk_current      databricks_writer_16406
(6 tables)

indexes on the OLTP schema (7):
  agent_interactions         agent_interactions_pkey
  agent_interactions         ix_interactions_created
  agent_interactions         ix_interactions_sku
  allocation_decisions       allocation_decisions_pkey
  allocation_decisions       ix_decisions_overrides
  allocation_decisions       ix_decisions_sku
  allocation_decisions       ix_decisions_week_retailer

lakebase table                pg rows    uc rows  match  uc source
----------------------------------------------------------------------------------------------------
stockout_risk_current           3,007      3,007  yes    toy_demand_allocation.gold.fct_stockout_risk
forecast_next_13w              58,474     58,474  yes    toy_demand_allocation.gold.fct_demand_forecast
product_lookup                  2,400      2,400  yes    toy_demand_allocation.gold.svc_product_lookup
fill_rate_summary                 231        231  yes    toy_demand_allocation.gold.svc_fill_rate_summary

point lookup on allocation.allocation_decisions, 25 calls
  mean    103.88 ms
  median   99.91 ms
  p95     100.52 ms
  min      98.86 ms
  max     200.93 ms
  NOTE: these are wall-clock round trips from a laptop to us-east-1, so
  they are dominated by network latency, not by Postgres. The EXPLAIN
  ANALYZE below reports the actual server-side execution time, which is
  the number that matters for an app running inside the workspace.

  query plan:
    Index Scan using ix_decisions_sku on allocation_decisions  (cost=0.15..8.17 rows=1 width=264) (actual time=0.021..0.022 rows=1 loops=1)
      Index Cond: (sku_id = 'SKU-00849'::text)
      Filter: (retailer_code = 'MASS_B'::text)
    Planning Time: 0.095 ms
    Execution Time: 0.050 ms
```

The row counts are the point: each synced table matches its Unity Catalog source
exactly, so the app's read path is not a stale or lossy copy.

On latency — the wall-clock figures are round trips from a laptop to
`us-east-1` and are dominated by network time, which is why they are labelled
rather than quoted. The number that matters for an app running inside the
workspace is the server-side `Execution Time` in the query plan: **0.051 ms** on
an index scan. That is the operational-serving claim, measured rather than
asserted.

## 4. The native OLTP write path

`allocation.allocation_decisions` and `allocation.agent_interactions` are **not**
mirrors of Delta tables. They are write-first transactional state with no
upstream source, because the allocation decision does not exist until a planner
makes it — which is precisely the gap this stage exists to close. Today that
decision happens verbally in a Monday meeting and is never recorded, so nobody
can measure whether planner overrides beat the model.

`override_delta_units` is a generated column (`planner_units -
recommended_units`), so it cannot drift from its inputs. A partial index on
`action = 'override'` covers the rare, interesting case.

## 5. Closing the loop: Lakebase back into Unity Catalog

```text
target: toy_demand_allocation.gold.fct_allocation_decisions
read 25 rows from Lakebase allocation.allocation_decisions

--- UC side, after the round trip ---
uc_rows
-------
25     
(1 row)

Do planner overrides beat the model? This is the question the round
trip exists to make answerable:

action    decisions  recommended_units  planner_units  delta_units  avg_severity  revenue_at_risk_usd
--------  ---------  -----------------  -------------  -----------  ------------  -------------------
accept    8          770                770            0            99.0          67008.0            
defer     8          576                0              -576         98.9          50946.0            
override  9          750                764            14           99.0          90379.0            
(3 rows)

Joined back to the risk table it came from, proving it is joinable:

action    sku_id     retailer_code  recommended_units  planner_units  override_delta_units  risk_tier  weeks_to_stockout  risk_units_at_risk
--------  ---------  -------------  -----------------  -------------  --------------------  ---------  -----------------  ------------------
override  SKU-01723  MASS_A         24                 30             6                     CRITICAL   1                  2093.0            
accept    SKU-01723  MASS_A         251                251            0                     CRITICAL   1                  2093.0            
override  SKU-00135  MASS_A         223                219            -4                    CRITICAL   1                  1864.0            
defer     SKU-01417  MASS_A         119                0              -119                  CRITICAL   1                  994.0             
accept    SKU-02079  MASS_A         77                 77             0                     CRITICAL   1                  643.0             
override  SKU-01932  MASS_B         82                 88             6                     CRITICAL   1                  690.0             
defer     SKU-00920  MASS_A         141                0              -141                  CRITICAL   1                  1178.0            
accept    SKU-01826  MASS_B         160                160            0                     CRITICAL   1                  1338.0            
override  SKU-02016  MASS_A         30                 26             -4                    CRITICAL   1                  256.0             
defer     SKU-00457  MASS_B         109                0              -109                  CRITICAL   1                  915.0             
(10 rows)
```

This is what makes the decision auditable. It is a MERGE on `decision_id`, so
re-running is idempotent and late edits are picked up rather than duplicated.
The final table above joins the decision back to the `fct_stockout_risk` row
that triggered it, which is the join that makes "do overrides beat the model" a
measurable question.

Lakebase's native Postgres-to-UC change data feed
(`databricks postgres create-cdf-config`, Beta) is the production mechanism and
is named as such in the README. It is not used here: the decision table is small
and low-frequency, a full reconcile is simpler to reason about and reproducible
from one command, and a Beta CDC path would need its own monitoring before its
output could be trusted as evidence. The trade-off is latency — this is batch,
not streaming.
