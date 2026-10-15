# Evidence 06 — Stage E: Genie

| | |
|---|---|
| Produced by | `src/genie/{space_definition,create_space,run_questions,questions}.py`, `tools/evidence_06_genie.py` |
| Generated at | 2026-10-15 21:54:29 UTC |
| Space | `Demand & Allocation` — `01f1c8cc66d713fba1decec949c0740c` |
| URL | https://fevm-stable-classic-pqjg18.cloud.databricks.com/genie/rooms/01f1c8cc66d713fba1decec949c0740c |
| Warehouse | `a6857db5911f7af1` |

## 1. What was built

A deliberately small surface: **4 Gold tables and the 4 governed metric views**, not all 20 objects in the catalog. Ratio KPIs (fill rate, OTIF, WAPE, weeks of supply) live in the metric views, so Genie is steered to `MEASURE()` by curation, not by prose.

```text
toy_demand_allocation.gold.dim_product                         6 column configs
toy_demand_allocation.gold.fct_allocation_decisions            6 column configs
toy_demand_allocation.gold.fct_demand_forecast                 4 column configs
toy_demand_allocation.gold.fct_stockout_risk                   11 column configs
toy_demand_allocation.gold.mv_fill_rate                        0 column configs
toy_demand_allocation.gold.mv_forecast_accuracy                0 column configs
toy_demand_allocation.gold.mv_otif                             0 column configs
toy_demand_allocation.gold.mv_weeks_of_supply                  0 column configs
```

Stored in the space (read back with `include_serialized_space=true`): 3 curated example queries, 3 join specs, 1 filter and 1 measure snippets, 12 benchmarks, 5 sample questions.

General instructions are **two lines of orientation**. That is intentional and follows the fix order in the `genie-rooms` skill (data → metadata → joins → example SQL → general instructions last): instructions grow only to patch a failure actually observed, below.

```text
This space answers planning questions about demand forecasts, projected stockouts, retailer service levels and planner allocation decisions for a licensed toy and collectibles manufacturer. All data is synthetic.
Retailer codes are MASS_A, MASS_B and ETAIL_C. Licence families are EVERGREEN, FILM_TIE_IN and SOCIAL_TREND.
```

**Governance carries through.** `dim_product.unit_cost_usd` and `gross_margin_pct` are masked by Unity Catalog for every identity but finance, including the catalog owner running this space. Genie inherits that: see q12.

**Two things about the write path worth knowing.** (1) The `serialized_space` schema is not in the CLI help; it is documented in the `genie-rooms` skill and returned by `GET /api/2.0/genie/spaces/{id}?include_serialized_space=true` — the earlier belief that `get-space` "does not echo it back" was wrong, it needs that query parameter. (2) The server concatenates `content` array entries with **no separator**: two sentences came back as `synthetic.Retailer`, so every entry carries its own newline. Both were found by reading the stored space back and comparing it with what was sent, not by trusting the 200 response.

## 2. How answers were scored

13 questions, each in its own Conversation-API conversation. For each, the SQL Genie generated and its rows are captured, then compared with a **reference query written before the run** (`src/genie/questions.py`): same row count, and every reference row found in Genie's rows within 1%. So a verdict is a data comparison, not a read-through.

## 3. First run (instruction revision 1): 9 of 12 flagged PASS, and what the 3 were

| question | verdict | what it turned out to be |
|---|---|---|
| By how many units in total did planners change the recommended allocat… | FAIL | **my question was ambiguous** |
| What is the total 13-week p50 forecast demand by brand?… | FAIL | **Genie was wrong** |
| What is the gross margin on SKU-01723?… | FAIL | **my scorer was wrong** |

Taking them one at a time, because three FAILs are not three Genie errors:

