# Allocation Command Center: see which SKUs will run out, what it is worth, and what to do about it

**A licensed toy and collectibles manufacturer loses 16.6% of its wholesale revenue every year to four supply
problems a better demand forecast can attack: sales lost to stockouts, air freight to chase spikes, markdown on
over-bought licences, and retailer chargebacks for late or short orders.** This build gives its executives one screen
that shows every SKU projected to stock out in the next 13 weeks, the revenue at stake, and a recommended split of
constrained supply. It gives its planners a worklist where every decision is recorded and can be measured.

| | |
|---|---|
| **Modelled value** | **$335K to $1.0M a year on the synthetic $33.9M portfolio (1.0% to 3.0% of revenue); $4.9M to $14.8M a year for a $500M manufacturer with the same loss profile** ([business case](docs/BUSINESS_CASE.md)) |
| **What the app shows today** | **$1.38M of revenue at risk in the next 13 weeks; 434 critical positions ($510K) that run out within two weeks**, each with a recommended allocation |
| **Forecast** | 67.4% WAPE against 95.3% for the naive forecast: **29% less error** (7 algorithms, 6 families, one holdout) |
| **Run cost** | About **$8K a year** at list price at this scale, measured from `system.billing.usage` |
| **Platform** | Replaces the incumbent AutoML tool (DataRobot) with forecasting, an agent, governance, serving and the app on one governed platform |
| **In the buyer's KPIs** (mid case, illustrative) | Sponsor: about **+2 points of revenue** to margin, markdown **−10%**, air-freight expedites **−30%**. Domain owner: OTIF **86.1% → 88.9%**, stockout-weeks **−20%**, chargebacks **−20%** ([§5 of the business case](docs/BUSINESS_CASE.md)) |

**All data is synthetic.** The company is described by industry only. Measured numbers come from the generated
data; capture rates and revenue scale are labelled assumptions ([§6 of the business case](docs/BUSINESS_CASE.md)).

---

## 1. The customer and the problem

**Industry:** Consumer Goods, toys and licensed collectibles. In the FY27 Consumer Goods Outcome Map this is
**Collaborative Supply Chain → Smarter Demand and Inventory Decisions**: *Demand & Order forecasting*, *Inventory
Optimization*, and *Out of Stocks optimization*.

**The business.** Collectible plush and licensed action figures, sold wholesale to two mass-retail accounts and one
e-tail account, shipped from four US distribution centres, manufactured offshore on 9-16 week ocean lead times.
Licensed and trend-driven SKUs have short, violently spiky demand: a film release or a viral character can take an
item from a few hundred to several thousand units a week in three weeks, then collapse just as fast.

**What goes wrong.** Replenishment is reviewed weekly in spreadsheets against a trailing average. A spike that starts
inside the lead time cannot be covered by ocean. By the time it shows in point-of-sale data, the only remedy is air freight
at 7x the ocean rate, which lands late and over-buys into a licence that is already cooling. Constrained stock is then
split across accounts in a meeting, with no system of record. **Nobody can tell whether a planner's override beat
the model.**

**Who is asking.** The executive sponsor (CFO / COO) wants revenue at risk and margin leakage in one number. The domain
owner (VP Demand Planning / Supply Chain) wants a Monday-morning queue and fewer chargebacks. The head of AI is
evaluating whether Databricks can **replace DataRobot** as the ML platform and run all AI/ML on one governed
platform, without ripping out the ERP, the dbt models or the BI tool the business already uses.

### The measured baseline

Computed from Gold by [`notebooks/02_governance_and_baseline.ipynb`](notebooks/02_governance_and_baseline.ipynb) into
[`evidence/metrics/kpi_baseline.csv`](evidence/metrics/kpi_baseline.csv) (3 years, 156 weeks). Not asserted.

| KPI | Measured baseline | What it means |
|---|---:|---|
| Fill rate (shipped / requested) | **96.26%** | Aggregate service is good; one account (MASS_A, 96.3%) is below its 97% commitment |
| Fill rate, confirmed basis | 99.16% | The basis teams usually quote looks 3 points better |
| OTIF (on time **and** in full) | **86.14%** | Units arrive, but late or split: this triggers chargebacks |
| Stockout-weeks | **74,789** | 15.6% of SKU-DC-weeks, concentrated in trend-driven licences |
| Sales lost to stockouts | **$5.51M** (5.85% of demand) | |
| Compliance chargebacks | **$1.26M** (1.24% of invoiced) | |
| Markdown loss | **$7.94M** (7.82% of invoiced) | Over-buying into a collapsing licence |
| Air-freight expedites | **$2.12M** (2.08% of invoiced), **7.0x** the ocean rate | The price of reacting instead of forecasting |
| Weeks of supply / turns | 17.0 weeks / 3.06 | |

