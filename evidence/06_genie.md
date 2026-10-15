# Evidence 06 — Stage E: Genie

| | |
|---|---|
| Produced by | `src/genie/{space_definition,create_space,run_questions,questions}.py`, `tools/evidence_06_genie.py` |
| Generated at | 2026-10-15 23:15:02 UTC |
| Space | `Demand & Allocation` — `01f1c8cc66d713fba1decec949c0740c` |
| URL | https://fevm-stable-classic-pqjg18.cloud.databricks.com/genie/rooms/01f1c8cc66d713fba1decec949c0740c |
| Warehouse | `a6857db5911f7af1` |
| Unity Catalog | `toy_demand_allocation.gold` (tables and metric views the space reads) |

## 1. What is configured now (revision 3)

Revision 3 is the configuration the build prompt specifies. It follows two earlier, deliberately thin revisions (sections 3-5), which were an experiment in curation first and instructions only to patch an observed failure; that experiment is kept because it produced the q11 finding. Revision 3 adds what the rubric asks for on top of the same curation.

```text
toy_demand_allocation.gold.dim_product                         6 column configs
toy_demand_allocation.gold.dim_retailer_dc                     5 column configs
toy_demand_allocation.gold.fct_allocation_decisions            6 column configs
toy_demand_allocation.gold.fct_demand_forecast                 4 column configs
toy_demand_allocation.gold.fct_demand_forecast_eval            4 column configs
toy_demand_allocation.gold.fct_demand_weekly                   7 column configs
toy_demand_allocation.gold.fct_inbound_eta                     5 column configs
toy_demand_allocation.gold.fct_orders_fulfillment              6 column configs
toy_demand_allocation.gold.fct_stockout_risk                   12 column configs
toy_demand_allocation.gold.mv_fill_rate                        6 column configs
toy_demand_allocation.gold.mv_forecast_accuracy                3 column configs
toy_demand_allocation.gold.mv_otif                             3 column configs
toy_demand_allocation.gold.mv_weeks_of_supply                  3 column configs
```

**13 objects** (9 Gold tables, 4 governed metric views), **53 columns with synonyms**, 8 curated example queries, 7 join specs, 1 filter and 1 measure snippets, 24 benchmarks, 5 sample questions. All read back from the live space with `include_serialized_space=true`, not from what was sent.

**General instructions** describe the business, define every KPI the build prompt names (fill rate on the REQUESTED basis, OTIF, WAPE, weeks of supply, stockout-weeks, chargebacks) and mark the metric views as the preferred source for ratio KPIs:

```text
BUSINESS
This space covers a licensed toy and collectibles manufacturer (collectible plush and licensed action figures) selling wholesale to three retail accounts from four distribution centres. All data is synthetic.
Accounts: MASS_A = Mass Retail Account A, MASS_B = Mass Retail Account B, ETAIL_C = E-tail Account C. Distribution centres: DC_EAST, DC_CENTRAL, DC_WEST, DC_SOUTH.
Units are eaches. Weeks end on Saturday and the weekly grain is the unit of analysis. The latest actual week is 2026-09-26; the forecast covers the 13 weeks after it. Licence families are EVERGREEN, FILM_TIE_IN and SOCIAL_TREND. Brands are Snugglewick, Titan Squad and Glowberry Pets.
KPI DEFINITIONS (use exactly these)
Fill rate (service level) = shipped units divided by REQUESTED units. The confirmed-basis figure flatters performance; show it only when asked for it.
OTIF (on-time in-full) = PO lines shipped within a 2-day grace AND complete, divided by all PO lines.
Forecast accuracy = WAPE = sum of absolute error divided by sum of actual units, at SKU x retailer x week. Lower is better, so the 'worst' accuracy is the HIGHEST WAPE.
Weeks of supply = latest on-hand units divided by mean weekly POS units over the last 13 weeks.
Stockout-weeks = SKU x retailer x DC x week cells where demand exceeded the units served (fct_demand_weekly.is_stockout_week).
Compliance chargeback $ = retailer penalties on non-compliant PO lines. Markdown or excess $ = margin forgone clearing excess stock. Inventory turns = 52 divided by weeks of supply.
WHERE TO LOOK
For fill rate, OTIF, forecast accuracy and weeks of supply ALWAYS use the metric views (mv_fill_rate, mv_otif, mv_forecast_accuracy, mv_weeks_of_supply) with MEASURE(). They are the governed definitions, shared with the app. Never average a percentage column and never recompute a ratio from a fact table.
fct_stockout_risk holds ONLY the SKU x retailer pairs projected to run short. Risk tiers rank CRITICAL > HIGH > MEDIUM > LOW; order by severity_score for 'worst'. For total or portfolio demand use fct_demand_forecast joined to dim_product, not fct_stockout_risk.
fct_allocation_decisions holds planner decisions: action is accept, override or defer; recommended_units is what the agent proposed; override_delta_units = planner_units minus recommended_units and is signed.
'Last quarter' means the most recent complete calendar quarter; 'the same quarter last year' is exactly twelve months earlier. Quarter is a dimension of mv_fill_rate and mv_otif.
Unit cost and gross margin are masked for everyone except finance. If they come back NULL, say they are not available to this user. Never estimate them.
```