* **q12 (margin) — scorer bug.** Genie queried `gross_margin_pct`, got NULL, and said the value is "not available". Correct behaviour. The scorer flagged the echoed `sku_id` cell as "a value returned for a masked column". Fixed to inspect only margin/cost columns, then re-asked: PASS (`genie_run_rev1b.json`).
* **q10 (override units) — ambiguous question.** "By how many units in total did planners change the recommended allocation": Genie summed `ABS(override_delta_units)` = 46 (gross); my reference was the signed net = 14. Genie's reading is defensible; the wording was the bug. Split into q10a (gross, 46) and q10b (net, 14), both of which Genie then answered correctly with no change to the space.
* **q11 (forecast by brand) — a genuine, confident error.** See section 4.

## 4. The answer Genie got wrong, and the change that followed

**Question:** What is the total 13-week p50 forecast demand by brand?

**Genie's SQL (revision 1):**
```sql
SELECT brand, SUM(forecast_p50_13w) AS total_p50_13w_demand
FROM toy_demand_allocation.gold.fct_stockout_risk
WHERE brand IS NOT NULL
GROUP BY brand
ORDER BY total_p50_13w_demand DESC
```

```text
Genie    : ['brand', 'total_p50_13w_demand'] [['Snugglewick', '987080.0'], ['Titan Squad', '927004.0'], ['Glowberry Pets', '687237.0']]
Reference: ['brand', 'p50_units'] [['Glowberry Pets', '911952.0'], ['Snugglewick', '1285858.0'], ['Titan Squad', '1212704.0']]
```

It answered from `fct_stockout_risk.forecast_p50_13w` — a table that contains **only the SKU × retailer pairs projected to run short** — so every brand total is understated (Snugglewick 987,080 against a true 1,285,858), and the prose presented it as total demand with no caveat. A plausible number from the wrong grain is the dangerous kind of wrong.

**The change (revision 2) — metadata, not instructions**, per the skill's fix order: column descriptions on `forecast_p50_13w` / `forecast_p90_13w` saying they cover at-risk series only and that total, portfolio or by-brand demand comes from `fct_demand_forecast`. General instructions were left alone.

### Did the change fix it? Measured, because one pass proves nothing

Genie is non-deterministic, and on a later look the revision-1 space answered q11 correctly several times. So q11 was asked repeatedly on each revision:

| revision | q11 attempts | wrong | used `fct_demand_forecast` |
|---|---|---|---|
| 1 — before | 10 | **3** | 7 of 9 repeats (+ the original, which did not) |
| 2 — after | 13 | **0** | 13 of 13 |

**Honest reading:** the failure is intermittent (≈30% before) and did not recur after the change (0 of 13). One-sided Fisher exact p ≈ 0.07: suggestive, **not conclusive** — the sample is too small to claim the description *caused* the improvement. The change is the right one regardless (the column really is misleading), and the numbers are here so the claim is exactly as strong as the evidence.

## 5. Final results, revision 2 — 13 of 13

| # | question | verdict | seconds | Genie used |
|---|---|---|---|---|
| q01 | How many SKU and retailer combinations are at CRITICAL risk, and how much revenue is at risk? | PASS | 19.0 | fct_stockout_risk |
| q02 | Show the 5 highest severity at-risk SKUs for MASS_A. | PASS | 18.8 | fct_stockout_risk |
| q03 | Which licence family has the most revenue at risk? | PASS | 13.3 | fct_stockout_risk |
| q04 | How many SKUs are projected to stock out within 4 weeks? | PASS | 19.2 | fct_stockout_risk |
| q05 | What is forecast accuracy (WAPE) by licence family? | PASS | 20.2 | mv_forecast_accuracy |
| q06 | How often does the P90 forecast cover actual demand for SOCIAL_TREND items? | PASS | 13.4 | mv_forecast_accuracy |
| q07 | What is the overall fill rate for each retailer? | PASS | 13.8 | mv_fill_rate |
| q08 | Which retailer has the lowest OTIF? | PASS | 25.4 | mv_otif |
| q09 | How many allocation decisions did planners accept, override and defer? | PASS | 18.4 | fct_allocation_decisions |
| q10a | How many units did planners move in total when they overrode a recommendation, counting increases and decreases alike? | PASS | 19.1 | fct_allocation_decisions |
| q10b | What was the net unit change from planner overrides, netting increases against decreases? | PASS | 25.2 | fct_allocation_decisions |
| q11 | What is the total 13-week p50 forecast demand by brand? | PASS | 34.2 | dim_product, fct_demand_forecast |
| q12 | What is the gross margin on SKU-01723? | PASS | 25.1 | dim_product |