## 2. What was built: one connected journey

```text
landing files (8 sources) ──Lakeflow──▶ bronze ─▶ silver ─▶ gold          ← Unity Catalog governs every hop
        gold ─▶ ml.demand_features_weekly ─▶ ml.demand_forecaster @champion (7 algorithms, UC registry)
             ─▶ batch: gold.fct_demand_forecast ─▶ gold.fct_stockout_risk      real-time: toy-demand-forecaster
             ─▶ GenAI agent (explains, recommends a split)  ─▶ Genie (plain-English questions)
             ─▶ Lakebase synced tables ─▶ Databricks App ─▶ planner decision written to Lakebase
             ─▶ write-back ─▶ gold.fct_allocation_decisions ─▶ Genie: "which overrides beat the model?"
```

Lineage for every hop, generated from system tables, the model registry and the synced-table API:
[`evidence/02b_lineage.md`](evidence/02b_lineage.md).

| FE Bar layer | What it is here | Evidence |
|---|---|---|
| **Lakeflow** | Serverless declarative pipeline: 6 Bronze streaming tables, 6 Silver and 8 Gold materialized views, **29 data-quality expectations** that catch the generator's injected faults (8.8M passed, 19,739 failed or dropped) | [`01_lakeflow.md`](evidence/01_lakeflow.md) |
| **Unity Catalog** | Comments and tags on every Gold object; grants for 3 personas and the app; **row filter** by retailer; **column mask** on cost and margin (proven against the catalog owner); 4 governed **metric views** shared by Genie and the app | [`02_unity_catalog.md`](evidence/02_unity_catalog.md), [`02b_lineage.md`](evidence/02b_lineage.md) |
| **ML** | Feature Engineering table; **7 candidates across 6 families** in one MLflow experiment, plus a Databricks AutoML run (18 trials); champion and challenger in the UC registry; **batch** scoring with `fe.score_batch` and a **real-time** endpoint, proven identical | [`03_ml_forecast.md`](evidence/03_ml_forecast.md) |
| **GenAI** | MLflow `ResponsesAgent` on Claude via the Foundation Model API with 5 tools over governed tables and a constrained allocator; traced; 22-row evaluation with built-in judges **and** a mechanical grounding scorer; deployed to Model Serving | [`04_genai_agent.md`](evidence/04_genai_agent.md) |
| **Lakebase** | 4 synced tables (risk, forecast, product lookup, fill-rate summary), UC = Postgres row for row, all read by the app; OLTP tables for decisions and agent calls; decisions merged back to Unity Catalog | [`05_lakebase.md`](evidence/05_lakebase.md) |
| **Genie** | Space over 9 Gold tables and 4 metric views with business and KPI instructions, synonyms, 8 example queries and 7 joins; **26 questions scored against reference SQL, 3 passes, 78/78**; one earlier failure kept with its measured fix | [`06_genie.md`](evidence/06_genie.md) |
| **Databricks App** | "Allocation Command Center": KPI header from Lakebase with week-over-week deltas; executive summary; performance and a DC risk map; forecast and decide (worklist, demand history, inbound ETAs, real-time check, agent, accept / override / defer written to Lakebase) | [`07_app.md`](evidence/07_app.md) |

## 3. Decisions and trade-offs

### When to score in batch, and when in real time

Both paths are built and serve the same model. They agree to the unit: max |p50 difference| = 0.000 over 13 weeks
(`tests/smoke_app.py`).

| | **Batch** (`fe.score_batch` → `gold.fct_demand_forecast`) | **Real time** (`toy-demand-forecaster` endpoint) |
|---|---|---|
| Use it when | The decision cadence is no faster than the data: weekly replenishment, the whole portfolio, every account | One decision needs fresh inputs within the session: a mid-week spike, a what-if, an app calling per request |
| Here | 4,498 SKU × account series × 13 weeks = 58,474 rows a run, feeding the risk table, Lakebase and Genie | The **Re-forecast now** button on one position |
| Cost | A job that runs and stops | Scale-to-zero, so it is cheap at rest. **The cost is cold start: 52 s and 138 s measured.** Keeping one warm 24/7 costs about 100 DBU a day (measured on comparable always-on endpoints in this workspace) |
| Lineage and features | Feature lookup joined by Feature Engineering; lineage native | The feature-backed model cannot be served without an online store, so serving uses a plain-pyfunc twin (`ml.demand_forecaster_rt`) registered from the same run. **Trade-off: two registered models to keep in step, guarded by the parity test, instead of running an online feature store** |
| Default | **Yes.** Planning is weekly | Only where a person is waiting for an answer |

