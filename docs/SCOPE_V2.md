# Scope v2 — for review before building

**Status 2026-10-15: executed through Stage E.** A, B, C1 (batch and real-time),
C2, D and E are built and evidenced; F (app) and the deck/docs remain. Decisions in
§5 are all done. The build-order table in §6 is kept as originally estimated. Read
`docs/HANDOFF.md` for current state; this file is the plan it followed.

Scope v1 built to the FE Bar rubric alone. This version keeps the rubric but
re-points the narrative at the compete conversation the customer is actually
having, and corrects two things v1 got wrong.

---

## 1. What changes, and why

| Change | Reason |
|---|---|
| **Feature View replaces Feature Table** | v1 used `fe.create_table()` + `FeatureLookup`. That is the legacy path. Same point-in-time guarantee, current API, and it stops dragging an online-store mental model into the room. |
| **AutoML built and run, but not sold** | Classical AutoML is API-only now and no longer the dominant story. It earns one notebook and one leaderboard shown *beside* the hand-built sweep — proof the capability exists, not the pitch. |
| **Multi-algorithm sweep stays the centrepiece** | It is the real answer to "can Databricks auto-run experiments across algorithms like our incumbent tool?" — and showing both it and AutoML side by side is a stronger answer than either alone. |
| **Real-time serving added for the forecaster** | v1 batch-scored only and I still described it as end-to-end. It was not. `ml.demand_forecaster` is served nowhere today. |
| **Lakebase folds into Stage F** | The rubric item is satisfied by the assets, which exist. Narrating it as its own stage makes four synced tables look like feature serving in a DataRobot-compete meeting. Inside the app it is what it actually is: decision capture. |
| **Online feature serving stays out** | Never built, and will not be. Confirmed zero feature-serving endpoints; the feature data is never served online. |

### The narrative this supports

> Governed features → auto-run experiments across algorithms → champion/
> challenger in UC → **batch *and* real-time inference** → agent explains and
> recommends → planner decides, and the decision is captured and measurable.

AutoML appears once, early, as "and yes, you can do it this way too."

---

## 2. The three customer questions, and where each is answered

The technical buyer asks three things. Scope v1 answered them only by
implication; v2 makes each one a specific artifact.

| Question | Answered by | Status |
|---|---|---|
| *"Can this replace our incumbent AutoML platform?"* | C1 §A AutoML leaderboard **and** C1 §C hand-built sweep leaderboard, on the identical holdout, in one experiment. Plus the comparison cell. | sweep REUSE, AutoML NEW |
| *"Is the MLOps real?"* | Feature View lineage → UC model with `@champion`/`@challenger` → alias-only promotion → batch job + real-time endpoint → forecast-accuracy metric view tracking per model version | partly REUSE, serving NEW |
| *"Where does lineage come from?"* | `_source_file` on the Bronze row surviving into Gold → Feature View → model → `fct_demand_forecast`, queried from `system.access.table_lineage` | REUSE |

---

## 3. Stage-by-stage, with reuse called out

### Stage A — Lakeflow · **REUSE ENTIRELY, no changes**

Serverless declarative pipeline, 6 Bronze streaming tables → 6 Silver MVs → 8
Gold MVs, 29 expectations. `gold.fct_demand_weekly` has 480,183 rows.
Evidence `01_lakeflow.md` stands.

### Stage B — Unity Catalog · **REUSE, one deletion**

Comments, tags, grants for 3 personas, row filter, column mask, 4 metric views,
lineage. Evidence `02_unity_catalog.md` stands, including the proof that the
column mask holds against the catalog owner.

- **Delete:** `gold.svc_product_lookup` / `gold.svc_fill_rate_summary`. These
  existed only to dodge "cannot have both row/column security and online
  materialized views" for the synced tables. If the app reads fewer synced
  tables, they are dead weight. *Keep the finding in the evidence — it is a good
  governance point — but drop the tables.*

### Stage C1 — Classical ML · **THE CENTREPIECE. Partly rebuild.**

Four sub-parts, in demo order:

**§A — AutoML (NEW, `notebooks/03a_automl.py`)**
Run `databricks.automl.regress` on the same training frame and holdout. Capture
its leaderboard, best trial, and the generated notebook link. One markdown cell
positioning it honestly: supported via API, no longer where the investment is,
shown because the customer asked. *Pre-flight: confirm `databricks.automl`
imports on DBR 16.4 ML — if it has been removed, this becomes a documented
"API-only, superseded" note rather than a run, and I will say so rather than
fake it.*

**§B — Feature View (REBUILD)**
Replace `fe.create_table()` + `FeatureLookup` with a **Feature View** over
`gold.fct_demand_weekly` (`DeltaTableSource`, declarative features,
`create_feature`). Keep the 35 features and the point-in-time semantics; the
training set is assembled from the view. `ml.demand_features_weekly` (287,966
rows) gets dropped.

**§C — Multi-algorithm sweep (REUSE, re-run against the Feature View)**
7 candidates, 6 families, one experiment, identical metrics. Current result:
lightgbm **67.36% WAPE** vs a 95.34% seasonal-naive baseline — 28 points / 29.4%
relative. The by-licence-family breakdown (EVERGREEN 20.5% / FILM_TIE_IN 55.6% /
SOCIAL_TREND 110.2%, p90 covering only 74.7% on trend items) is the most
credible thing in the build and must survive the rebuild.