### Generated SQL and rows, every question

#### q01_critical_count
**How many SKU and retailer combinations are at CRITICAL risk, and how much revenue is at risk?**  — _basic filter + aggregate on the risk table_

```sql
SELECT COUNT(*) AS `sku_retailer_combinations_at_critical_risk`, SUM(`revenue_at_risk_usd`) AS `total_revenue_at_risk_usd`
FROM `toy_demand_allocation`.`gold`.`fct_stockout_risk`
WHERE `risk_tier` = 'CRITICAL'
```

```text
Genie    : ['sku_retailer_combinations_at_critical_risk', 'total_revenue_at_risk_usd'] [['434', '509957.81999999995']]
Reference: ['n', 'revenue_at_risk'] [['434', '509958.0']]
Verdict  : PASS — every reference row found, same row count
```
> There are **434** SKU-retailer combinations at **CRITICAL** risk, with **$509,957.82** in total revenue at risk. Based on this result, the average revenue at risk is about **$1,174.10 per SKU-retailer combination**.

#### q02_top_mass_a
**Show the 5 highest severity at-risk SKUs for MASS_A.**  — _'highest severity' must map to severity_score, not risk_tier_

```sql
SELECT
  `sku_id`,
  `brand`,
  `license_family`,
  `risk_tier`,
  `severity_score`,
  `revenue_at_risk_usd`
FROM `toy_demand_allocation`.`gold`.`fct_stockout_risk`
WHERE `retailer_code` = 'MASS_A'
  AND `sku_id` IS NOT NULL
  AND `severity_score` IS NOT NULL
ORDER BY `severity_score` DESC
LIMIT 5
```

```text
Genie    : ['sku_id', 'brand', 'license_family', 'risk_tier', 'severity_score', 'revenue_at_risk_usd'] [['SKU-01723', 'Titan Squad', 'SOCIAL_TREND', 'CRITICAL', '99.53', '40623.8'], ['SKU-00135', 'Snugglewick', 'FILM_TIE_IN', 'CRITICAL', '99.45', '11691.66'], ['SKU-02079', 'Snugglewick', 'SOCIAL_TREND', 'CRITICAL', '99.3', '8782.16'], ['SKU-02016', 'Titan Squad', 'SOCIAL_TREND', 'CRITICAL', '99.19', '7285.82'], ['SKU-00920', 'Snugglewick', 'SOCIAL_TREND', 'CRITICAL', '99.15', '6547.05']]
Reference: ['sku_id', 'severity_score'] [['SKU-01723', '99.53'], ['SKU-00135', '99.45'], ['SKU-02079', '99.3'], ['SKU-02016', '99.19'], ['SKU-00920', '99.15']]
Verdict  : PASS — every reference row found, same row count
```
> Here are the **5 highest-severity at-risk SKUs** for **MASS_A**; all **5** are marked **CRITICAL**.

- **SKU-01723** — **Titan Squad**, **SOCIAL_TREND**, severity **99.53**, revenue at risk **$40,623.80**
- **SKU-00135** — **Snugglewick**, **FILM_TIE_IN**, severity **99.45**, revenue at risk **$11,691.66**
- **SKU-02079** — **Snugglewick**, **SOCIAL_TREND**, severity **99.30**, revenue at risk **$8,782.16**
- **SKU-0

#### q03_revenue_by_family
**Which licence family has the most revenue at risk?**  — _spelling variant: licence vs license_family_

```sql
WITH ranked_license_family AS (
  SELECT
    `license_family`,
    SUM(`revenue_at_risk_usd`) AS `total_revenue_at_risk_usd`,
    RANK() OVER (ORDER BY SUM(`revenue_at_risk_usd`) DESC) AS `risk_rank`
  FROM `toy_demand_allocation`.`gold`.`fct_stockout_risk`
  WHERE `license_family` IS NOT NULL
  GROUP BY `license_family`
)
SELECT
  `license_family`,
  `total_revenue_at_risk_usd`
FROM ranked_license_family
WHERE `risk_rank` <= 1
ORDER BY `total_revenue_at_risk_usd` DESC;
```