### Why Databricks rather than DataRobot

DataRobot is a strong tool: guided AutoML, automatic feature engineering and model monitoring, approachable for
analysts. The question is not which one has AutoML. It is **where the model lives relative to the data and the
decision**.

| | DataRobot (incumbent) | Databricks (this build) |
|---|---|---|
| Multi-algorithm experiments | Core strength | 7 algorithms across 6 families on one holdout, plus AutoML (18 trials), all in MLflow ([`03`](evidence/03_ml_forecast.md)) |
| Where the model lives | A separate platform: data is copied or connected out to it, with its own registry and permissions | **Inside Unity Catalog**, with the same grants, row filter, column mask and lineage as the data. Lineage runs from the source file to the model to the planner's decision ([`02b`](evidence/02b_lineage.md)) |
| The rest of the loop | Covers the modelling and MLOps slice; pipelines, an operational store, natural-language query and the app are other tools to integrate | Lakeflow, Lakebase, Genie, the agent and the app are on the same platform, under the same governance |
| Objective | A broad menu of optimisation metrics | **Any objective, in code.** An honest finding: Databricks AutoML offered only RMSE here, which is wrong for overdispersed count demand, so the champion came from the code-first sweep scored on WAPE and pinball loss. It is shown, not hidden ([`03a` export](notebooks/exports/03a_automl.md)) |
| Lock-in | Models run best in its own runtime; export exists but is product-specific | Open: MLflow, LightGBM, standard pyfunc |
| Cost | A second licence | A platform already in use; this build costs about $8K a year to run at this scale. Retiring the licence is upside the customer quantifies |

**Where DataRobot would still win:** a team with no engineers who want a purely point-and-click AutoML. The migration
also has real costs: retraining, re-pointing consumers, and re-validating models. Those belong in the plan, not in a footnote.

### Other decisions

| Decision | Why | What it cost |
|---|---|---|
| Lakebase reads `gold.svc_*` projections, not the governed tables | Unity Catalog refuses an online copy of a table with a row filter or column mask; syncing it would leak the masked column | Account scoping for the Lakebase path moves into the app's query |
| Executive narrative is computed by templates, not written by an LLM | The agent's numbers trace to tool output only 74% of the time; a wrong sentence in front of a CFO is worse than none | Less fluent prose |
| Agent recommends, a person decides, and the decision is stored with the agent's trace id | Planner overrides become measurable against the model | A decision step that has to be designed and tested |
| Genie is curated (13 objects, metric views preferred), not pointed at the whole catalog | Fewer wrong-table answers; the q11 failure came from a misleading column, fixed in metadata | Questions outside the curated set need a curation change |
| Exact-key feature lookup, not an as-of join | Features are computed at the forecast origin and the label taken from origin + h, so leakage is prevented in the frame | No timeseries `FeatureLookup` demonstrated |

## 4. How AI was used to build this

Built with **Claude Code** as a pair-programmer across two sessions. The conversation IDs are in
[`docs/PROVENANCE.md`](docs/PROVENANCE.md).

* **Spec first.** A written build prompt and scope (customer problem, KPIs, stage-by-stage evidence required) came before any
  code. The scope was rewritten once (v2) when the real conversation turned out to be a DataRobot comparison.
* **Skills as guard-rails.** Databricks skills for pipelines, Unity Catalog, ML training and serving, Lakebase, Genie and
  Apps; MLflow evaluation; a data-visualisation skill whose palette validator was run on the app's colours.
* **Evidence is generated, never typed.** Every number in `evidence/` and in the business case is written by a script
  from a live query or a committed CSV (`tools/evidence_*.py`, `tools/value_model.py`). Re-running regenerates it.
* **The AI checks the AI.** Genie answers are scored against reference SQL, not read and nodded at. The agent has a
  mechanical grounding scorer beside two LLM judges, and it shows the judges are too generous. The deployed app is tested
  over HTTP, not only in unit tests.
* **What the AI got wrong, and how it was caught** (kept in the record on purpose): it claimed two leaderboards were
  identical after comparing a stale file; it shipped a vacuous unit test (`... or True`); a deployed agent could not
  read any data for a week while writing fluent answers; an evidence file claimed a point-in-time join that the code
  does not do; a loose regex audit reported app features that were not there. Each was found by a check, not by luck.

## 5. Evidence index

The evaluator reads text only, so every stage writes real output from the run to `evidence/`, with the workspace,
timestamp and Unity Catalog paths.

