# Continuation notes

Handoff for resuming this build in a fresh session. Stages A and B are done;
C1, C2, D, E, F and the deck are not. Read `README.md` for status and
`docs/PROVENANCE.md` for every resource ID.

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

## Stage C1 — the immediate next task

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

## Remaining stages after C1

C2 (agent: 5 real tools, tracing, prompt registry, ≥20-row eval with a custom
numeric-grounding scorer, Model Serving endpoint), D (new Lakebase instance,
synced tables, native OLTP `allocation_decisions`, sync back to UC), E (Genie
space + ≥10 questions via the Conversation API, including one failure and the
instruction change that fixed it), F (the app), then the deck as both `.pdf`
and `.md`, `docs/TALK_TRACK.md`, `docs/ARCHITECTURE.md`,
`docs/BUSINESS_CASE.md`, `databricks.yml`, and the §11 final checklist.

Also still owed: `tests/`, and the `evidence/metrics/genai_eval_results.csv`
artifact.