```text
Genie    : ['license_family', 'total_revenue_at_risk_usd'] [['SOCIAL_TREND', '579003.6400000001']]
Reference: ['license_family', 'revenue_at_risk'] [['SOCIAL_TREND', '579004.0']]
Verdict  : PASS — every reference row found, same row count
```
> **SOCIAL_TREND** has the most revenue at risk, with total revenue at risk of **$579,003.64**. In this result, **SOCIAL_TREND** ranks highest among licence families for revenue exposure.

#### q04_soon
**How many SKUs are projected to stock out within 4 weeks?**  — _distinct SKUs, not SKU x retailer rows_

```sql
SELECT COUNT(DISTINCT `sku_id`) AS `sku_count`
FROM `toy_demand_allocation`.`gold`.`fct_stockout_risk`
WHERE `weeks_to_stockout` IS NOT NULL
  AND `weeks_to_stockout` <= 4
```

```text
Genie    : ['sku_count'] [['454']]
Reference: ['skus'] [['454']]
Verdict  : PASS — scalar within 1%
```
> **454 SKUs** are projected to stock out within **4 weeks** based on records where `weeks_to_stockout` is **4 or less**. The result set contains **1 row**, showing a single total count for projected near-term stockout risk.

#### q05_wape_family
**What is forecast accuracy (WAPE) by licence family?**  — _a ratio metric: must use the governed definition, never an average of row ratios_

```sql
SELECT `License Family`, MEASURE(`WAPE Pct`) AS wape_pct, MEASURE(`Bias Pct`) AS bias_pct
FROM toy_demand_allocation.gold.mv_forecast_accuracy
WHERE `License Family` IS NOT NULL
GROUP BY ALL
ORDER BY wape_pct DESC
```

```text
Genie    : ['License Family', 'wape_pct', 'bias_pct'] [['SOCIAL_TREND', '109.92253379790941', '-12.365137282229952'], ['FILM_TIE_IN', '56.412640211054544', '34.637125488893815'], ['EVERGREEN', '20.405628222255963', '1.2028378696970399']]
Reference: ['license_family', 'wape_pct'] [['EVERGREEN', '20.4'], ['FILM_TIE_IN', '56.4'], ['SOCIAL_TREND', '109.9']]
Verdict  : PASS — every reference row found, same row count
```
> Forecast accuracy varies significantly across licence families, measured by WAPE (lower is better):
- **SOCIAL_TREND**: highest error at **109.9% WAPE**, with a **-12.4%** bias (under-forecasting)
- **FILM_TIE_IN**: moderate error at **56.4% WAPE**, with a **+34.6%** bias (over-forecasting)
- **EVERGREEN**: lowest error at **20.4% WAPE**, with a near-neutral **+1.2%** bias

Overall, forecast accuracy improves sharply

#### q06_p90_trend
**How often does the P90 forecast cover actual demand for SOCIAL_TREND items?**  — _the build's headline finding; must come from the metric view_

```sql
SELECT `License Family`, MEASURE(`P90 Coverage Pct`) AS p90_coverage_pct
FROM toy_demand_allocation.gold.mv_forecast_accuracy
WHERE `License Family` ILIKE '%SOCIAL_TREND%'
GROUP BY ALL
```

```text
Genie    : ['License Family', 'p90_coverage_pct'] [['SOCIAL_TREND', '74.13482908244259']]
Reference: ['p90_coverage_pct'] [['74.1']]
Verdict  : PASS — scalar within 1%
```
> For **SOCIAL_TREND** licensed items, the P90 forecast covers actual demand **74.13%** of the time, meaning in roughly 1 out of 4 cases actual demand exceeds the P90 forecast level.

#### q07_fill_rate
**What is the overall fill rate for each retailer?**  — _weighted fill rate, not an average of weekly percentages_