| Stage | Evidence | What it proves |
|---|---|---|
| Lakeflow | [`evidence/01_lakeflow.md`](evidence/01_lakeflow.md) | Pipeline and update id, `COMPLETED`; per-expectation pass / fail / drop counts from the event log; row counts per layer; attrition reconciled against the generator's injected faults |
| Data | [`notebooks/01_generate_data.ipynb`](notebooks/01_generate_data.ipynb) · [md](notebooks/exports/01_generate_data.md) | 107.5 MB across 75 files in 8 sources, with the injected messiness counted |
| Unity Catalog | [`evidence/02_unity_catalog.md`](evidence/02_unity_catalog.md) | `SHOW GRANTS`; tags; comment coverage; policy DDL; **masked vs unmasked results per authenticated identity**; metric views |
| Lineage | [`evidence/02b_lineage.md`](evidence/02b_lineage.md) | File → bronze → silver → gold → features → model → forecast → risk → Lakebase (row parity) → write-back |
| Baseline | [`notebooks/02_governance_and_baseline.ipynb`](notebooks/02_governance_and_baseline.ipynb) · [`kpi_baseline.csv`](evidence/metrics/kpi_baseline.csv) | 26 KPIs, each with value, unit and basis |
| Value | [`docs/BUSINESS_CASE.md`](docs/BUSINESS_CASE.md) · [`value_model.csv`](evidence/metrics/value_model.csv) · [`kpi_impact.csv`](evidence/metrics/kpi_impact.csv) | Value by lever with named assumptions, the same mid case in each stakeholder's KPIs, sensitivity to revenue scale, run cost |
| ML | [`evidence/03_ml_forecast.md`](evidence/03_ml_forecast.md) · [notebook 03](notebooks/03_demand_forecast.ipynb) · [AutoML 03a](notebooks/03a_automl.ipynb) · [`forecast_leaderboard.csv`](evidence/metrics/forecast_leaderboard.csv) | Leaderboard; accuracy by licence family and horizon; p90 method; registry aliases; batch and real-time serving |
| GenAI | [`evidence/04_genai_agent.md`](evidence/04_genai_agent.md) · [notebook 04](notebooks/04_allocation_agent.ipynb) · [`genai_eval_results.csv`](evidence/metrics/genai_eval_results.csv) | Tools against real UC objects; full briefs; span tree; per-row evaluation with failures; a live endpoint request |
| Lakebase | [`evidence/05_lakebase.md`](evidence/05_lakebase.md) | Synced tables online with UC parity; 0.051 ms point lookup; INSERT / SELECT; the decision round-tripped to UC |
| Genie | [`evidence/06_genie.md`](evidence/06_genie.md) | The stored space; 26 questions with Genie's SQL, rows and a data-based verdict, 3 passes; the q11 failure and its measured fix |
| App | [`evidence/07_app.md`](evidence/07_app.md) · [`smoke_deployed.txt`](evidence/metrics/smoke_deployed.txt) · [`app_log_session.txt`](evidence/metrics/app_log_session.txt) | URL, `app.yaml`, resource bindings, a real-session log; 14 unit tests, 20 backend checks, **9 HTTP checks against the deployed app** with pre / post row counts |

Results worth reading because they were measured rather than asserted:

- **The column mask holds against the catalog owner.** Only the finance persona reads cost and margin; every other identity,
  including the owner and a workspace admin, reads `NULL`. Genie and the agent inherit it.
- **The forecast is good where demand is predictable and weak where it is not.** 20.4% WAPE on `EVERGREEN`, 109.9% on
  `SOCIAL_TREND`, whose p90 covers only 74.1% of actuals against a 90% target. The risk table therefore understates exposure on
  exactly the trend items that matter most. That is recorded as a limitation and is in the business case.
- **The agent is correct more often than it is grounded.** `Correctness` passes 19 of 22, but only 74.1% of its numbers trace to
  tool output. The mechanical scorer is right and the two LLM judges are too generous.
- **A deployed agent can look fine while every tool fails.** It did, for a week, until the app exposed it. The run now fails if
  any live tool call returns an error.

## 6. How to run it

