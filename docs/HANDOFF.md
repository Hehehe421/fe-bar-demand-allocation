# Handoff — read this first

Self-contained brief for continuing this build in a fresh session. You should not
need the previous conversations.

**Repo:** https://github.com/Hehehe421/fe-bar-demand-allocation (public). Session 2's
commits are **not pushed** — see the GH_TOKEN workaround in §3.
**Local:** `/Users/sixuan.he/Documents/SA_fy27/fe-bar-demand-allocation`
**Last session ended:** 2026-10-15 (session 2; ids in `docs/PROVENANCE.md`)

---

## 1. What this is, and the two documents that govern it

A Databricks FE Bar submission built on a real customer case: a governed
demand-forecasting and constrained-allocation loop for a licensed toy and
collectibles manufacturer. All data synthetic.

| Document | Role |
|---|---|
| `../fe-bar-allocation/BUILD_PROMPT.md` | The **FE Bar rubric**. Six required stages, evidence contract, final checklist. Outside this repo. |
| `docs/SCOPE_V2.md` | The **plan this build followed**. Re-points the narrative at the DataRobot-compete conversation while still satisfying the rubric. Supersedes the build order in BUILD_PROMPT §9. |

The narrative:

> Governed features → auto-run experiments across algorithms → champion/
> challenger in UC → **batch *and* real-time inference** → agent explains and
> recommends → planner decides, and the decision is captured and measurable.

AutoML appears once, early, as *"and yes, you can do it this way too."* Shown, not sold.

### The three customer questions every artifact must serve

| Question | Answered by |
|---|---|
| *Can this replace our incumbent AutoML platform?* | AutoML leaderboard (`03a`) **and** the hand-built sweep (`03`), same holdout |
| *Is the MLOps real?* | Feature lineage → UC model + aliases → alias-only promotion → batch **+ real-time** (verified equal) → accuracy tracked per version |
| *Where does lineage come from?* | `_source_file` on Bronze surviving into Gold → feature table → model → forecast, via `system.access.table_lineage` |

---

## 2. State: 7 of 8 items built

