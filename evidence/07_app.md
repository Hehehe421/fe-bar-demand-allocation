# Evidence 07 — Stage F: the Allocation Command Center

| | |
|---|---|
| Produced by | `app/{app,backend,ui,narrative,art,settings,deploy}.py`, `tests/{smoke_app,test_app_pure}.py` |
| App | `allocation-command-center` on Databricks Apps (Dash, Python 3.11 runtime) |
| URL | https://allocation-command-center-7474649643770783.aws.databricksapps.com |
| Runs as | its own service principal, `73bbee7a-df47-4bb9-b503-30e5b392657f` |
| Genie | https://fevm-stable-classic-pqjg18.cloud.databricks.com/genie/rooms/01f1c8cc66d713fba1decec949c0740c |

Built for two audiences who use it every day: executives, who need the answer in one screen, and
operations managers, who need to act on it.

## 1. The three tabs

### Executive summary

![Executive summary](screens/app_executive.png)

* A **computed headline** and four hero metrics (revenue at risk, critical positions, earliest
  stockout, decision coverage).
* **What needs your attention**: ordered, plain-language findings, each with an icon and a label.
  Every sentence is a template filled with numbers just read from Lakebase or the metric views
  (`app/narrative.py`), so it cannot drift from the dashboards or invent a figure. No language
  model writes them.
* **Ask the data, in plain English**: six suggested questions plus free text, answered by Genie with
  the SQL shown. All six suggested questions were scored against reference SQL before being offered
  (`evidence/06_genie.md`, 15 questions).
* **Biggest exposures**: the five largest positions with generated product art, a status chip, and a
  **Review →** button that opens that exact SKU in the decision workspace.

### Performance & risk

![Performance and risk](screens/app_performance.png)

KPI cards with status chips; a 52-week fill-rate and OTIF trend; delivered fill rate against each
account's contractual commitment (dumbbell); forecast accuracy by licence family with the least
accurate highlighted; revenue at risk by tier in the reserved status colours; **a map of where the
at-risk demand is served from**, with a matching table; and the already-incurred business cost
(Stage B baseline).

### Forecast & decide

![Forecast and decide](screens/app_decide.png)

The ranked worklist (filter by tier and licence family, sort, **export CSV**), a SKU card with its
image and facts, the 13-week p50/p90 forecast with the stockout week marked, the **real-time check**
(live endpoint against the batch forecast), the **agent**, the **decision form**, and the decision log.

## 2. Design decisions

| Decision | Why |
|---|---|
| Headline and insights are computed, not generated | A wrong sentence in front of a C-level is worse than a missing one. The agent scored ~74% on numeric grounding; a template scores 100% by construction. Unit-tested. |
| Fill-rate status checks **every account**, not an average | A first version said "At or above commitment" on the blended 96.26% while MASS_A (96.3%) was below its 97% commitment. An averaged commitment is meaningless to a retailer. |
| Status = colour **and** icon **and** label | Never colour alone. Tier colours are the reserved status steps; the two series colours were validated with `validate_palette.js` (adjacent CVD dE 24.7, normal-vision dE 33.6). |
| Honest KPI wording | The high-case coverage card shows 89.9% "On target" *and* the 74.1% on trend-driven items beside it, because that is where the exposure is understated. |
| Map: attribution from the demand fact's own DC grain | Each at-risk position is spread over the DCs that served it in the last 13 weeks, in proportion to unconstrained demand. **The four DC values sum to $1,381,392, exactly the total revenue at risk** (tested), so the method neither creates nor loses revenue. DC coordinates are illustrative regional hubs. |
| Product art generated from SKU attributes | Every SKU gets a consistent picture at zero storage cost. See §3. |
| Tab content loads lazily and is cached; start-up warms caches | The first Performance load runs warehouse queries; the cache means a user pays that once. |
| Production touches | `/healthz`, live service-status dots (Lakebase, warehouse, agent, forecaster) including "scaled to zero", a Refresh button, CSV export, print stylesheet, loading states, an error banner per panel instead of a blank, and graceful degradation when the warehouse is unavailable. |

## 3. Generated SKU art

![Generated SKU art](screens/sku_art_sheet.png)

`app/art.py` renders an SVG as a pure function of the SKU's attributes: **category** decides the toy
(plush animal or action figure), **brand** the palette, **sku_id** the variant (bear, bunny, cat, pup;
robot, hero, knight), **licence family** the badge (star, sparkle, heart), **lifecycle** a ribbon
(NEW, SALE, LAST), and the source name's variant word a presentation (Deluxe crown, Mini size, Set or
Pack as two toys). It is illustrative, not photography; production would swap `sku_svg` for the real
asset URL.

**A data-quality finding shaped this.** The synthetic catalog's product names are independent of
their category: "Titan Squad Plushie 147" is an `ACTION_FIGURE`, and about half of all names
disagree with their category. Using the name to pick the picture would have shown a robot beside the
caption "Plushie". The art follows `category`, and the on-screen caption is derived from `category`
too ("Titan Squad Action Figure #147"); the source name stays in the image tooltip. A unit test pins
this.

## 4. Verification

### 4a. Unit tests — 13 of 13 (`python tests/test_app_pure.py`, no network)

The narrative (headline, totals, partial and empty cases, which insights fire and which must not),
the KPI status rules including the every-account fill check, and the art (valid XML, deterministic,
follows category, caption agrees with picture, badges and ribbons, unknown brand still renders).