**Curated example queries** (they are deliberately *different* from the scored questions: other retailers, thresholds and quarters, because teaching Genie the exact test would make the score meaningless):

* Which SKUs at MASS_B will run out within 6 weeks, worst first?
* How accurate is the forecast for FILM_TIE_IN by brand?
* How many overrides did each planner make, and what was the net change in units?
* What is the total 13-week p90 forecast by licence family?
* Compare MASS_B fill rate in Q2 2026 with Q2 2025.
* Which inbound shipments are more than 10 days later than first promised?
* What are weeks of supply by licence family?
* What is fill rate and order acceptance by retailer?

**Synonyms** include units / eaches / POS units, fill rate / service level, OTIF / on-time in-full, and retailer code <-> readable label (`dim_retailer_dc.retailer_label`).

**Governance carries through.** `dim_product.unit_cost_usd` and `gross_margin_pct` are masked by Unity Catalog for every identity but finance, including the catalog owner running this space. Genie inherits that: see q12.

**Two things about the write path worth knowing.** (1) The `serialized_space` schema is not in the CLI help; it is documented in the `genie-rooms` skill and returned by `GET /api/2.0/genie/spaces/{id}?include_serialized_space=true` — the earlier belief that `get-space` "does not echo it back" was wrong, it needs that query parameter. (2) The server concatenates `content` array entries with **no separator**: two sentences came back as `synthetic.Retailer`, so every entry carries its own newline. Both were found by reading the stored space back and comparing it with what was sent, not by trusting the 200 response.

## 2. How answers were scored

26 questions, each in its own Conversation-API conversation. For each, the SQL Genie generated and its rows are captured, then compared with a **reference query written before the run** (`src/genie/questions.py`): same row count, and every reference row found in Genie's rows within 1% (`rows`). Questions whose natural answer is a list, a top-1 or a set of numbers use a looser, named check: `scalar` (first value within 1%), `top1` (Genie's first row contains the reference's top row), `values` (every reference number appears in Genie's numbers, row count and label format free), and `refusal` (a masked column must come back NULL). So a verdict is a data comparison, not a read-through.

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

## 5. Revision 2 results (the q11 fix), 13 of 13

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

## 6. Revision 3: the specified configuration

The first time revision 3 ran, it answered **26 of 26** correctly. That includes the 15 questions of revisions 1-2 (no regression) and 11 new ones: the **four questions the build prompt names** (q15-q18), the remaining required KPIs (weeks of supply, chargebacks, stockout-weeks), inbound ETA slip, a brand entity-match, a portfolio forecast that must not come from the at-risk-only table (q24), and a second masked-column question (q25). **There was no new failure to record in this pass**, and none has been invented. Because Genie is non-deterministic, the whole set was run again:

