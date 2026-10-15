# Audit against the FE Bar rubric (deck excluded)

Audited 2026-10-15 against `fe-bar-allocation/BUILD_PROMPT.md`. Method: literal greps over every
tracked file and over all git history; a fresh `git clone` of the public repo; content checks of each
evidence file against the "Evidence →" line for its stage; and live checks of the workspace. Where a
regex was too loose it is called out. The deck, `docs/TALK_TRACK.md`, `ARCHITECTURE.md` and
`BUSINESS_CASE.md` are excluded by request (built last).

## 1. The final checklist (§11), line by line

| # | Line | Result | Note |
|---|---|---|---|
| 1 | All six stages built, each consuming the previous | **PASS** | landing → medallion → features → forecast → risk → agent → app → Lakebase → UC → Genie over `fct_allocation_decisions` |
| 2 | Notebooks committed with outputs; every stage has evidence with real output | **PASS** | every code cell has output except a Python-restart cell; headers on evidence 06 and 07 are incomplete (G11) |
| 3 | No screenshots as primary evidence | **PASS** | screenshots are in `evidence/screens/` as a bonus only |
| 4 | Zero hits for the rubric's forbidden-word list (§1.2) | **FAIL (3 hits)** | two HTML input-hint attributes in `app/app.py`, and one sentence in `docs/HANDOFF.md` (G2) |
| 5 | The customer name appears nowhere | **FAIL (history)** | absent from every current file; present in two pushed, public commits (G1) |
| 6 | No secrets or `.env` | **PASS** | scan clean; `.env` ignored; `.env.example` has empty values |
| 7 | Repo public; clean clone readable | **PASS** | cloned to `/tmp`: 32 commits, all notebooks and evidence present |
| 8 | Deck as pdf and md | excluded | |
| 9 | README opens with problem + outcome and has the evidence index | **PARTIAL** | problem and evidence index present; no outcome table with achieved/target and modeled $ (G9); "how to run" stops before `bundle deploy` (G5) |
| 10 | `TALK_TRACK.md` | excluded | |
| 11 | `PROVENANCE.md` has the conversation ID | **PASS** | both sessions |
| 12 | KPI baseline measured and committed | **PASS** | `evidence/metrics/kpi_baseline.csv`, 26 KPIs |
| 13 | Leaderboard: ≥5 algorithms, ≥3 families | **PASS** | 7 candidates, 6 families, identical metrics |
| 14 | UC model with both `@champion` and `@challenger` | **PASS** | `ml.demand_forecaster` v13 / v14 |
| 15 | Lakebase real write-back round-tripping to UC | **PASS** | 25 in Lakebase = 25 in `gold.fct_allocation_decisions`, joined to the risk rows |
| 16 | Genie transcript with a failure and its fix | **PASS** | q11, measured (3/10 before, 0/13 after, p≈0.07) |
| 17 | GenAI eval with a failing row and analysis | **PASS** | |
| 18 | Nothing in DO-NOT-TOUCH modified | **PASS** | no operation in this build targeted those assets |

## 2. Gaps, by stage (S = fix first)

