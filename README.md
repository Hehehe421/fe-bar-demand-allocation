# Demand forecasting and constrained allocation for a licensed toy manufacturer

> **Build status: 2 of 6 stages complete.** Stages A (Lakeflow) and B (Unity
> Catalog) are built, executed and evidenced. Stages C1, C2, D, E and F are
> **not yet built** and nothing in this repository claims otherwise — there are
> no placeholder modules, no empty notebooks and no asserted results for
> unbuilt stages. See [Build status](#build-status) for exactly what exists.
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
| **C1 — Classical ML** | Feature engineering, ≥5 algorithms across ≥3 families in one MLflow experiment, leaderboard, UC registration with `@champion`/`@challenger`, 13-week batch scoring, `gold.fct_stockout_risk` | Not built |
| **C2 — GenAI** | MLflow `ResponsesAgent` on Claude via FMAPI with 5 real tools, tracing, prompt registry, eval with built-in + custom scorers, Model Serving endpoint | Not built |
| **D — Lakebase** | New instance, synced tables for the app read path, native OLTP `allocation_decisions` table, sync back into UC | Not built |
| **E — Genie** | Space over Gold + metric views with instructions, synonyms and curated example SQL; ≥10 questions via the Conversation API | Not built |
| **F — Databricks App** | "Allocation Command Center": KPI header, at-risk worklist, SKU detail, agent panel, accept/override writing to Lakebase, embedded Genie | Not built |

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

**Built, executed, evidenced** — Stage A and Stage B, as indexed above. The
pipeline ran to `COMPLETED`, the expectations produced real counts, the
policies were verified by authenticating as each persona, and the baseline was
measured from the Gold layer.

**Not built** — Stages C1, C2, D, E and F, and the business deck. There are no
stub modules, empty notebooks, skeleton dashboards or asserted results for any
of them. `src/features/`, `src/agent/`, `src/genie/`, `src/lakebase/`,
`src/serving/`, `app/` and `tests/` are empty directories, and
`gold.mv_forecast_accuracy` is deliberately deferred to Stage C1 because its
source table does not exist yet — recorded as `DEFERRED` in the Stage B run
output rather than silently skipped.

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