| # | question | pass 1 | pass 2 | pass 3 | check |
|---|---|---|---|---|---|
| q01 | How many SKU and retailer combinations are at CRITICAL risk, and how much revenue is | PASS | PASS | PASS | rows |
| q02 | Show the 5 highest severity at-risk SKUs for MASS_A. | PASS | PASS | PASS | rows |
| q03 | Which licence family has the most revenue at risk? | PASS | PASS | PASS | rows |
| q04 | How many SKUs are projected to stock out within 4 weeks? | PASS | PASS | PASS | scalar |
| q05 | What is forecast accuracy (WAPE) by licence family? | PASS | PASS | PASS | rows |
| q06 | How often does the P90 forecast cover actual demand for SOCIAL_TREND items? | PASS | PASS | PASS | scalar |
| q07 | What is the overall fill rate for each retailer? | PASS | PASS | PASS | rows |
| q08 | Which retailer has the lowest OTIF? | PASS | PASS | PASS | rows |
| q09 | How many allocation decisions did planners accept, override and defer? | PASS | PASS | PASS | rows |
| q10a | How many units did planners move in total when they overrode a recommendation, count | PASS | PASS | PASS | scalar |
| q10b | What was the net unit change from planner overrides, netting increases against decre | PASS | PASS | PASS | scalar |
| q11 | What is the total 13-week p50 forecast demand by brand? | PASS | PASS | PASS | rows |
| q12 | What is the gross margin on SKU-01723? | PASS | PASS | PASS | refusal |
| q13 | How many critical positions are there for each account? | PASS | PASS | PASS | rows |
| q14 | What is the revenue at risk by risk tier? | PASS | PASS | PASS | rows |
| q15 | Which SKUs are projected to stock out at MASS_A in the next 4 weeks? | PASS | PASS | PASS | rows |
| q16 | What's our fill rate by retailer last quarter vs. the same quarter last year? | PASS | PASS | PASS | values |
| q17 | Which license family has the worst forecast accuracy? | PASS | PASS | PASS | top1 |
| q18 | Show me the allocation overrides planners made last week and the agent's original re | PASS | PASS | PASS | values |
| q19 | What are weeks of supply by distribution centre? | PASS | PASS | PASS | values |
| q20 | How much compliance chargeback did each retailer incur? | PASS | PASS | PASS | values |
| q21 | How many stockout weeks did MASS_B have in 2026? | PASS | PASS | PASS | scalar |
| q22 | How many inbound shipments are more than 14 days later than first promised? | PASS | PASS | PASS | scalar |
| q23 | Which brand has the most critical positions? | PASS | PASS | PASS | top1 |
| q24 | What is the total 13-week p90 forecast demand for ETAIL_C? | PASS | PASS | PASS | scalar |
| q25 | What is the unit cost of SKU-00042? | PASS | PASS | PASS | refusal |

Passes: 26/26, 26/26, 26/26 across 3 full runs (78 of 78 answers). Timing, per question: median 18 s.

**Two things that look like passes and are worth reading closely:**

* **q18 passed by coincidence of interpretation.** "Last week" is ambiguous. The reference uses the calendar week before today on `decided_at`; Genie used the latest override `week_end_date` (2026-10-03). Today both select the same 9 rows, so the verdict is PASS, but they are different rules and will disagree as the calendar moves. The reference window is relative to the run date.
* **q15 and q17 are strict only where they are checked.** q15's reference is 287 distinct SKUs and Genie returned 287 rows (the row-count check is the strong one); q17 is a `top1` check, so it proves the worst family is first, not that the rest of the ordering is right.

### Generated SQL and rows, revision 3 (first pass)

#### q15_stockout_mass_a_4w
**Which SKUs are projected to stock out at MASS_A in the next 4 weeks?**  — _rubric question 1: a filtered list, one row per SKU_

```sql
SELECT sku_id, brand, license_family, risk_tier, severity_score, weeks_to_stockout, projected_stockout_week, units_at_risk, revenue_at_risk_usd
FROM toy_demand_allocation.gold.fct_stockout_risk
WHERE retailer_code = 'MASS_A' AND weeks_to_stockout <= 4
ORDER BY severity_score DESC
```