| ID | Sev | Stage | Gap | Rubric text | Effort |
|---|---|---|---|---|---|
| G1 | **S** | gate | Customer name in git history (commits `73fa8cf`, `1714fca`; public). Needs a history rewrite or a fresh repo. | §1.3 "appears nowhere … or commit messages" | 15 min + a decision |
| G2 | **S** | gate | 3 hits on the forbidden-word list: the app's two input-hint attributes and a HANDOFF sentence. The attribute name is itself on the list, so the hints must be replaced by visible labels. | §1.2, §11 | 10 min |
| G3 | **S** | E Genie | Space is far thinner than specified: instructions are 2 lines with **no business description or KPI definitions**; **3** example SQL (rubric 6–8); the named synonyms (units/eaches/POS units, fill rate/service level, OTIF/on-time in-full, retailer code ↔ label) are absent; metric views are not marked as the preferred KPI source; 3 of the 4 named questions are not asked (stockouts at MASS_A in 4 weeks; fill rate last quarter vs same quarter last year; overrides vs the agent's original recommendation). The thin config was deliberate (curation first), but it contradicts the rubric's explicit setup. | §E | ~1 h |
| G4 | **S** | D Lakebase | **2 synced tables, rubric names 4** (`product_lookup` and `fill_rate_summary` were cut). This also means the app's KPI header reads the warehouse, not Lakebase. | §D, §F "not a warehouse query per page load" | ~30 min |
| G5 | **S** | repo | **No `databricks.yml`** (DAB with dev/prod targets); README "how to run" cannot reach `bundle deploy` / `bundle run`. Notebook `05_lakebase_serving.ipynb` is absent (Stage D ran as scripts). | §6, §7.5 | ~1.5 h |
| G6 | **S** | F App | Missing: **week-over-week deltas** in the KPI header; worklist **brand** filter; SKU detail **forecast vs actuals** and **inbound ETAs** (it shows p50/p90, on-hand and inbound *units*); KPI header from Lakebase. Evidence 07 lacks `app.yaml` and resource bindings, **app log output from a real session**, and a smoke-test stdout with **HTTP statuses and pre/post row counts** on `allocation_decisions` (the smoke write is rolled back, and the two committed test decisions were deleted). | §F | ~2 h |
| G7 | M | C1 | **Point-in-time lookup removed** (decided 2026-10-14). The rubric asks for a `FeatureLookup` training set "so point-in-time correctness … is demonstrable". The leakage guard is in the frame (documented), but a grader reading the rubric literally will look for it. | §C1 | ~1 h (re-run 03, 04) |
| G8 | M | B | Lineage evidence stops at Gold. The rubric wants landing → bronze → silver → gold → **model → Lakebase**; the system tables hold the rows (65 involving the feature table, forecast and online catalog). | §B | 20 min |
| G9 | M | README | No outcome table (baseline → achieved/target → modeled $ impact with a labeled sensitivity table). | §7.2 | 30 min (shares inputs with BUSINESS_CASE) |
| G10 | M | KPIs | §4 "use only these metrics, everywhere": the app shows **p90 coverage**, **air-freight $** and **lost sales $**, and omits **stockout-weeks** and **chargeback $**. p90 coverage is the honest finding but is a model metric. | §4 | 30 min or accept |
| G11 | L | evidence | Evidence 06 and 07 headers lack workspace / timestamp / UC path; evidence 02 shows tags and comments from `information_schema` rather than a literal `DESCRIBE EXTENDED`; evidence 05 prose-describes the OLTP write path instead of showing an `INSERT` + `SELECT`. | §1.1 | 30 min |
| G12 | L | docs | Now-false statements: the agent endpoint **does** load its prompt from the UC registry by version (verified live: `uc-prompt-store … /1`); HANDOFF trap 22 and parts of evidence 04 say it falls back. Good news, stale text. | | 10 min |
| G13 | opt | P1 | `tests/test_allocation_logic.py` (allocator unit tests), AI-functions notebook (`ai_parse_document`, `ai_query` vision), AI/BI dashboard. | §9 P1 | optional |
| G14 | opt | layout | Names differ from §6: no `src/features/feature_engineering.py` (features live in notebook 03), `deploy_forecaster.py` not `deploy_endpoints.py`. | §6 | cosmetic |

## 3. What is solid

Stage A (13 PDFs, 21 images, 2,400 SKUs, 156 weeks, expectation failures visible in the event log),
Stage B grants/row filter/column mask/metric views with masked-vs-unmasked per identity, the
7-model sweep with champion and challenger, the agent's tracing, per-row-scoped evaluation with a
mechanical grounding scorer that exposed the judges, the Lakebase round trip, the measured Genie
failure, and an executed-and-verified app. Failures are recorded rather than hidden.

## 4. Corrections to my own checking

Three automated "Y" results (week-over-week deltas, forecast vs actuals, inbound ETAs) matched
unrelated words such as `override_delta` and are wrong; G6 is from reading the UI. The Guidelines
scorer *is* the built-in `Guidelines` (named `output_contract`), so Stage C2's scorer requirement
is met.
