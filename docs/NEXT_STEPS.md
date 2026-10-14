# Continuation notes

Handoff for resuming this build in a fresh session. Stages A, B, C1 and C2 are
done; D (Lakebase), E (Genie), F (the app) and the deck are not. Read
`README.md` for status and `docs/PROVENANCE.md` for every resource ID.

## What Stage D can rely on

`gold.fct_stockout_risk` is built and is the spine: 3,006 at-risk SKU x retailer
rows tiered CRITICAL / HIGH / MEDIUM / LOW, with `severity_score`,
`units_at_risk`, `revenue_at_risk_usd` and `projected_stockout_week`. The app
worklist sorts on `severity_score` and filters on `risk_tier`.
`gold.fct_demand_forecast` holds 13 weeks of p50/p90 per series, and
`gold.agg_fill_rate_summary` is already sized for the app's KPI header. The
agent is live on serving endpoint `toy-allocation-agent` and the app's agent
panel should call that rather than re-instantiating the agent.

## Stage C2 additions worth knowing

- MLflow 3 on this cluster needs a notebook-scoped `%pip install` plus
  `dbutils.library.restartPython()`; a plain or `--target` install does not take
  path precedence. Pin `pyopenssl>=24.2` alongside it or `log_model` dies with
  `module 'lib' has no attribute 'GEN_EMAIL'`.
- Declare `httpx` explicitly in `pip_requirements` for anything that serves the
  `openai` client, or the endpoint deploys READY and fails every request.
- Query a ResponsesAgent endpoint on the raw `/invocations` route with
  `{"input": [...]}`. `WorkspaceClient.query()` wraps into legacy `{"inputs":
  ...}` and is rejected.
- The MLflow prompt registry is disabled org-wide; `src/agent/prompt_store.py`
  is the UC-backed substitute. Inside Model Serving it falls back to the
  source-file tier because the endpoint identity cannot reach the warehouse —
  granting that service principal `SELECT` on `ml.prompt_versions` and `CAN USE`
  on the warehouse would close it.
- `mlflow.genai.scorers.Guidelines` requires a global `guidelines=` list and
  raises a pydantic `ValidationError` without one, so it cannot read per-row
  expectations.

## Live resources (verified 2026-10-08)

| | |
|---|---|
| Profile | `vm-summer-classic` — auth was live; re-run `databricks auth login --profile vm-summer-classic` if stale |
| Catalog | `toy_demand_allocation` (owned by `sixuan.he@databricks.com`) |
| Warehouse | `a6857db5911f7af1` — **stops when idle, start it before any SQL** |
| ML cluster | `1008-205814-qhbfm7tc` — 90-min autotermination, **will be TERMINATED; restart it** |
| Pipeline | `7c2d619c-4f3e-4ca2-add3-7376bd722d93`, last update `95162037-84c7-4d9d-b01f-3eab305de31a` COMPLETED |
| Gold tables | Built and populated. Stage C1 can read them immediately; no need to re-run Stage A |

Re-running Stage A is unnecessary and costs ~10 minutes. The landing volume and
all 20 medallion tables persist.

## Things learned the hard way — don't rediscover these

1. **The Databricks MCP server is bound to the broken `DEFAULT` profile**
   (points at `fe-vm-vm-summer`, invalid refresh token). Its tools target the
   *wrong workspace*. Use the CLI and the Python SDK with
   `profile="vm-summer-classic"` throughout. `tools/dbsql.py` already does.
2. **Unity Catalog resolves only account-level principals.** This workspace has
   no account-level auth, so workspace-local groups fail every `GRANT` with
   `PRINCIPAL_DOES_NOT_EXIST`. The personas are service principals; IDs are in
   `docs/PROVENANCE.md` and `src/governance/apply.py`.
3. **Gold objects are materialized views.** Policies and column comments attach
   with `ALTER MATERIALIZED VIEW`, not `ALTER TABLE`.
4. **Policy UDFs need `RETURNS <type>` and `COMMENT` before `RETURN`.**
5. **This metastore enforces UC tag policies.** `data_product` ∈
   {analytics, machine_learning, data_engineering}; `sensitivity` ∈
   {pii, internal, public}; `pii` ∈ {ssn, address} (a classification key, not a
   boolean). There is no tag-policy read API on this version.
6. **Metric views need DBR 17.2+**, so they must be created and queried through
   the serverless warehouse, never the 16.4 ML cluster.
7. **SDP forbids `DataFrame.collect()`** inside a decorated function, and
   mixing `expect_all_or_fail` with `expect_all_or_drop` halts the update
   because every expectation is evaluated against the dataset the function
   returns.
8. **The column mask hides `unit_cost_usd` from every identity except
   `toy-finance`, including the catalog owner.** Any cost-based arithmetic in a
   notebook must read cost from `silver.product_master`. This bit the KPI
   baseline and will bite Stage C1's margin-weighted allocator.