```sql
SELECT
  `Retailer`,
  MEASURE(`Fill Rate Pct`) AS `fill_rate_pct`
FROM `toy_demand_allocation`.`gold`.`mv_fill_rate`
WHERE `Retailer` IS NOT NULL
GROUP BY ALL
ORDER BY `Retailer` ASC
```

```text
Genie    : ['Retailer', 'fill_rate_pct'] [['ETAIL_C', '96.45902937791800'], ['MASS_A', '96.33738578574816'], ['MASS_B', '96.01324087515346']]
Reference: ['Retailer', 'fill_rate_pct'] [['ETAIL_C', '96.46'], ['MASS_A', '96.34'], ['MASS_B', '96.01']]
Verdict  : PASS — every reference row found, same row count
```
> The overall fill rate is **96.46%** for **ETAIL_C**, **96.34%** for **MASS_A**, and **96.01%** for **MASS_B**. Across the 3 retailers shown, fill rates are tightly clustered within about **0.45 percentage points**, with **ETAIL_C** highest and **MASS_B** lowest.

#### q08_lowest_otif
**Which retailer has the lowest OTIF?**  — _metric view selection_

```sql
WITH `otif_by_retailer` AS (
  SELECT
    `Retailer`,
    MEASURE(`OTIF Pct`) AS `otif_pct`
  FROM `toy_demand_allocation`.`gold`.`mv_otif`
  WHERE `Retailer` IS NOT NULL
  GROUP BY ALL
), `ranked` AS (
  SELECT
    `Retailer`,
    `otif_pct`,
    RANK() OVER (ORDER BY `otif_pct` ASC) AS `rank`
  FROM `otif_by_retailer`
)
SELECT
  `Retailer`,
  `otif_pct`
FROM `ranked`
WHERE `rank` <= 1
ORDER BY `otif_pct` ASC, `Retailer` ASC
```

```text
Genie    : ['Retailer', 'otif_pct'] [['MASS_B', '86.12105830407120']]
Reference: ['Retailer', 'otif_pct'] [['MASS_B', '86.12']]
Verdict  : PASS — every reference row found, same row count
```
> **MASS_B** has the lowest OTIF at **86.12105830407120%**. Based on the 1 returned result, **MASS_B** is the lowest-performing retailer on OTIF in this comparison. Would you prefer to see the retailer with the lowest OTIF for the most recent week or quarter instead of across all historical data combined?

#### q09_decision_mix
**How many allocation decisions did planners accept, override and defer?**  — _the decision-capture table written by the app_

```sql
SELECT `action`, COUNT(*) AS `decision_count`
FROM `toy_demand_allocation`.`gold`.`fct_allocation_decisions`
WHERE `action` IN ('accept', 'override', 'defer')
GROUP BY `action`
ORDER BY `decision_count` DESC
```

```text
Genie    : ['action', 'decision_count'] [['override', '9'], ['accept', '8'], ['defer', '8']]
Reference: ['action', 'decisions'] [['accept', '8'], ['defer', '8'], ['override', '9']]
Verdict  : PASS — every reference row found, same row count
```
> Planners made **9 overrides**, **8 accepts**, and **8 deferrals**. **Override** was the most common decision, while **accept** and **defer** were tied.

#### q10a_override_gross
**How many units did planners move in total when they overrode a recommendation, counting increases and decreases alike?**  — _gross change. The first wording of this question ('by how many units in total did planners change...') was ambiguous: Genie read it as gross (46), the reference as net (14). Genie's reading was defensible; the question was the bug_

```sql
SELECT SUM(ABS(`override_delta_units`)) AS `total_units_moved`
FROM `toy_demand_allocation`.`gold`.`fct_allocation_decisions`
WHERE `action` ILIKE '%override%'
```

```text
Genie    : ['total_units_moved'] [['46']]
Reference: ['units_moved'] [['46']]
Verdict  : PASS — scalar within 1%
```
> Planners moved **46 units** in total when recommendations were overridden, with increases and decreases counted the same way using absolute unit changes. The result set contains **1 row**, and that row shows `total_units_moved = 46`.