**§D — Batch *and* real-time serving (batch REUSE, real-time NEW)**
- Batch: `gold.fct_demand_forecast`, 58,474 rows, p50/p90 — **done**
- Real-time: deploy `ml.demand_forecaster@champion` to a serving endpoint and
  query it live. **Not built.** This is the gap that made "end-to-end" untrue.
- Show promotion as an **alias move only** — no re-export, no redeploy.

Downstream of C1 and unaffected: `gold.fct_stockout_risk` (3,007 rows),
`gold.fct_demand_forecast_eval` (50,574), `gold.mv_forecast_accuracy`.

### Stage C2 — GenAI agent · **REUSE ENTIRELY**

MLflow `ResponsesAgent` on `databricks-claude-sonnet-4-5`, 5 real tools, traced,
22-row eval with a custom mechanical grounding scorer, registered in UC, **live
on `toy-allocation-agent`**. Evidence `04_genai_agent.md` stands — including the
honest finding that `Correctness` passes 20/22 while only **74.5%** of the
agent's numbers trace to tool output.

Note this endpoint is already a real-time serving proof point; it just is not
the *model* one.

### Stage D — Lakebase · **BUILT. Re-narrate inside Stage F, keep the evidence.**

Assets all stand: Autoscaling project, 4 synced tables ONLINE matching UC
row-for-row, native OLTP `allocation_decisions` / `agent_interactions`,
**0.051 ms** server-side point lookup, 25 decisions merged back into
`gold.fct_allocation_decisions` and joinable to the risk row that triggered each.

- `evidence/05_lakebase.md` stays as the rubric artifact
- The *story* moves into F: "the planner decides in the app, and the decision
  lands somewhere it can be measured"
- **Decided:** cut to 2 synced tables (see §5)

### Stage E — Genie · **NOT BUILT**

Space over the Gold tables *and the metric views*, with instructions, column
synonyms and curated example SQL, then ≥10 questions via the Conversation API
including one the space got wrong plus the instruction fix.

**Status 2026-10-15: BUILT.** See `evidence/06_genie.md`. The `serialized_space`
schema turned out to be documented in the `genie-rooms` skill and readable via
`GET /api/2.0/genie/spaces/{id}?include_serialized_space=true`; no UI round trip or
blind probing was needed.

### Stage F — App · **NOT BUILT. Absorbs Lakebase.**

"Allocation Command Center", reading from Lakebase, with:
1. KPI header — fill rate, OTIF, WAPE, units/revenue at risk
2. At-risk worklist — `stockout_risk_current` by severity, filtered by `risk_tier`
3. SKU detail — forecast p50/p90 vs actuals, on-hand, inbound ETA
4. Agent panel — calls the live `toy-allocation-agent`, shows the trace ID
5. **Decide** — accept/override/defer → writes to Lakebase *(this is Stage D's
   story, told where it belongs)*
6. Ask the data — embedded Genie

`dataviz` skill before any chart code.

---

## 4. Still outstanding regardless

`deck/allocation_deck.{md,pdf}` · `docs/TALK_TRACK.md` (business + technical,
90s and 5min) · `docs/ARCHITECTURE.md` · `docs/BUSINESS_CASE.md` ·
`databricks.yml` · `tests/` · `evidence/06_genie.md` · `evidence/07_app.md`

---

## 5. Decisions taken (2026-10-14)

1. **Synced tables cut to 2.** Keep `stockout_risk_current` and
   `forecast_next_13w` — the two the app actually reads. Drop
   `product_lookup` and `fill_rate_summary`, and with them
   `gold.svc_product_lookup` / `gold.svc_fill_rate_summary`, which existed only
   to dodge the row-filter/column-mask restriction on online copies. The
   governance finding stays in `evidence/02_unity_catalog.md`; the tables go.
2. **Registry tidied before rebuild.** Versions 1–8 of
   `ml.demand_forecaster` are iteration churn from my debugging, not model
   evolution, and a 10-version history is noise in front of a customer asking
   whether the MLOps is real. Clear them, then land a clean `@champion` /
   `@challenger` pair logged from the Feature View.
3. **Forecaster endpoint stays scale-to-zero, warmed before the demo.** ~0 idle
   cost, and `tests/smoke_app.py` fires a warm-up call so there is no cold-start
   pause. Declining to add a fourth always-on endpoint to a workspace where
   three pre-existing ones already burn ~288 DBU/day.

## 6. Build order once approved

| # | Work | Est. |
|---|---|---|
| 0 | Pre-flight: confirm `databricks.automl` imports on DBR 16.4 ML; confirm the Feature View API surface | 0.25h |
| 1 | C1 §A — AutoML notebook, run, leaderboard | 1.0h |
| 2 | C1 §B — Feature View migration, drop the Feature Table | 1.0h |
| 3 | C1 §C — registry tidy, sweep re-run against the view, champion/challenger | 1.0h |
| 4 | C1 §D — real-time endpoint for `@champion` + live query, batch already done | 0.75h |
| 5 | Cut synced tables to 2, drop the `svc_*` projections | 0.25h |
| 6 | Stage E — Genie space + 10-question transcript | 1.5h |
| 7 | Stage F — App, 6 panels, absorbs the Lakebase decision loop | 2.0h |
| 8 | Deck, talk track, ARCHITECTURE, BUSINESS_CASE, databricks.yml, tests | 1.5h |
| | **Total** | **~9.25h** |

Steps 1–4 are the compete story and are worth doing first regardless of how far
the rest gets.