```text
Genie    : ['sku_id', 'brand', 'license_family', 'risk_tier', 'severity_score', 'weeks_to_stockout', 'projected_stockout_week', 'units_at_risk', 'revenue_at_risk_usd'] [['SKU-01723', 'Titan Squad', 'SOCIAL_TREND', 'CRITICAL', '99.53', '1', '2026-10-03', '3460.0', '40623.8'], ['SKU-00135', 'Snugglewick', 'FILM_TIE_IN', 'CRITICAL', '99.45', '1', '2026-10-03', '1766.0', '11691.66'], ['SKU-02079', 'Snugglewick', 'SOCIAL_TREND', 'CRITICAL', '99.3', '1', '2026-10-03', '706.0', '8782.16'], ['SKU-02016', 'Titan Squad', 'SOCIAL_TREND', 'CRITICAL', '99.19', '1', '2026-10-03', '391.0', '7285.82'], ['SKU-00920', 'Snugglewick', 'SOCIAL_TREND', 'CRITICAL', '99.15', '1', '2026-10-03', '1228.0', '6547.05']]  (287 rows)
Reference: ['sku_id'] [['SKU-00002'], ['SKU-00007'], ['SKU-00016'], ['SKU-00025'], ['SKU-00032']]
Verdict  : PASS — every reference row found, same row count
```
> Examples include:
- **SKU-01723** (**Titan Squad**, **SOCIAL_TREND**) is projected to stock out in **1 week** on **2026-10-03** with **CRITICAL** risk.
- **SKU-00135** (**Snugglewick**, **FILM_TIE_IN**) is projected to stock out in **1 week** on **2026-10-03** with **CRITICAL** risk.
- **SKU-02079** (**Snugglewick**, **SOCIAL_TREND**) is projected to stock out in **1 week** on **2026-10-03** with **CRITICAL** risk.
-

#### q16_fill_qoq
**What's our fill rate by retailer last quarter vs. the same quarter last year?**  — _rubric question 2: two quarters x three retailers from the governed metric view; 'last quarter' = Q3 2026 as of 2026-10-15_

```sql
SELECT `Retailer`, `Quarter`, MEASURE(`Fill Rate Pct`) AS fill_rate_pct
FROM toy_demand_allocation.gold.mv_fill_rate
WHERE `Quarter` IN (TIMESTAMP '2026-07-01', TIMESTAMP '2025-07-01')
GROUP BY ALL
ORDER BY `Retailer`, `Quarter`
```

```text
Genie    : ['Retailer', 'Quarter', 'fill_rate_pct'] [['ETAIL_C', '2025-07-01T00:00:00.000Z', '97.93268619003472'], ['ETAIL_C', '2026-07-01T00:00:00.000Z', '94.28869998058918'], ['MASS_A', '2025-07-01T00:00:00.000Z', '97.84928409947249'], ['MASS_A', '2026-07-01T00:00:00.000Z', '95.59176974791223'], ['MASS_B', '2025-07-01T00:00:00.000Z', '98.50858151983959']]  (6 rows)
Reference: ['Retailer', 'Quarter', 'fill_rate_pct'] [['ETAIL_C', '2025-07-01T00:00:00.000Z', '97.93'], ['MASS_A', '2025-07-01T00:00:00.000Z', '97.85'], ['MASS_A', '2026-07-01T00:00:00.000Z', '95.59'], ['MASS_B', '2026-07-01T00:00:00.000Z', '95.68'], ['MASS_B', '2025-07-01T00:00:00.000Z', '98.51']]
Verdict  : PASS — all 6 reference values found
```
> Fill rate declined year-over-year for all three retailers between Q3 2025 and Q3 2026:
- **ETAIL_C**: dropped from **97.9%** to **94.3%**
- **MASS_A**: dropped from **97.8%** to **95.6%**
- **MASS_B**: dropped from **98.5%** to **95.7%**

**MASS_B** had the highest fill rate in both periods, while **ETAIL_C** saw the steepest year-over-year decline (about 3.6 percentage points).

#### q17_worst_wape_family
**Which license family has the worst forecast accuracy?**  — _rubric question 3: 'worst' accuracy is the HIGHEST WAPE, a direction trap_

```sql
WITH ranked AS (
  SELECT `License Family`,
         MEASURE(`WAPE Pct`) AS wape_pct,
         MEASURE(`Bias Pct`) AS bias_pct,
         RANK() OVER (ORDER BY MEASURE(`WAPE Pct`) DESC) AS rnk
  FROM toy_demand_allocation.gold.mv_forecast_accuracy
  WHERE `License Family` IS NOT NULL
  GROUP BY ALL
)
SELECT `License Family`, wape_pct, bias_pct
FROM ranked
WHERE rnk = 1
```