| Stage | State | Evidence |
|---|---|---|
| **A — Lakeflow** | Built | `evidence/01_lakeflow.md` |
| **B — Unity Catalog** | Built | `evidence/02_unity_catalog.md` |
| **C1 §A — AutoML** | Built, 18 trials | `notebooks/exports/03a_automl.md` |
| **C1 §C — sweep** | Built, 7 candidates / 6 families | `evidence/03_ml_forecast.md` |
| **C1 §D — batch + real-time** | **Built and verified** | `evidence/03_ml_forecast.md`, notebook 03 §12–13 |
| **C2 — GenAI agent** | Built; endpoint **now actually reads data** (it could not before — §6 #39–41) | `evidence/04_genai_agent.md` |
| **D — Lakebase** | Built, trimmed to 2 synced tables | `evidence/05_lakebase.md` |
| **E — Genie** | **Built**, 13/13, one intermittent error recorded | `evidence/06_genie.md` |
| **F — App** | **Built and deployed**, 10/10 smoke, decision flow verified on the deployed app | `evidence/07_app.md` |
| **Deck + docs** | **Not built — START HERE** | — |

### Headline measured numbers (all computed, none asserted)

- Baseline: **96.26%** fill rate, **86.14%** OTIF, **74,789** stockout-weeks,
  $5.51M lost revenue, $7.94M markdown, $2.12M air freight at **7.0x** ocean,
  3.06 turns on $101.5M invoiced — `evidence/metrics/kpi_baseline.csv`
- Forecast: LightGBM **≈67.4% WAPE** vs **95.34%** seasonal-naive. Across three
  executions it landed at 67.36 / 67.56 / 67.42 — multi-threaded LightGBM spreads
  ~0.2 points run to run. **Quote "about 67.4%", never two decimals.**
- **The most credible output:** EVERGREEN **20.4%** WAPE vs SOCIAL_TREND
  **109.9%**, with p90 covering only **74.1%** on trend items against a 90%
  target. Since p90 drives the risk table, exposure is *understated* on exactly
  the items that matter. Preserve this through any rebuild.
- `sklearn_hist_gbm` is **unstable** under Poisson loss: 92.99% on one run,
  138.7% on the next, same hyperparameters. Reported as measured, not tuned.
- Agent: `Correctness` 19/22 but only **74.1%** of its numbers trace to tool
  output; 5/22 answers fully grounded (earlier runs: 20/22 · 74.5% · 5/22 and
  19/22 · 73.9% · 4/22 — the spread is part of the finding). A mechanical scorer
  catches what two LLM judges miss. **The live endpoint's tool calls are now verified
  to return data** (8/8 in a direct probe; a notebook cell fails the run otherwise).
- Lakebase: 2 synced tables ONLINE matching UC row-for-row (3,016 and 58,474);
  25 decisions round-tripped to UC; 0.051 ms server-side point lookup.
- Real-time vs batch: SKU-01723 / MASS_A, horizon 1 → p50 **2,154.33**, p90
  **3,460.29** from the live endpoint, identical to `fe.score_batch`.
  Median ~179 ms from a laptop including network.
- Genie: 13/13 on the final space. q11 (forecast by brand) was answered wrongly
  3 of 10 times before a metadata fix and 0 of 13 after (Fisher p ≈ 0.07:
  suggestive, **not** conclusive — the evidence says so).

---

## 3. Live resources

**Workspace** `https://fevm-stable-classic-pqjg18.cloud.databricks.com`,
profile **`vm-summer-classic`**, workspace id `7474649643770783`.

| Resource | Id / name | Note |
|---|---|---|
| Catalog | `toy_demand_allocation` | `bronze` `silver` `gold` `ml` |
| Cluster | `1008-205814-qhbfm7tc` | DBR 16.4 LTS ML, single node, 90-min autoterminate — **restart it** |
| Warehouse | `a6857db5911f7af1` | auto-stops; **start before any SQL** |
| Pipeline | `7c2d619c-4f3e-4ca2-add3-7376bd722d93` | medallion |
| Experiments | sweep `4347060009219469` · agent `2590193212819414` · AutoML `3440760213960864` | |
| Model — forecaster (batch) | `ml.demand_forecaster` | v13 `@champion` (lightgbm), v14 `@challenger` (xgboost); feature-backed, for `fe.score_batch` |
| Model — forecaster (real-time) | `ml.demand_forecaster_rt` | v3 `@champion`, v4 `@challenger`; plain pyfunc, for serving |
| Model — agent | `ml.allocation_agent` | v8 `@champion` (versions 4–7 are superseded) |
| Endpoint — forecaster | `toy-demand-forecaster` | **READY**, serves rt v3, scale-to-zero. A *deploy* takes 20–25 min (queue + build); scale-from-zero cold start was **not measured**, so warm it before a demo |
| Endpoint — agent | `toy-allocation-agent` | **READY**, v8 at 100%. Served entities for v4–v7 are still attached at 0% (`agents.deploy` accumulates them; scale-to-zero, so free). Removing them means re-submitting the endpoint config — not done, to avoid risking a working endpoint |
| Lakebase project | `projects/toy-demand-alloc` | PG 17, Autoscaling |
| Lakebase host | `ep-empty-butterfly-d2dihd0i.database.us-east-1.cloud.databricks.com` | |
| UC online catalog | `toy_demand_alloc_online` | 2 synced tables: `stockout_risk_current`, `forecast_next_13w` |
| Genie space | `Demand & Allocation`, id `01f1c8cc66d713fba1decec949c0740c` | id also in `evidence/metrics/genie_space.json` |
| App | `allocation-command-center` — https://allocation-command-center-7474649643770783.aws.databricksapps.com | service principal `73bbee7a-df47-4bb9-b503-30e5b392657f`; source `/Workspace/Users/sixuan.he@databricks.com/apps/allocation-command-center` |
| View | `gold.v_stockout_risk` | the agent's read path for stockout risk — workaround, §6 #41 |
| Notebooks in workspace | `/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/notebooks/` | with outputs |
| Source in workspace | `.../fe-bar-demand-allocation/src/` | re-upload after editing `src/` |

**Personas are service principals**, not groups (UC resolves only account-level
principals and this workspace has no account auth). The unused `toy_*` workspace
groups were deleted on 2026-10-15.

| Persona | Application ID |
|---|---|
| `toy-planner-mass-a` | `5d9eb1c3-888e-4acc-8625-4ecedea0fea5` |
| `toy-data-scientist` | `7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8` |
| `toy-finance` | `dd70e525-c0aa-4ca5-ade2-29671715871a` |
| `toy-app-service` | `1c73204c-6b49-4db0-a860-300ba392393e` |

**Conversation ID for `docs/PROVENANCE.md`:** both sessions are already recorded.
A new session has a new id — append a column, don't replace.

### Two environment gotchas

- **The FEVM `CREATE CATALOG` grant expired 2026-10-13.** Re-request via
  `request_metastore_permission(use_case_id="lakebase", region="us-east-1", cloud_provider="aws")`
  before anything needing a new catalog. Requires explicit user confirmation first.
- **A stale `GH_TOKEN` in the environment shadows the gh keyring** and 401s on
  push. Workaround that works:

```bash
env -u GH_TOKEN -u GITHUB_TOKEN gh auth switch --user Hehehe421
env -u GH_TOKEN -u GITHUB_TOKEN git -c credential.helper='!f(){ test "$1" = get \
  && echo "username=Hehehe421" \
  && echo "password=$(env -u GH_TOKEN -u GITHUB_TOKEN gh auth token -u Hehehe421)"; };f' push
env -u GH_TOKEN -u GITHUB_TOKEN gh auth switch --user sixuan-he_data
```

---

## 4. How to run things

**Notebooks** are authored as jupytext percent-format `.py` and executed
cell-by-cell on the cluster via the Command Execution API, with output captured
verbatim into the committed `.ipynb` and auto-published to the workspace:

```bash
.venv/bin/python tools/dbx_notebook.py notebooks/03_demand_forecast.py \
    --cluster-id 1008-205814-qhbfm7tc
```

Nothing is hand-edited. A failing cell is captured as an error and halts the
run, so a committed notebook cannot show a passing cell that did not run.
**Do not hand-edit a committed `.ipynb`.** Fix the `.py`, re-run. (If you spot a
lint error *after* starting a run, kill and restart rather than editing the source
mid-run — the `.py` must match what executed.)

After editing anything under `src/`:

```bash
find src tools -name __pycache__ -type d -prune -exec rm -rf {} +   # import-dir uploads these
databricks workspace import-dir src \
  /Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation/src \
  --overwrite --profile vm-summer-classic
```

`src/config.py` is the single source of truth for every name.

**Evidence files** are generated by `tools/evidence_0N_*.py`. 03 and 06 compute their
prose from the recorded run (03 used to hardcode headline numbers and silently
contradicted a re-run — don't reintroduce that). **`evidence_03` reads
`evidence/metrics/forecast_leaderboard.csv` from the repo, but the notebook writes
it to a Volume.** After re-running notebook 03:

```bash
databricks fs cp dbfs:/Volumes/toy_demand_allocation/bronze/pipeline_meta/forecast_leaderboard.csv \
  evidence/metrics/forecast_leaderboard.csv --overwrite --profile vm-summer-classic
.venv/bin/python tools/evidence_03_ml_forecast.py
```

Forgetting this produced a stale leaderboard that looked identical to the old one.

**Genie** (`src/genie/`): `python -m src.genie.create_space [--rev N]` builds the
`serialized_space` in Python and PATCHes the space, then reads it back and compares;
`python -m src.genie.run_questions --tag X` asks all 13 questions and scores them
against reference SQL in `questions.py`; `python tools/evidence_06_genie.py` renders
the evidence. `space_definition.LATEST_REV` is the current revision.

**Commit discipline:** one commit per stage with its evidence file; never commit a
notebook with a failing cell; end messages with
`Co-authored-by: Isaac <no-reply@databricks.com>`; use `git commit --file=<path>`.

---

## 5. Next actions, in order

### 5a. Stage F — the app: DONE; what is left

Built, deployed and verified (`evidence/07_app.md`). Open items, none blocking:

- **The deployed UI was never clicked through in a browser** — workspace SSO blocked the
  automation. Open the URL once yourself and try: select a row → Ask the agent → Record
  decision. Then delete the row you create (or keep it and re-sync: the KPIs and Genie
  counts assume the 25 seeded decisions).
- **Per-user planner scoping is simulated** (a dropdown filtering the app's own query).
  Real scoping needs on-behalf-of auth mapped to a retailer.
- **Run `python app/deploy.py --grants` after any `sync.py` re-sync** — re-creating a synced
  table drops the app role's `SELECT` on it.
- The decision is not written back to UC by the app; `src/lakebase/writeback.py` does that
  as a job and was not re-run.

### 5b. Deck and docs — START HERE

`deck/allocation_deck.{md,pdf}` · `docs/TALK_TRACK.md` (business **and** technical,
90-second and 5-minute each) · `docs/ARCHITECTURE.md` · `docs/BUSINESS_CASE.md` ·
`databricks.yml` · `tests/`

This is a separately scored FE Bar domain and the only remaining item. A sharp compete story
with honest gaps beats a long one. The gaps to carry are in §2 and `evidence/`: trend-item
p90 under-coverage (74.1% vs 90%), grounding 74.1%, hist-GBM instability, Genie's
intermittent table-choice error (p ≈ 0.07), LightGBM run-to-run spread, and the agent
endpoint that shipped for a week unable to read any data (§6 #39).

### 5c. Optional

- `fe.score_batch` currently runs only for the champion into `fct_demand_forecast`.
  If the deck wants champion-vs-challenger accuracy per version, score the challenger
  into `fct_demand_forecast_eval` too (the table already carries `model_version`).
- Push: nothing from session 2 (commit `07e30ff` onward) has been pushed to GitHub.

---

## 6. Traps that already cost time — do not rediscover these

**Environment**
1. Databricks **MCP server is bound to the broken `DEFAULT` profile** (points at
   `fe-vm-vm-summer`, wrong workspace). Use the CLI/SDK with
   `profile="vm-summer-classic"`. `tools/dbsql.py` already does.
2. `UID` is **readonly in zsh** — do not use it as a shell variable. Globs that match
   nothing abort the command in zsh (`--include=*.md` in a `grep -r` did).
3. Long compound shell commands can trip the `--no-verify` guard hook. Split
   them and use `git commit --file=<path>` for long messages.
4. **The training runtime is not the serving runtime.** DBR 16.4 ML ships Python
   3.12, **pandas 1.5.3**, MLflow **2.21.3**, scikit-learn 1.4.2, lightgbm 4.5.0,
   databricks-feature-engineering 0.10.2. `importlib.metadata.version("mlflow")`
   raises on the cluster — use `mlflow.__version__`. There is no `uv` on the cluster;
   `python -m pip install uv` works.

**Model serving (all of these were hit on the forecaster)**
5. **A model logged with `fe.log_model` cannot be deployed to Model Serving here.**
   Serving reads the feature-lookup spec and tries to set up an online feature store:
   "Online feature store setup failed". This build has none, deliberately. Register a
   **plain-pyfunc twin** for serving (`ml.demand_forecaster_rt`) and keep the
   feature-backed one for `fe.score_batch`.
6. **Never register the bare estimator.** Anything built outside it (calendar columns,
   categorical levels, p90) is missing at inference. `forecasting.pyfunc_model.
   DemandForecaster` carries `build_matrix` + p50 + p90; training and serving share
   one implementation.
7. **Never pin serving dependencies from the training runtime.** `pandas==1.5.3` has
   no Python 3.12 wheel, so the image tries to compile it and the build dies in ~100 s
   (`No module named 'pkg_resources'`). Pin `pandas==2.2.3`.
8. **A failed build is reported only after a ~20–25 min queue.** Validate first:
   notebook 03 builds the model's `requirements.txt` into a fresh Python 3.12 venv
   (`uv`, `--only-binary :all:`) and compares forecasts before anything is deployed.
9. `fe.log_model(flavor=mlflow.pyfunc, model=<PythonModel>, code_paths=[...],
   pip_requirements=[...])` works. `fe.score_batch(..., result_type=
   "struct<forecast_p50:double,forecast_p90:double>")` returns both quantiles from keys.
10. A `FeatureLookup` on a table **without** a timeseries column: `lookup_key` is
    positional against the primary key, so `["sku_id","retailer_code","origin_week"]`
    maps `origin_week` onto `week_end_date`.
11. **Boosting results vary run to run** (±0.2 WAPE for LightGBM; hist-GBM can
    diverge). Hash-based sampling fixes row *membership*, not row order or threading.

**MLflow / packages (agent notebook)**
12. MLflow 3 (`mlflow.genai`, `ResponsesAgent`) needs a **notebook-scoped
    `%pip install` + `dbutils.library.restartPython()`**. A plain or `--target`
    install does not take path precedence.
13. **Pin `pyopenssl>=24.2`** with any MLflow 3 install, or `AttributeError: module
    'lib' has no attribute 'GEN_EMAIL'` surfaces far from the cause.
14. **Do not upgrade MLflow in `03a_automl`** — it breaks `databricks.automl`.
15. AutoML logs **`val_root_mean_squared_error`**, not `val_rmse`.
16. `search_model_versions` returns objects whose `.tags` / `.aliases` are **bound
    methods**. Use `registered_models.get(name, include_aliases=True)` and
    `get_model_version`. (The CLI `registered-models get` without
    `--include-aliases` showed no aliases for a model that had two.)
17. `mlflow.genai.scorers.Guidelines` **requires** a global `guidelines=` list.
18. **Do not decorate `ResponsesAgent.predict` with `@mlflow.trace`.**
19. Declare **`httpx` explicitly** in `pip_requirements` for anything serving `openai`.
20. `EndpointCoreConfigInput` requires `name`.
21. Query a ResponsesAgent endpoint on the **raw `/invocations` route** with
    `{"input": [...]}`.
22. **MLflow prompt registry is disabled org-wide.** `src/agent/prompt_store.py` is the
    UC-backed substitute. Inside Model Serving it falls back to the source-file tier.

**Unity Catalog**
23. Gold objects are **materialized views** — `ALTER MATERIALIZED VIEW`, not `ALTER TABLE`.
24. A policy UDF needs **`RETURNS <type>` and `COMMENT` before `RETURN`**.
25. **UC tag policies constrain values.** `data_product` ∈ {analytics,
    machine_learning, data_engineering}; `sensitivity` ∈ {pii, internal, public};
    `pii` ∈ {ssn, address}.
26. **The column mask hides `unit_cost_usd` / `gross_margin_pct` from every identity
    including the catalog owner.** Cost arithmetic reads `silver.product_master`. Genie
    inherits the mask and (correctly) reports the value as unavailable.
27. Metric views need **DBR 17.2+** — create/query them through the serverless warehouse.
28. **UC refuses an online copy of a table with a row filter or column mask.** Kept as a
    governance finding in `evidence/02` and `evidence/05`; the tables that needed it
    (`product_lookup`, `fill_rate_summary`) and their `svc_*` projections were dropped.

**Lakebase**
29. **Provisioned Lakebase is retired.** Use `databricks postgres` (Autoscaling).
30. Synced tables need **`new_pipeline_spec.storage_catalog`**. **The CLI rejects that
    field client-side** and stores `null`; the **API supports it**. POST to
    `/api/2.0/postgres/synced_tables` (what `src/lakebase/sync.py` does).
31. Credentials come from `w.postgres.generate_database_credential(endpoint=...)`.
32. Deleting a synced table via the API also removes its sync pipeline. The pipelines
    named `Synced table: …` are the **live** ones, not orphans.

**Pipelines**
33. SDP forbids `DataFrame.collect()` inside a decorated function — use a window.
34. Do not mix `expect_all_or_fail` with `expect_all_or_drop`.

**Genie**
35. The `serialized_space` schema is **documented** in the `genie-rooms` skill
    (`references/example-serialized-space.json`) and returned by
    `GET /api/2.0/genie/spaces/{id}?include_serialized_space=true`. The CLI `get-space`
    omits it without that query parameter. Build the payload in Python.
36. **The server concatenates `content` array entries with no separator** — two bare
    sentences came back as `synthetic.Retailer`. Give every entry its own `\n`.
37. **Genie is non-deterministic.** One pass proves nothing; q11 was wrong 3 of 10 times
    on the same space. Measure with repeats before claiming a fix worked.
38. Score Genie against reference SQL, and **audit the scorer too**: of the first run's
    3 FAILs, one was my scorer, one was my ambiguous question, and one was Genie.

---

**Agent endpoint, Apps and auth (added when building Stage F)**
39. **A deployed agent can answer fluently while every tool fails.** The endpoint runs its
    tools as its own identity, which is granted only *declared* resources. The agent was logged
    with the LLM endpoint + warehouse only, so every tool returned `INSUFFICIENT_PERMISSIONS
    ... USE CATALOG` and the model wrote a plausible refusal. It was in the committed export for
    a week. Declare every UC object the tools read (`config.AGENT_UC_TABLES`) as
    `DatabricksTable` resources, and **assert on `function_call_output`, not on the prose**
    (notebook 04's last cell and `tests/smoke_app.py` do).
40. Declared-resource access does **not** show in `SHOW GRANTS`. Do not conclude it is missing
    from an empty grants list.
41. **Observed, cause not isolated:** with all seven tables declared, the endpoint could read
    six (materialized views, a metric view, two Delta tables) but not `gold.fct_stockout_risk`
    (`does not have SELECT on Table`), though the app's service principal reads it fine. Declaring
    it first or last made no difference. The tools read `gold.v_stockout_risk` (a view) instead.
42. `system.access.audit` and `system.query.history` lag by minutes to an hour; they did not
    help diagnose a live failure. Probe the endpoint directly instead.
43. Forecaster **cold start from scale-to-zero: 52 s and 138 s** (two measurements). The app
    pings it at start-up (`WARMUP=1`) and `tests/smoke_app.py` warms it first.
44. Apps runtime is **Python 3.11** and serves on `DATABRICKS_APP_PORT` (it was 8000, not 8080);
    read the env var. `psycopg` is not pre-installed — put it in `requirements.txt`.
45. `AppResourcePostgres` (branch + database + `CAN_CONNECT_AND_CREATE`) creates the app's
    Postgres role, but **grants on tables are yours to apply** (`app/deploy.py` does). The role
    name is the app's service-principal client id.
46. The Apps URL needs workspace SSO. Browser automation hits the login page; drive the
    callbacks over HTTP with `databricks auth token --profile vm-summer-classic` instead.
47. **The CLI profile's refresh token expires mid-session** ("refresh token is invalid") while
    a cached access token keeps some calls working for up to an hour — a confusing half-failure.
    `databricks auth login --profile vm-summer-classic` fixes it and completed without a click.
48. `chrome-devtools` `wait_for` returns the whole page snapshot; with a 250-row table it blew
    the output limit. The worklist is paginated for this reason too.

## 7. Cost — check before and after

Last measured 2026-10-13 from `system.billing.usage`: **this build is ~4%** of the
workspace's 7-day spend. Nothing added since is always-on: both serving endpoints scale
to zero, Lakebase idles, the cluster terminates, Genie uses the auto-stopping warehouse.

**Three pre-existing always-on serving endpoints belonging to other engagements are
~78%** at roughly 288 DBU/day between them — about 8,600 DBU/month. They are on the
brief's do-not-touch list and were left alone. If they are no longer needed,
scale-to-zero or deletion is the single biggest saving in this workspace. Raise it
with the owner; do not act unilaterally.

---

## 8. Things to carry into the next session

- **Honesty over coverage.** The evidence is worth something because the failures
  are in it: the 73.9% grounding result, the p90 under-coverage on trend items, the
  three stacked endpoint defects, the Genie error and the p ≈ 0.07 that does not prove
  the fix. Do not smooth those out.
- **Read the skill before probing an API.** `databricks-ml-training`,
  `databricks-model-serving`, `databricks-genie`, `genie-rooms`, `databricks-app-python`.
  The Genie schema was "unknown" for two sessions and was in a skill the whole time.
- **When a CLI says a field is unknown, check the API.**
- **Reproduce a failure before changing config.** The forecaster endpoint failed three
  different ways in sequence; each fix exposed the next, and each retry cost a 25-minute
  queue. The local/venv validation in notebook 03 exists so the next one fails in seconds.
- **Do not start a stage you cannot finish.** The repo has no stubs because of it.