9. **zsh:** `UID` is readonly — don't use it as a shell variable.
10. **FMAPI:** `databricks-claude-sonnet-4-5` exists and is READY, but resolve
    at runtime from `config.FMAPI_ENDPOINT_PREFERENCE` and record which one was
    used.

## How to run a notebook and commit real outputs

```bash
python tools/dbx_notebook.py notebooks/03_demand_forecast.py \
    --cluster-id 1008-205814-qhbfm7tc
```

Authoring format is jupytext percent (`# %%` for code, `# %% [markdown]` for
prose). The harness executes cell-by-cell on the cluster in one execution
context and writes the captured output into `notebooks/*.ipynb` plus
`notebooks/exports/*.md`. A failing cell is captured as an error and halts the
run, so a committed notebook cannot show a passing cell that did not run. Use
`--keep-going` only deliberately.

On the cluster, `sys.path` must include
`/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/src`, and
after editing anything under `src/` re-upload it:

```bash
databricks workspace import-dir src \
  /Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/src \
  --overwrite --profile vm-summer-classic
```

## Stage C1 — COMPLETE (kept for reference)

Target: `notebooks/03_demand_forecast.py`, evidence `evidence/03_ml_forecast.md`.

1. Features from `gold.fct_demand_weekly`: lags, rolling means, weeks-since-launch,
   licence-family seasonality, promo flag, holiday calendar. Register in Feature
   Engineering with a `FeatureLookup` training set for point-in-time correctness.
   Target table name is already declared as `config.FEATURE_TABLE`.
2. Sweep **≥5 candidates across ≥3 families** into one MLflow experiment
   (`config.MLFLOW_EXPERIMENT`): seasonal-naive baseline, LightGBM, XGBoost,
   sklearn GBM or RandomForest, and a statistical time-series model. Consistent
   metrics on a **time-respecting** holdout: WAPE, MAPE, bias, pinball loss at
   p50/p90.
3. Leaderboard via `mlflow.search_runs()` → print in-notebook **and** write
   `evidence/metrics/forecast_leaderboard.csv`.
4. Register the winner as `config.MODEL_NAME`, set `@champion`; set
   `@challenger` on the runner-up. Print the version table and both aliases.
5. Batch-score 13 weeks forward (p50 + p90) into `gold.fct_demand_forecast`.
6. Build `gold.fct_stockout_risk`: forecast p90 vs on-hand + inbound-by-ETA →
   projected stockout week, units at risk, revenue at risk, severity score.
   **This table is the spine of Stages C2, D, E and F** — get its grain and
   column names right before moving on.
7. Create `gold.fct_demand_forecast_eval` (actual vs p50/p90 on the holdout,
   with `model_version` and `model_alias`), then apply the deferred metric view:
   `python -m src.governance.apply --with-forecast`. The YAML for
   `mv_forecast_accuracy` is already written and matches that schema.
8. One markdown cell comparing a generic incumbent AutoML platform to
   Databricks: governed features, open model format, lineage from raw file to
   prediction, one platform for serving plus agent plus BI, no export handoff.

Note `gold.fct_demand_forecast`, `gold.fct_stockout_risk` and
`gold.fct_demand_forecast_eval` will be **real Delta tables** written by the
notebook rather than pipeline materialized views, so they take `ALTER TABLE`
for policies — and the row filter on retailer should be extended to
`fct_stockout_risk`, since that is what the planner persona actually reads in
the app.

## Stage D — PARTIALLY COMPLETE, and the blocker is specific

**What works and is evidenced:** a Lakebase Autoscaling project
(`projects/toy-demand-alloc`, PG 17) registered in Unity Catalog as the
`toy_demand_alloc_online` catalog, and the native OLTP write path —
`allocation.allocation_decisions` and `allocation.agent_interactions` with 7
indexes, a generated `override_delta_units` column, a proven INSERT/SELECT and
24 seeded decisions across accept / override / defer. Server-side point lookup
is 0.044 ms by `EXPLAIN ANALYZE` (the ~79 ms wall figure is laptop-to-us-east-1
RTT, not Postgres).

**What is blocked: the four synced tables.** Every attempt reaches
`SYNCED_TABLE_OFFLINE_FAILED`, and the sync pipeline's own error is:

```text
[ErrorClass=INVALID_STATE] Metastore storage root URL does not exist.
```

This metastore uses Default Storage and has no storage root, so the sync
pipeline has nowhere to write its event log. The documented remedy is to set
`new_pipeline_spec.storage_catalog` to a regular UC catalog — but on this API
version that field **does not exist**. Probing the spec shows only
`existing_pipeline_id` is accepted; `new_pipeline_spec`, `storage_catalog`,
`storage_schema` and `pipeline_spec` are all rejected as unknown fields, and a
`new_pipeline_spec` passed through the CLI is silently stored as `null`. The
synced table also pins itself to the first auto-created pipeline
(`b63df43e-b67e-47bf-ac95-e6d4abf6c4fa`), which is already broken, so recreating
does not get a fresh one.