```text
Genie    : ['License Family', 'wape_pct', 'bias_pct'] [['SOCIAL_TREND', '109.92253379790941', '-12.365137282229952']]  (1 rows)
Reference: ['license_family', 'wape_pct'] [['SOCIAL_TREND', '109.9']]
Verdict  : PASS — first row matches the reference's top row
```
> The **SOCIAL_TREND** license family has the worst forecast accuracy, with a WAPE of **109.9%**, the highest (worst) among all license families. Its bias of **-12.4%** indicates the forecast tends to under-predict actual demand for this license family.

#### q18_overrides_last_week
**Show me the allocation overrides planners made last week and the agent's original recommendation.**  — _rubric question 4: the decision-capture loop; 'original recommendation' = recommended_units. The window is relative to today, so this reference moves with the calendar_

```sql
WITH `target_week` AS ( SELECT MAX(`week_end_date`) AS `week_end_date` FROM `toy_demand_allocation`.`gold`.`fct_allocation_decisions` WHERE `action` = 'override' ) SELECT `decision_id`, `planner_id`, `sku_id`, `retailer_code`, `week_end_date`, `recommended_units` AS `agent_original_recommendation`, `planner_units`, `override_delta_units`, `action`, `rationale_text`, `decided_at` FROM `toy_demand_allocation`.`gold`.`fct_allocation_decisions` WHERE `action` = 'override' AND `week_end_date` = (SELECT `week_end_date` FROM `target_week`) ORDER BY `decided_at` DESC, `decision_id` DESC
```

```text
Genie    : ['decision_id', 'planner_id', 'sku_id', 'retailer_code', 'week_end_date', 'agent_original_recommendation', 'planner_units', 'override_delta_units', 'action', 'rationale_text', 'decided_at'] [['24', 'planner_b', 'SKU-02079', 'ETAIL_C', '2026-10-03', '44', '50', '6', 'override', 'Adjusted to protect the line review at the mass account; air freight already committed.', '2026-10-09T23:29:28.626Z'], ['21', 'planner_b', 'SKU-00135', 'ETAIL_C', '2026-10-03', '118', '114', '-4', 'override', 'Adjusted to protect the line review at the mass account; air freight already committed.', '2026-10-09T23:29:28.626Z'], ['18', 'planner_b', 'SKU-00457', 'ETAIL_C', '2026-10-03', '110', '116', '6', 'override', 'Adjusted to protect the line review at the mass account; air freight already committed.', '2026-10-09T23:29:28.626Z'], ['15', 'planner_b', 'SKU-01815', 'ETAIL_C', '2026-10-03', '96', '92', '-4', 'override', 'Adjusted to protect the line review at the mass account; air freight already committed.', '2026-10-09T23:29:28.626Z'], ['12', 'planner_b', 'SKU-01674', 'MASS_A', '2026-10-03', '23', '29', '6', 'override', 'Adjusted to protect the line review at the mass account; air freight already committed.', '2026-10-09T23:29:28.626Z']]  (9 rows)
Reference: ['recommended_units', 'planner_units'] [['82', '88'], ['24', '30'], ['44', '50'], ['30', '26'], ['223', '219']]
Verdict  : PASS — all 18 reference values found
```
> For the latest override week in the data, **2026-10-03**, planners made **9 override decisions** and every row shown is an override with the agent’s original recommendation alongside the planner’s final units. Examples include:
- **SKU-02079 / ETAIL_C**: agent recommended **44** units; planner set **50** (**+6**)
- **SKU-00135 / ETAIL_C**: agent recommended **118** units; planner set **114** (**-4**)
- **SKU-00457 / 

#### q19_wos_dc
**What are weeks of supply by distribution centre?**  — _a required KPI; a ratio of sums, so averaging per-SKU values is wrong_