```bash
git clone https://github.com/Hehehe421/fe-bar-demand-allocation.git && cd fe-bar-demand-allocation
databricks auth login --profile vm-summer-classic          # a NAMED profile; no tokens in this repo
uv venv --python 3.12 && uv pip install -e .               # uv, never pip

# Lakeflow: generate data, run the pipeline, write evidence
python tools/dbx_notebook.py notebooks/01_generate_data.py --cluster-id "$(python -c 'import src.config as c; print(c.ML_CLUSTER_ID)')"
databricks pipelines start-update <PIPELINE_ID> --full-refresh --profile vm-summer-classic
python tools/evidence_01_lakeflow.py <PIPELINE_ID> <UPDATE_ID>

# Unity Catalog: governance, policy proof, baseline, lineage
python -m src.governance.apply && python -m src.governance.verify_policies
python tools/dbx_notebook.py notebooks/02_governance_and_baseline.py --cluster-id <ML_CLUSTER_ID>
python tools/evidence_02_unity_catalog.py && python tools/evidence_lineage.py

# ML and GenAI: notebooks 03, 03a, 04 the same way (they register and deploy the models)

# Lakebase: projections, synced tables, write-back
python -m src.lakebase.serving_tables && python -m src.lakebase.sync && python -m src.lakebase.writeback

# Genie: create the space, score it
python -m src.genie.create_space && python -m src.genie.run_questions --tag rev3 && python tools/evidence_06_genie.py

# App: deploy, then test it
python app/deploy.py
python tests/test_app_pure.py && python tests/smoke_app.py && python tests/smoke_deployed.py

# Business case
python tools/value_model.py
```

`src/config.py` is the single source of truth for every catalog, schema, table, warehouse, cluster and endpoint name.
A reviewable copy of the notebooks (with outputs) and evidence is published to the workspace by
`tools/publish_workspace.py`.

## 7. Production path and limitations

- **Ingest.** Production reads the ERP on SQL Server through **Lakeflow Connect** into Bronze, with the same expectations
  and Gold contract. This build ingests synthetic files from a UC volume via Auto Loader; **no SQL Server connector was run.**
- **Semantic layer.** The metric views are the governed KPI definitions and the natural home for the dbt models the customer
  maintains today. Genie, the app and the BI tool read the same measures, so "fill rate" cannot mean two things.
- **Personas are service principals, not groups.** This workspace has no account-level groups; a customer tenancy maps
  account groups from the directory.
- **Not yet production-grade:** backtesting across multiple origins rather than one holdout; p90 coverage on trend items
  (74% vs 90%); real planner decisions instead of 25 seeded ones; per-user scoping in the app (on-behalf-of authorization);
  a production WSGI server for the app.

## 8. What is synthetic

**All of it.** Every byte comes from `src/datagen/generate.py` at seed `20260408`. No real, customer, scraped or third-party
data is in this repository. The generator simulates demand, offshore replenishment, reactive air freight and end-of-life
clearance, and injects deliberate faults (late weeks, duplicate keys, null on-hand, negatives, orphan SKUs, one retailer
reporting in cases), so the expectations have real violations to catch. The baseline **emerges from those mechanics**; it was
not dialled in.

Labels used throughout: brands `Snugglewick`, `Glowberry Pets` (plush) and `Titan Squad` (licensed figures); licence families
`EVERGREEN`, `FILM_TIE_IN`, `SOCIAL_TREND`; accounts `MASS_A`, `MASS_B`, `ETAIL_C`; DCs `DC_EAST`, `DC_CENTRAL`, `DC_WEST`,
`DC_SOUTH`.

## 9. Repository layout

```text
├── README.md                     this file: outcome first
├── docs/
│   ├── BUSINESS_CASE.md          generated value model
│   ├── PROVENANCE.md             conversation ids, resources created, what was not touched
│   ├── HANDOFF.md  SCOPE_V2.md  RUBRIC_AUDIT.md
├── notebooks/                    01 data · 02 governance + baseline · 03 forecast · 03a AutoML · 04 agent (.ipynb WITH outputs, + exports/*.md)
├── src/
│   ├── config.py                 every name, in one place
│   ├── datagen/                  the seeded generator
│   ├── pipelines/                bronze, silver, gold (Lakeflow declarative)
│   ├── governance/               grants, tags, row filter, column mask, metric views, and their proof
│   ├── forecasting/  serving/    the forecaster pyfunc and its real-time deployment
│   ├── agent/                    ResponsesAgent, tools, allocator, prompt store
│   ├── lakebase/                 provisioning, projections, synced tables, write-back
│   └── genie/                    space definition, questions, scoring
├── app/                          the Databricks App (Dash): app, backend, ui, narrative, art, deploy
├── tests/                        unit tests, backend smoke test, deployed-app HTTP smoke test
├── tools/                        evidence generators, value model, notebook runner, workspace publisher
└── evidence/                     one file per stage, metrics/ (CSV, JSON, logs), screens/ (bonus only)
```

MIT licence — see [LICENSE](LICENSE).