### 4b. Live smoke test — 16 of 16 (`python tests/smoke_app.py`)

```text
PASS  warm-up: real-time forecaster          forecaster answered in 0.2s
PASS  lakebase: ranked worklist              250 rows, top SKU-01723/MASS_A
PASS  lakebase: account scoping              997 rows
PASS  lakebase: 13-week forecast             13 weeks, model v13
PASS  lakebase: totals                       3,016 rows, $1,381,392 at risk
PASS  warehouse: governed KPIs               fill 96.26 otif 86.14 wape 67.4 p90 89.9 (trend 74.1)
PASS  executive aggregates                   434 critical, $1,381,392 at risk, top SKU-01815
PASS  performance trends                     52 weeks, fill 95.8% otif 84.1%
PASS  DC exposure conserves revenue          4 DCs sum to $1,381,392 vs total $1,381,392
PASS  catalog attributes for art             Titan Squad Plushie 147
PASS  service status                         Lakebase=ok, Warehouse=ok, Agent=idle, Forecaster=ok
PASS  business baseline snapshot             26 baseline KPIs
PASS  lakebase: decision write (rollback)    insert ok (delta 2), rolled back, 25 rows remain
PASS  real-time forecast == batch            max |p50 diff| = 0.000 units over 13 weeks
PASS  agent: tools return data               [..., recommend_allocation], recommends 2796 units for MASS_A
PASS  genie: answers with SQL                [['SOCIAL_TREND', '579003.64']]
```

### 4c. The deployed app, as its own service principal

Driven over HTTP with an OAuth token, calling the same Dash callbacks the page fires:

| Callback | Result |
|---|---|
| Executive | headline, 6 insights, **5 embedded product images**, as-of "Forecast origin 26 Sep 2026 · model v13 (lightgbm)" |
| Performance | 4 KPI cards (fill rate: "Below commitment: MASS_A"), trend, map data, DC table, baseline tiles |
| Genie chip | "What is the revenue at risk by risk tier?" answered with SQL: MEDIUM $556,576 · CRITICAL $509,958 · HIGH $216,010 · LOW $98,848 |
| Agent | answered from tool output: MASS_A needs 3,460, 2,796 available, 664 unmet |
| **Decide** | decision #32 recorded (accept, 2,796 units) through the service principal's `INSERT` grant |

The decision carried agent request id `02877b0b-ee28-4986-930f-4591422e8c8b`, so it traces back to
the brief the planner read, the model version in force (13) and the severity at decision time.
The test decision and its agent-interaction row were then deleted by id (counts back to **25
decisions, 0 interactions**) so the seeded 25 that Genie and the KPIs report are unchanged.

### 4d. In the browser (local run of the same code against the live workspace)

Clicking a **Review →** card switched to *Forecast & decide* with that SKU selected; a Genie chip
returned "ETAIL_C 147, MASS_B 147, MASS_A 140" (which sums to the 434 critical positions) in 14 s.

## 5. What went wrong on the way, kept in

* **The deployed agent had never been able to read any data.** Its tools run as the serving
  endpoint's own identity, which only receives access to *declared* resources; the agent was logged
  without the tables, so every tool call returned `INSUFFICIENT_PERMISSIONS` and the model wrote a
  fluent refusal around it. Fixed by declaring the tables, plus a notebook cell that fails if any
  live tool call returns an error, and a red banner in the app. One table
  (`gold.fct_stockout_risk`) stayed unreadable to the endpoint although declared; the cause was not
  isolated, and the agent reads it through the view `gold.v_stockout_risk` (a workaround).
* **A KPI chip contradicted the executive page.** "At or above commitment" next to MASS_A being
  below its commitment (see §2). Caught by looking at the rendered page, then fixed and unit-tested.
* **The first map projection rendered blank.** Explicit longitude/latitude ranges and mercator
  variants produced empty axes in this plotly build; the default Albers USA works.
* **Bootstrap's `.lead` class** silently enlarged the insight headings; renamed.
* **A stale selection.** After "Review →", the worklist radio still pointed at row 1 while the panel
  showed another SKU; the radio is now cleared.
* **Cold starts.** Forecaster from zero: 52 s and 138 s. **Agent from zero: 217 s.** The app wakes
  both at start-up and the UI says so.
* **My own test bugs:** a smoke check read the wrong key (`revenue` vs `rev`); a unit test had a
  vacuous `... or True` assertion. Both were found and replaced with real checks.

## 6. Limits, stated

* **The deployed UI was not clicked through in a browser** (workspace SSO blocks the automation);
  it was verified over HTTP, and the screenshots come from the same code running locally against
  the same live workspace.
* **Product images are generated illustrations**, and DC locations are illustrative hubs.
* **The business-cost tiles are a dated snapshot** (Stage B, measured 2026-10-08, shipped with the
  app), not a live query.
* **Planner scoping is simulated** (a dropdown filtering the app's own query); real per-user scoping
  needs on-behalf-of authorization mapped to a retailer.
* **Decision coverage reads 6%** because the 25 seeded decisions are the only ones; that is the
  data, not a defect, but it will look alarming to a first-time viewer.
* **The app does not write decisions back to Unity Catalog**; the existing writeback job does, and it
  was not re-run.
* **Light theme only; Flask development server** under Dash. Grants on the synced tables are lost
  when `sync.py` re-creates them: re-run `python app/deploy.py --grants`.