```sql
SELECT
  `DC`,
  MEASURE(`Weeks Of Supply`) AS `weeks_of_supply`,
  MEASURE(`On Hand Units`) AS `on_hand_units`,
  MEASURE(`In Transit Units`) AS `in_transit_units`,
  MEASURE(`Total Available Units`) AS `total_available_units`,
  MEASURE(`Weekly Demand Units`) AS `weekly_demand_units`
FROM `toy_demand_allocation`.`gold`.`mv_weeks_of_supply`
WHERE `DC` IS NOT NULL
GROUP BY ALL
ORDER BY `DC` ASC
```

```text
Genie    : ['DC', 'weeks_of_supply', 'on_hand_units', 'in_transit_units', 'total_available_units', 'weekly_demand_units'] [['DC_CENTRAL', '17.464489713662', '460282', '157479', '617761', '26355.307687'], ['DC_EAST', '16.532540357394', '878323', '234284', '1112607', '53126.923087'], ['DC_SOUTH', '19.439644312518', '471854', '98563', '570417', '24272.769214'], ['DC_WEST', '16.171529836971', '859890', '243270', '1103160', '53173.076924']]  (4 rows)
Reference: ['DC', 'wos'] [['DC_WEST', '16.17'], ['DC_EAST', '16.53'], ['DC_CENTRAL', '17.46'], ['DC_SOUTH', '19.44']]
Verdict  : PASS — all 4 reference values found
```
> Weeks of supply by distribution centre ranges from **16.17** to **19.44** weeks across the 4 centres, with **DC_SOUTH** highest at **19.44** weeks and **DC_WEST** lowest at **16.17** weeks.

- **DC_CENTRAL:** **17.46** weeks
- **DC_EAST:** **16.53** weeks
- **DC_SOUTH:** **19.44** weeks
- **DC_WEST:** **16.17** weeks

The two highest-demand centres, **DC_WEST** and **DC_EAST**, also have the lowest weeks of supply at

#### q20_chargebacks
**How much compliance chargeback did each retailer incur?**  — _a required KPI (compliance chargeback $)_

```sql
SELECT `Retailer`, MEASURE(`Chargeback USD`) AS chargeback_usd
FROM toy_demand_allocation.gold.mv_fill_rate
GROUP BY ALL
ORDER BY chargeback_usd DESC
```

```text
Genie    : ['Retailer', 'chargeback_usd'] [['MASS_B', '444766.300000001'], ['ETAIL_C', '427082.96999999945'], ['MASS_A', '388868.55000000057']]  (3 rows)
Reference: ['Retailer', 'chargeback_usd'] [['ETAIL_C', '427083.0'], ['MASS_B', '444766.0'], ['MASS_A', '388869.0']]
Verdict  : PASS — all 3 reference values found
```
> Compliance chargebacks vary across the three retail accounts, with **Mass Retail Account B (MASS_B)** incurring the highest total at **$444,766**, followed by **E-tail Account C (ETAIL_C)** at **$427,083**, and **Mass Retail Account A (MASS_A)** at the lowest with **$388,869**. Overall, the gap between the highest and lowest chargeback totals is relatively modest (around $56,000), suggesting non-compliance penalties 

#### q21_stockout_weeks
**How many stockout weeks did MASS_B have in 2026?**  — _a required KPI; counts SKU x DC x week cells where demand exceeded service_

```sql
SELECT COUNT(*) AS `stockout_weeks`
FROM `toy_demand_allocation`.`gold`.`fct_demand_weekly`
WHERE `retailer_code` = 'MASS_B'
  AND `week_end_date` >= DATE '2026-01-01'
  AND `week_end_date` < DATE '2027-01-01'
  AND `is_stockout_week` = TRUE
```

```text
Genie    : ['stockout_weeks'] [['2452']]  (1 rows)
Reference: ['n'] [['2452']]
Verdict  : PASS — scalar within 1%
```
> MASS_B had **2,452 stockout weeks** in **2026** based on weekly records flagged as stockout weeks. This result reflects the total count of MASS_B weekly stockout occurrences during 2026.

#### q22_inbound_slip
**How many inbound shipments are more than 14 days later than first promised?**  — _inbound ETA table; slip = revised minus original_

```sql
SELECT COUNT(*) AS `shipment_count`
FROM `toy_demand_allocation`.`gold`.`fct_inbound_eta`
WHERE `eta_slip_days` > 14
```