Three ways forward, in order of preference:

1. **Get a metastore storage root set** (metastore admin, which this identity
   does not have). This fixes the root cause and makes synced tables work as
   designed — and it is the same missing storage root that forced an explicit
   `--storage-root` when this project's catalog was created.
2. **Find a newer API/CLI** that accepts a pipeline storage catalog on the
   synced-table spec, then re-run `python -m src.lakebase.sync`, which already
   builds that field.
3. **Substitute the read path**: replicate the four gold tables into Postgres
   with a small scheduled job (read via the Statement Execution API, `COPY` into
   Lakebase). This loses UC-managed sync semantics but gives the app a genuine
   low-latency read path, and would need documenting as a deliberate
   substitution the way the Stage A SQL Server path is.

Two governance findings from this stage are worth keeping regardless:

- **UC refuses to build an online copy of a table carrying a row filter or
  column mask** (`cannot have both row/column security and online materialized
  views`). That is UC closing a governance hole — an online replica sits outside
  the policy. The resolution was `gold.svc_product_lookup`, which omits the
  masked cost and margin columns entirely so there is nothing left to protect.
- Retailer scoping therefore has to move into the app's own query for the
  Lakebase read path. The authoritative governed path stays UC and Genie.

**Also outstanding for D:** the write-back of `allocation_decisions` into UC as
`gold.fct_allocation_decisions`. `databricks postgres create-cdf-config` (Beta)
is the native candidate and was not yet tried; the fallback is a scheduled job
reading Postgres and writing Delta.

**Cleaned up 2026-10-13:** `toy-demand-alloc-db`, a legacy *Provisioned*
Lakebase instance created before Provisioned was understood to be retired, has
been deleted. It was billing ~13 DBU/day with no scale-to-zero and nothing
referenced it. Stage D uses the Autoscaling project `projects/toy-demand-alloc`.

## Cost state as of 2026-10-13

Measured from `system.billing.usage` over the preceding 7 days for workspace
`7474649643770783`. Worth recording because the intuition was wrong:

| Resource | Owner | DBU/day | State |
|---|---|---|---|
| `other-engagement-forecast-agent` | pre-existing | 96 | always on |
| `other-engagement-sku-demand-forecaster` | pre-existing | 96 | always on |
| `dbxmetagen-vs` | pre-existing | 96 | always on |
| all-purpose serverless | pre-existing | 48 | flat, predates this build |
| pre-existing Lakebase (`rtm-*`) | pre-existing | ~5 | always on |
| `toy-allocation-agent` | **this build** | **0** | scale-to-zero working; 15.33 DBU total, all on 10-09 |
| Lakebase `toy-demand-alloc` | **this build** | **0** | IDLE since 10-12 |
| `toy-demand-alloc-ml` cluster | **this build** | **0** | TERMINATED; 10.49 DBU lifetime |
| SQL warehouse | shared | ~0 | STOPPED, 10-min auto-stop |

This build is roughly **4%** of the workspace's 7-day spend. Three pre-existing
always-on serving endpoints are roughly **78%** at ~288 DBU/day between them,
and they are on the brief's do-not-touch list, so they were left alone. If they
are no longer needed, enabling scale-to-zero or deleting them is the single
biggest saving available in this workspace -- about 8,600 DBU/month.

**The FEVM metastore grant (`CREATE CATALOG`) expired 2026-10-13.** Re-request
via `request_metastore_permission(use_case_id="lakebase", ...)` before any work
that needs a new catalog, including registering a fresh Lakebase online catalog.

## Remaining stages

**D — Lakebase.** New instance (`config.LAKEBASE_INSTANCE`), synced tables for
the app read path (`config.SYNCED_TABLES` already maps the four), a native OLTP
`allocation_decisions` table the app writes to, and a sync back into UC as
`gold.fct_allocation_decisions` so the decision is auditable and answerable in
Genie. Note: registering a Lakebase instance's online catalog in UC needs
`CREATE CATALOG`, which the FEVM `lakebase` metastore grant conferred — that
grant has a 5-day TTL from 2026-10-08, so it may need re-requesting.

**E — Genie.** Space over the gold tables *and the metric views*, with
instructions, column synonyms and curated example SQL for the hard joins, then
≥10 questions through the Conversation API including at least one the space got
wrong plus the instruction change that fixed it.

**F — App.** "Allocation Command Center" reading from Lakebase, with the agent
panel calling the live `toy-allocation-agent` endpoint and the accept/override
writing back to Lakebase. Invoke the `dataviz` skill before any chart code.

**Then** the deck as both `.pdf` and `.md`, `docs/TALK_TRACK.md`,
`docs/ARCHITECTURE.md`, `docs/BUSINESS_CASE.md`, `databricks.yml`, `tests/`, and
the §11 final checklist run literally.