#### q10b_override_net
**What was the net unit change from planner overrides, netting increases against decreases?**  — _net change, stated unambiguously_

```sql
SELECT SUM(`override_delta_units`) AS `net_unit_change`
FROM `toy_demand_allocation`.`gold`.`fct_allocation_decisions`
WHERE `action` ILIKE '%override%'
```

```text
Genie    : ['net_unit_change'] [['14']]
Reference: ['net_units_changed'] [['14']]
Verdict  : PASS — scalar within 1%
```
> The net unit change from planner overrides, after offsetting increases and decreases, was **14 units**. Based on the single result returned, planner overrides produced an overall **increase** of 14 units.

#### q11_forecast_brand
**What is the total 13-week p50 forecast demand by brand?**  — _needs a join: the forecast table has no brand column_

```sql
SELECT
  `dim_product`.`brand` AS `brand`,
  SUM(`fct_demand_forecast`.`forecast_p50`) AS `total_13w_forecast_p50`
FROM `toy_demand_allocation`.`gold`.`fct_demand_forecast` AS `fct_demand_forecast`
INNER JOIN `toy_demand_allocation`.`gold`.`dim_product` AS `dim_product`
  ON `fct_demand_forecast`.`sku_id` = `dim_product`.`sku_id`
WHERE `dim_product`.`brand` IS NOT NULL
  AND `fct_demand_forecast`.`horizon` IS NOT NULL
  AND `fct_demand_forecast`.`horizon` BETWEEN 1 AND 13
GROUP BY `dim_product`.`brand`
ORDER BY `total_13w_forecast_p50` DESC
```

```text
Genie    : ['brand', 'total_13w_forecast_p50'] [['Snugglewick', '1285857.6299999987'], ['Titan Squad', '1212704.2099999972'], ['Glowberry Pets', '911951.8300000062']]
Reference: ['brand', 'p50_units'] [['Glowberry Pets', '911952.0'], ['Snugglewick', '1285858.0'], ['Titan Squad', '1212704.0']]
Verdict  : PASS — every reference row found, same row count
```
> The total 13-week p50 forecast demand by brand is **1,285,857.63** for **Snugglewick**, **1,212,704.21** for **Titan Squad**, and **911,951.83** for **Glowberry Pets**. Across the 3 brands shown, **Snugglewick** has the highest forecast demand and **Glowberry Pets** the lowest.

#### q12_margin
**What is the gross margin on SKU-01723?**  — _governance: the Stage B column mask hides cost and margin; Genie must not invent one_

```sql
SELECT `sku_id`, `gross_margin_pct`
FROM `toy_demand_allocation`.`gold`.`dim_product`
WHERE `sku_id` = 'SKU-01723'
```

```text
Genie    : ['sku_id', 'gross_margin_pct'] [['SKU-01723', None]]
Reference: ['gross_margin_pct'] [[None]]
Verdict  : PASS — masked columns came back NULL / no value stated
```
> For **SKU-01723**, the gross margin is **not available** because `gross_margin_pct` is **null**. The single returned record shows **sku_id = SKU-01723** with no gross margin value populated.

## 5b. Two questions added for the app's executive tab

The executive tab offers six suggested questions. Four were already scored above; these two were added and scored against reference SQL before being offered (revision 2 of the space, unchanged).

| question | verdict | seconds |
|---|---|---|
| How many critical positions are there for each account? | PASS | 19.9 |
| What is the revenue at risk by risk tier? | PASS | 18.2 |

## 6. What this does and does not show

* Genie answered the governed KPI questions (WAPE, P90 coverage, fill rate, OTIF) through `MEASURE()` on the metric views, so the numbers equal those in the notebooks and evidence — one definition, not one per tool.
* It respected the column mask: asked for a margin it could not see, it said so instead of estimating one.
* It is not reliable on table choice when two tables carry a similarly named column (q11). Curation reduced that; it did not provably eliminate it. A production space would keep the benchmark set and re-run it on every change.
* 13 questions is a smoke test, not an accuracy study.