```text
Genie    : ['shipment_count'] [['3151']]  (1 rows)
Reference: ['n'] [['3151']]
Verdict  : PASS — scalar within 1%
```
> There are **3,151** inbound shipments where `eta_slip_days` is greater than **14 days** later than first promised. Based on the result set, the count of delayed inbound shipments over the 14-day threshold is **3,151**.

#### q23_brand_critical
**Which brand has the most critical positions?**  — _entity matching on brand; top-1 of a count_

```sql
WITH `brand_counts` AS (
  SELECT
    `brand`,
    COUNT(*) AS `critical_positions`
  FROM `toy_demand_allocation`.`gold`.`fct_stockout_risk`
  WHERE `risk_tier` = 'CRITICAL'
    AND `brand` IS NOT NULL
  GROUP BY `brand`
), `ranked` AS (
  SELECT
    `brand`,
    `critical_positions`,
    RANK() OVER (ORDER BY `critical_positions` DESC) AS `brand_rank`
  FROM `brand_counts`
)
SELECT
  `brand`,
  `critical_positions`
FROM `ranked`
WHERE `brand_rank` <= 1
ORDER BY `critical_positions` DESC, `brand` ASC
```

```text
Genie    : ['brand', 'critical_positions'] [['Snugglewick', '179']]  (1 rows)
Reference: ['brand', 'n'] [['Snugglewick', '179']]
Verdict  : PASS — first row matches the reference's top row
```
> **Snugglewick** has the most critical positions, with **179** SKU-by-retailer pairs flagged as **CRITICAL** in `fct_stockout_risk`. In the returned result set, **Snugglewick** is the top-ranked brand for critical stockout risk exposure.

#### q24_forecast_etail
**What is the total 13-week p90 forecast demand for ETAIL_C?**  — _portfolio demand must come from fct_demand_forecast, not the at-risk-only table_

```sql
SELECT SUM(`fct_demand_forecast`.`forecast_p90`) AS `total_p90_forecast_demand`
FROM `toy_demand_allocation`.`gold`.`fct_demand_forecast` AS `fct_demand_forecast`
WHERE `fct_demand_forecast`.`retailer_code` = 'ETAIL_C'
```

```text
Genie    : ['total_p90_forecast_demand'] [['1929826.1299999934']]  (1 rows)
Reference: ['p90_units'] [['1929826.0']]
Verdict  : PASS — scalar within 1%
```
> The total p90 forecast demand for **ETAIL_C** is **1,929,826.13 units** based on the data returned. This result reflects the sum of `forecast_p90` for retailer code **ETAIL_C** in the query.

#### q25_unit_cost
**What is the unit cost of SKU-00042?**  — _governance: cost is masked for everyone except finance_

```sql
SELECT `sku_id`, `unit_cost_usd`
FROM `toy_demand_allocation`.`gold`.`dim_product`
WHERE `sku_id` = 'SKU-00042'
```

```text
Genie    : ['sku_id', 'unit_cost_usd'] [['SKU-00042', None]]  (1 rows)
Reference: ['unit_cost_usd'] [[None]]
Verdict  : PASS — masked columns came back NULL / no value stated
```
> For **SKU-00042**, **unit cost is not available to this user** because `unit_cost_usd` is **NULL** in the data. The single returned record shows `sku_id = SKU-00042` and `unit_cost_usd = NULL`.

## 7. What this does and does not show

* Genie answered the governed KPI questions (WAPE, P90 coverage, fill rate, OTIF) through `MEASURE()` on the metric views, so the numbers equal those in the notebooks and evidence — one definition, not one per tool.
* It respected the column mask: asked for a margin it could not see, it said so instead of estimating one.
* In revision 2 it was not reliable on table choice when two tables carry a similarly named column (q11). Curation reduced that; it did not provably eliminate it. A production space would keep the benchmark set and re-run it on every change.
* 26 questions, run 3 times, is a smoke test with a repeat, not an accuracy study. Passing every one is a result about this question set, not a guarantee about the next question.
* The examples in the space were written to be different from the scored questions, but they are written by the same person who wrote the questions, so the separation is by intent, not by an independent party.
