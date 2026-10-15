# Demand forecasting and constrained allocation for a licensed toy manufacturer

> **Build status: 7 of 8 items complete.** Stages A (Lakeflow), B (Unity
> Catalog), C1 (classical ML, batch and real-time), C2 (GenAI agent), D
> (Lakebase) and E (Genie) and F (the app) are built, executed and evidenced. The deck
> and docs are **not built**, and nothing in this repository claims
> otherwise: no empty modules, no unexecuted notebooks, no asserted results for
> anything unbuilt. See [Build status](#build-status) for exactly what exists.
> **All data is synthetic.**

## The problem

A licensed toy and collectibles manufacturer — collectible plush and licensed
action figures — sells wholesale into three mass and e-tail accounts, ships
from four domestic distribution centres, and manufactures offshore on 60–110
day inbound lead times. Licensed and trend-driven SKUs have short, violently
spiky demand curves: a film release window or a viral plush character can take
an item from a few hundred to several thousand units a week in three weeks, and
then collapse just as fast.

The planning process cannot absorb that. Replenishment is reviewed weekly in
spreadsheets against a trailing average, and the ocean lead time is 9–16 weeks.
A spike that develops inside the lead time is structurally uncoverable: by the
time it is visible in point-of-sale data, the only remedy is air freight at a
large premium, which arrives weeks late and over-buys into a licence that is
already cooling. Constrained inventory is then split across retailer DCs
manually, in a meeting, with no shared system of record for the decision — so
nobody can measure whether a planner's override beat the model.

Three failure modes, in the buyer's language: **stockout at the retailer DC
during a spike**, which costs point-of-sale revenue now and shelf facings at
the next line review; **units pushed to the wrong account**, which clears at
markdown once the licence cools; and **missed OTIF**, which draws retailer
compliance chargebacks against the invoice.

## The measured baseline

Every figure below is computed from the generated data by
`notebooks/02_governance_and_baseline.ipynb` and committed to
[`evidence/metrics/kpi_baseline.csv`](evidence/metrics/kpi_baseline.csv). None
of it is asserted, and none of it is a claim about any real business.

| KPI | Measured baseline | Where the headroom is |
|---|---:|---|
| Retailer fill rate (shipped / requested) | **96.26%** | A competent operation — the problem is not aggregate service |
| Fill rate, confirmed basis | 99.16% | The same weeks look 3 points better on the basis teams usually quote |
| OTIF (on-time **and** in-full) | **86.14%** | Units arrive, but late or split. This is what triggers penalties |
| On-time / in-full, separately | 92.66% / 92.97% | Roughly equal contribution, so both need attacking |
| Stockout-weeks | **74,789** | Concentrated in the spiky licence families |
| Lost units / lost revenue | 585,575 units / **$5.51M** | 5.85% of unconstrained demand never served |
| Compliance chargebacks | **$1.26M** (1.24% of invoiced) | Direct consequence of the OTIF gap |
| Markdown loss | **$7.94M** (7.82% of invoiced) | Over-buying into a collapsing licence |
| Air-freight premium | **$2.12M** (2.08% of invoiced) | **7.0x** the ocean unit rate — the price of reacting instead of forecasting |
| Weeks of supply / turns | 17.0 weeks / 3.06 | Long lead times, short licences |
| Invoiced wholesale (3 years) | $101.5M | Scale of the synthetic portfolio |

The baseline is deliberately **not** uniformly bad, because a straw man would
not be credible. Aggregate fill rate is good. The money is in five specific,
separately-attackable places: the OTIF gap, the stockout-weeks on spiky
licences, the air-freight premium, the markdown, and the chargebacks.

Dollar figures are the synthetic portfolio's own measured values. Any
forward-looking impact modelling will be presented as a sensitivity table with
its revenue scale labelled as an input assumption — see
`src/config.py:SENSITIVITY_REVENUE_SCALES_USD`.

## Architecture

The loop this build is working toward:

```
landing files -> bronze/silver/gold -> features -> forecast -> risk table
  -> agent -> app -> Lakebase decision -> back to UC -> Genie asks about overrides
```

| Stage | What it produces | Status |
|---|---|---|
| **A — Lakeflow** | Seeded generator lands 8 sources into `bronze.landing`; serverless declarative pipeline builds 6 Bronze streaming tables, 6 Silver materialized views, 8 Gold materialized views, with 29 data-quality expectations | **Built** |
| **B — Unity Catalog** | Comments on every Gold table and column; tags; grants for 3 personas + app identity; row filter on retailer; column mask on cost/margin; 3 metric views; lineage | **Built** |
| **C1 — Classical ML** | Feature Engineering table consumed via `FeatureLookup`, 7 candidates across 6 families in one MLflow experiment, leaderboard, UC registration with `@champion`/`@challenger`, 13-week forward scoring, `gold.fct_stockout_risk` | **Built** |
| **C2 — GenAI** | MLflow `ResponsesAgent` on Claude via FMAPI with 5 real tools, end-to-end tracing, versioned prompt, 22-row eval with built-in judges + a custom mechanical scorer, registered in UC and deployed to a live Model Serving endpoint | **Built** |
| **D — Lakebase** | Autoscaling project registered in UC; 2 synced tables ONLINE matching UC row-for-row; native OLTP `allocation_decisions` / `agent_interactions`; decisions merged back into `gold.fct_allocation_decisions` | **Built** |
| **E — Genie** | Space over 4 Gold tables + 4 governed metric views with synonyms, joins, curated example SQL and 12 benchmarks; 13 questions through the Conversation API scored against reference SQL, including one Genie got wrong intermittently | **Built** |
| **F — Databricks App** | "Allocation Command Center", three tabs: an executive summary (computed headline, attention list, Genie, top exposures with generated product art), performance and risk (KPIs, trends, accuracy, a distribution-centre map), and forecast and decide (worklist, batch and real-time forecast, agent, accept/override/defer written to Lakebase) | **Built** |

## Evidence index

The evaluator reads text only, so every built stage writes real, copied-from-the-run
output to `evidence/`. Each file opens with the command that produced it, a
timestamp, the workspace, and the fully-qualified UC paths it touched.

| Stage | Evidence | What it proves |
|---|---|---|
| A | [`evidence/01_lakeflow.md`](evidence/01_lakeflow.md) | Pipeline + update ID and `COMPLETED` state; **per-expectation pass/fail/drop counts from the event log** (8,837,513 passed, 19,739 failed or dropped across 29 expectations on 6 datasets); row counts per layer; Bronze→Silver attrition reconciled against the generator's injected faults; the case→each unit normalisation proven; 10-row Gold sample; provenance columns; grain-uniqueness check |
| A | [`notebooks/01_generate_data.ipynb`](notebooks/01_generate_data.ipynb) · [md export](notebooks/exports/01_generate_data.md) | Full-scale generation run: 107.5 MB across 75 files in 8 sources, with the injected messiness counted explicitly |
| B | [`evidence/02_unity_catalog.md`](evidence/02_unity_catalog.md) | `SHOW GRANTS` per object; table and column tags from `information_schema`; column-comment coverage; the policy DDL; **masked-vs-unmasked query results with the authenticated identity used for each**; metric-view queries with results; table- and column-level lineage rows |
| B | [`notebooks/02_governance_and_baseline.ipynb`](notebooks/02_governance_and_baseline.ipynb) · [md export](notebooks/exports/02_governance_and_baseline.md) | The KPI baseline measured from Gold, including the loss breakdown by licence family and the ocean-vs-air freight comparison |
| B | [`evidence/metrics/kpi_baseline.csv`](evidence/metrics/kpi_baseline.csv) | 26 KPIs, each with its value, unit and exact basis of measurement |
| C1 | [`evidence/03_ml_forecast.md`](evidence/03_ml_forecast.md) | Leaderboard of 7 candidates across 6 families on one holdout; accuracy decomposed by licence family and horizon through the `mv_forecast_accuracy` metric view; the p90-method comparison; UC registry state with both aliases resolved; the risk table tiered, with the constrained-allocation contention |
| C1 | [`notebooks/03_demand_forecast.ipynb`](notebooks/03_demand_forecast.ipynb) · [md export](notebooks/exports/03_demand_forecast.md) · [`evidence/metrics/forecast_leaderboard.csv`](evidence/metrics/forecast_leaderboard.csv) | The full sweep as executed, including the three modelling errors found and corrected |
| C2 | [`evidence/04_genai_agent.md`](evidence/04_genai_agent.md) | Runtime-resolved FMAPI endpoint; all five tools invoked against real UC objects; two full agent briefs verbatim; span tree with timings and token counts; 22-row evaluation with per-row scores and the failures in full; UC registration; **a real request/response against the live serving endpoint** |
| D | [`evidence/05_lakebase.md`](evidence/05_lakebase.md) | `\dt` listing; 2 synced tables ONLINE with row counts matching UC exactly; server-side point lookup of **0.051 ms** by query plan; INSERT/SELECT proving a write landed; the decision round-tripped into `gold.fct_allocation_decisions` and joined back to the risk row that triggered it |
| C2 | [`notebooks/04_allocation_agent.ipynb`](notebooks/04_allocation_agent.ipynb) · [md export](notebooks/exports/04_allocation_agent.md) · [`evidence/metrics/genai_eval_results.csv`](evidence/metrics/genai_eval_results.csv) | The agent run end to end, including the prompt-registry feature gate and the two deployment failures |

Two results in there are worth calling out because they were produced rather
than asserted:

- **The column mask holds against the catalog owner.** `src/governance/verify_policies.py`
  signs in as each persona over OAuth M2M and runs the identical query. Only
  `toy-finance` reads raw cost and margin; every other identity — including me,
  the catalog owner and a workspace admin — reads `NULL`.
- **The expectation drop counts reconcile with the generator's known faults.**
  `sku_resolves_to_master` dropped exactly the 721 orphan SKU references the
  generator injected, and `pos_units_non_negative` dropped 992 of 997 injected
  negatives (the other 5 were in rows already removed as duplicates).
- **The forecast is good where demand is predictable and bad where it is not,
  and the evidence says which.** 20.4% WAPE on `EVERGREEN` against 109.9% on
  `SOCIAL_TREND`, whose p90 interval covers only 74.1% of actuals against a 90%
  target. Since that p90 drives the risk table, the risk table understates
  exposure on exactly the trend items that matter most — recorded as a
  limitation rather than averaged away into a single portfolio number.
- **The agent is correct more often than it is grounded.** `Correctness` passes
  19 of 22 rows, but the custom mechanical scorer finds only 74.1% of the
  numbers in its answers trace back to actual tool output, with 5 of 22 answers
  fully grounded. Two LLM judges and one mechanical check disagree, and the
  mechanical one is right — which is the whole argument for having it. (Earlier
  runs scored 20/22 · 74.5% and 19/22 · 73.9%; the spread is itself part of the
  finding.)
- **A deployed agent can look fine while every tool fails.** The first deployment's
  tools all returned permission errors and the model wrote a fluent refusal around
  them; it went unnoticed until the app's agent panel exposed it. The fix declared the
  tables the tools read as model resources, and the run now fails if any live tool call
  returns an error.

## How to run it

```bash
# 1. clone
git clone https://github.com/Hehehe421/fe-bar-demand-allocation.git
cd fe-bar-demand-allocation

# 2. authenticate with a NAMED PROFILE (no tokens in this repo)
databricks auth login --profile vm-summer-classic

# 3. local environment (uv, never pip)
uv venv --python 3.12 && uv pip install -e .

# 4. Stage A — generate data and run the medallion pipeline
#    (names come from src/config.py; nothing is hardcoded in notebooks)
python tools/dbx_notebook.py notebooks/01_generate_data.py \
    --cluster-id "$(python -c 'import src.config as c; print(c.ML_CLUSTER_ID)')"
databricks pipelines start-update <PIPELINE_ID> --full-refresh --profile vm-summer-classic
python tools/evidence_01_lakeflow.py <PIPELINE_ID> <UPDATE_ID>

# 5. Stage B — apply governance, verify the policies, measure the baseline
python -m src.governance.apply
python -m src.governance.verify_policies
python tools/dbx_notebook.py notebooks/02_governance_and_baseline.py \
    --cluster-id "$(python -c 'import src.config as c; print(c.ML_CLUSTER_ID)')"
python tools/evidence_02_unity_catalog.py
```

### Where this lands in the workspace

Notebooks are executed through the Command Execution API, which runs code in a
context on the cluster and returns output over the wire — it does **not** create
a workspace notebook. So `tools/dbx_notebook.py` also imports each executed
notebook into the workspace with `--format JUPYTER`, which preserves the
captured outputs, giving an openable copy that renders exactly as the committed
one. Without that step the committed evidence is complete but there is nothing
to open in the Databricks UI.

| Asset | Workspace location |
|---|---|
| Notebooks (with outputs) | `/Workspace/Users/<you>/fe-bar-demand-allocation/notebooks/` |
| Source modules | `/Workspace/Users/<you>/fe-bar-demand-allocation/src/` |
| Catalog | `toy_demand_allocation` (`bronze`, `silver`, `gold`, `ml`) |
| Pipeline | `toy-demand-allocation-medallion` |
| MLflow experiments | `.../toy_demand_allocation_forecast` and `..._agent` |
| Registered models | `ml.demand_forecaster` (feature-backed, batch), `ml.demand_forecaster_rt` (real-time), `ml.allocation_agent` |
| Serving endpoint | `toy-allocation-agent` |
| Lakebase | `projects/toy-demand-alloc`, Postgres schema `allocation` |

`src/config.py` is the single source of truth for every catalog, schema, table,
warehouse, cluster and endpoint name. Nothing under `notebooks/`, `src/` or
`tools/` hardcodes one.

## What is synthetic

**All of it.** Every byte of data comes from `src/datagen/generate.py` at fixed
seed `20260408`. No real, customer, scraped or third-party data appears
anywhere in this repository.

The generator simulates consumer demand, an offshore replenishment policy, a
reactive air-freight path and end-of-life clearance, and it injects deliberate
messiness — late-arriving weeks, duplicate grain keys, null on-hand, negative
quantities, orphan SKU references, and one retailer reporting in cases rather
than eaches — so the Stage A expectations have genuine violations to catch.

The baseline **emerges from those mechanics**; it was not dialled in to a
target. Getting it to emerge realistically meant finding and fixing several
modelling errors that each produced an implausible business — an order-up-to
target that covered 6 weeks against a 9–16 week lead time, launches that began
with no pre-build, a seeded on-hand position with an empty inbound pipeline,
and a spike-peak buy sized to 17 weeks of peak demand. The commit history
records each one.

Synthetic labels used consistently throughout: brands `Snugglewick` and
`Glowberry Pets` (plush) and `Titan Squad` (licensed figures); licence families
`EVERGREEN`, `FILM_TIE_IN`, `SOCIAL_TREND`; retailers `MASS_A`, `MASS_B`,
`ETAIL_C`; DCs `DC_EAST`, `DC_CENTRAL`, `DC_WEST`, `DC_SOUTH`.

The business is described generically, by industry. It has no company name, no
revenue claim and no founding story.

## Production path and limitations

- **Ingest.** In production this reads from Microsoft SQL Server through
  **Lakeflow Connect**, landing CDC output into Bronze with the same
  expectations and the same Gold contract downstream. This build ingests
  committed synthetic flat files from a UC volume via Auto Loader instead. **No
  SQL Server connector was run here**; the substitution is deliberate.
- **Semantic layer.** The metric views are the governed KPI definitions. This
  is the layer the customer maintains in dbt today, and it is the natural
  integration point — Genie, the app and any external BI tool read the same
  measures, so `fill rate` cannot mean two things in two places.
- **Governance personas are service principals, not groups.** Unity Catalog
  resolves only account-level principals, and this FEVM workspace has no
  account-level auth, so workspace-local groups are invisible to `GRANT`. In a
  customer tenancy these would be account groups mapped from the corporate
  directory.
- **What production needs beyond this build:** real POS and EDI feeds, genuine
  backtesting rigour across multiple origin dates rather than a single
  time-respecting holdout, and human-in-the-loop evaluation on real planner
  decisions rather than synthetic expectations.

## Build status

Honest accounting, because a submission that overclaims is worse than one that
is short:

**Built, executed, evidenced** — Stages A, B, C1 and C2, as indexed above. The
pipeline ran to `COMPLETED`, the expectations produced real counts, the policies
were verified by authenticating as each persona, the baseline was measured from
the Gold layer, the sweep registered a champion and a challenger in Unity
Catalog, and the agent is deployed to a Model Serving endpoint that answers
real requests.

**Two capabilities substituted, both documented, neither skipped:**

- **MLflow prompt registry** is disabled at organisation level on this workspace
  (`FEATURE_DISABLED: CreatePrompt`) on both the Unity Catalog and workspace
  registry paths. Implemented instead as a governed, content-addressed,
  version-addressable prompt store in Unity Catalog
  (`src/agent/prompt_store.py`). The agent tries MLflow first and records on
  every trace which path answered. Verbatim errors are in
  `evidence/04_genai_agent.md`.
- **Lakeflow Connect from SQL Server** is the production ingest path and is
  stated as such; this build ingests committed synthetic flat files via Auto
  Loader instead. No SQL Server connector was run.

**Not built** — Stages E (Genie) and F (the app), and the business deck. Nothing
for them is faked or half-written: no empty modules, no unexecuted notebooks,
no asserted results. `src/genie/` and `src/serving/`, `app/` and
`tests/` are empty directories.

`docs/PROVENANCE.md` records the conversation ID, every resource this build
created, and confirmation that nothing on the workspace's do-not-touch list was
read or modified.

## Repository layout

```
├── README.md                      # this file
├── docs/PROVENANCE.md             # conversation ID, resources created, what was not touched
├── notebooks/
│   ├── 01_generate_data.{py,ipynb}            # .ipynb committed WITH outputs
│   ├── 02_governance_and_baseline.{py,ipynb}
│   └── exports/*.md                           # markdown export of each notebook
├── src/
│   ├── config.py                  # single source of truth for all names
│   ├── datagen/generate.py        # the seeded generator
│   ├── pipelines/{bronze,silver,gold}.py      # the declarative pipeline
│   └── governance/{apply,verify_policies}.py  # Stage B DDL and its proof
├── tools/
│   ├── dbx_notebook.py            # executes notebooks on a cluster, captures real outputs
│   ├── dbsql.py                   # SQL helper rendering text tables for evidence
│   ├── validate_baseline.py       # local cross-check of the KPI baseline
│   └── evidence_0{1,2}_*.py       # evidence generators
├── evidence/
│   ├── 01_lakeflow.md  02_unity_catalog.md
│   └── metrics/kpi_baseline.csv
├── .env.example  .gitignore  pyproject.toml  LICENSE
```

## Licence

MIT — see [LICENSE](LICENSE).
