# Evidence 03 — Stage C1: demand forecast, sweep, champion/challenger

| | |
|---|---|
| Produced by | `notebooks/03_demand_forecast.ipynb` on cluster `1008-205814-qhbfm7tc` (DBR 16.4 LTS ML), then `tools/evidence_03_ml_forecast.py` reading MLflow and Unity Catalog back |
| Generated at | 2026-10-15 18:43:59 UTC |
| Workspace | `https://fevm-stable-classic-pqjg18.cloud.databricks.com` |
| Profile | `vm-summer-classic` |
| MLflow experiment | `/Users/sixuan.he@databricks.com/toy_demand_allocation_forecast` |
| Registered model | `toy_demand_allocation.ml.demand_forecaster` |
| UC paths written | `toy_demand_allocation.gold.fct_demand_forecast`, `toy_demand_allocation.gold.fct_demand_forecast_eval`, `toy_demand_allocation.gold.fct_stockout_risk`, `toy_demand_allocation.ml.demand_features_weekly`, `toy_demand_allocation.gold.mv_forecast_accuracy` |
| Data | 100% synthetic |

---

## 1. Problem setup

| Choice | Value | Why |
|---|---|---|
| Grain | SKU x retailer x week | The DC is a fulfilment detail, not a demand signal, so Gold's SKU x retailer x DC x week fact is aggregated up |
| Target | `true_demand_units` | The business wants to predict *demand*, but production only observes *sales*, censored by your own stockouts in exactly the weeks that matter. This synthetic dataset carries unconstrained demand as ground truth; a real deployment must reconstruct it from censored sales first, which the README lists as a prerequisite |
| Strategy | Direct multi-horizon | Every row is (series, origin, horizon) with features computed only at or before the origin and `horizon` as a model feature. No recursion, so no error compounding over the 13-week score |
| Holdout | **Single forecast origin**, 13 horizons | Stand at one week and forecast the next 13. This is the decision a planner actually makes and is strictly harder than rolling one-step-ahead, which would let the model see an actual from week 149 while scoring week 150 |
| Series | 4,852 | `SOCIAL_TREND` series average only ~17 active weeks, so lag-52 features are null for most of the portfolio and were dropped |

### Feature table, registered in Feature Engineering

```text
feature_rows  skus  retailers  origin_weeks  first_week  last_week 
------------  ----  ---------  ------------  ----------  ----------
287966        2324  3          156           2023-10-07  2026-09-26
(1 row)
```

The training set is assembled through a `FeatureLookup` on the exact key
(`sku_id`, `retailer_code`, `origin_week`), declared once and reused at inference
instead of being reimplemented in a scoring script. **This is an exact-key lookup,
not an as-of (point-in-time) join:** the feature table deliberately has no
timeseries column. The leakage guard is the frame instead (features are computed
at the origin week, the label is taken from origin + h, and the holdout is a
single origin). A timeseries `FeatureLookup`, which the build prompt describes in
Stage C1, is not implemented; `docs/RUBRIC_AUDIT.md` (G7) records that. `retailer_code` is deliberately a
primary key of the feature table rather than a feature column — including it
as both makes `create_table` fail with `COLUMN_ALREADY_EXISTS`.

## 2. The sweep — leaderboard

Seven candidates across six families, one MLflow experiment, identical
metrics on an identical holdout. Pulled back out of the tracking server with
`mlflow.search_runs()`, filtered to a single `sweep_id` so the table is an
exact record of one execution rather than an accumulation across runs.

| rank | algorithm | family | wape_pct | mape_nonzero_pct | bias_pct | rmse | pinball_p50 | pinball_p90 | p90_coverage_pct | train_seconds | run_id |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lightgbm | gradient_boosting | 67.419 | 176.793 | 4.007 | 102.629 | 11.32 | 9.613 | 89.837 | 51.9 | d76f7f2cf35549d59323e8d2b802741c |
| 2 | xgboost | gradient_boosting | 72.386 | 194.987 | 9.401 | 98.937 | 12.154 | 9.916 | 88.917 | 80.5 | b6bc59817ee741efaded55c38df265bc |
| 3 | ridge_linear | linear | 91.665 | 406.062 | 31.89 | 96.371 | 15.391 | 18.144 | 85.317 | 12.4 | ba4a53bf59294a9588e4c5858bd7670e |
| 4 | random_forest | sklearn_ensemble | 93.282 | 320.046 | 34.981 | 113.766 | 15.662 | 10.744 | 91.608 | 143.6 | fe029c5e0b1f493e97fc88e3de3fbbe2 |
| 5 | seasonal_naive | baseline | 95.342 | 269.2 | 20.241 | 119.124 | 16.008 | 16.477 | 89.629 | 0.7 | 8a6456aae5a3403e8ec369f2efb14bc2 |
| 6 | trailing_mean_13 | statistical | 96.724 | 258.484 | 16.466 | 114.866 | 16.24 | 15.101 | 91.173 | 0.4 | 4cfade5323f64340968fc6575e997b9c |
| 7 | sklearn_hist_gbm | sklearn_boosting | 138.684 | 301.994 | 71.503 | 306.437 | 23.285 | 14.158 | 90.689 | 26.4 | 9f3e32966e624416b837a73ef14073ab |

**WAPE is the headline** because it is volume-weighted and therefore stable
on intermittent series. The `mape_nonzero_pct` column shows why a per-row
MAPE average is not usable here — 177% to 406% — since it is dominated by
near-zero denominators on slow sellers. It is reported because buyers ask
for it, and the gap between the two is itself the argument.

Three findings worth stating:

1. **Gradient boosting wins decisively** — 67.4% WAPE (lightgbm) against a 95.3%
   seasonal-naive baseline, a 27.9-point / 29.3% relative improvement.
   Across three executions of this notebook the champion's WAPE was 67.36%,
   67.56% and 67.42%: a ~0.2-point run-to-run spread from multi-threaded
   LightGBM. The ranking at the top never changed; quote the headline as
   'about 67.4%', not to two decimals.
2. **4 candidates (`ridge_linear`, `random_forest`, `trailing_mean_13`, `sklearn_hist_gbm`) land within 10 points of the
   baseline or worse.** Not every family suits this problem and the
   leaderboard says so. `sklearn_hist_gbm` in particular is unstable under
   Poisson loss: 138.7% on this run against 93.0% on the previous one,
   with identical hyperparameters and the same hash-selected training rows;
   the join feeding it was the only change. The cause was not isolated. It
   is reported as measured, not tuned until it behaves.
3. **The first version of this sweep was useless and the reason is
   instructive.** See §3.

## 3. Two modelling errors found and fixed

Recorded because the corrected leaderboard is only meaningful if the
mistakes behind it are visible.

**No forward calendar.** The first run scored every boosting model *worse*
than a 13-week trailing mean, with +62% to +106% bias. The cause: the
holdout stands at a single forecast origin, so every origin-based calendar
feature is **constant across all 50,574 test rows**, and the model had no
way to distinguish a July target week from a late-September one except
through `horizon`. Adding calendar features for the week being *predicted*
— which is known at forecast time, so not leakage — moved XGBoost from
148.2% to 72.9% WAPE.

**Squared error on an overdispersed count.** Weekly demand here is a
non-negative count with a long right tail; least squares chases that tail
and over-predicts the bulk of the portfolio. Switching the three boosting
candidates to a Poisson objective cut bias from +69.7% to +5.2% on the
eventual champion. `HistGradientBoosting` with Poisson loss initially
*diverged* — RMSE 5,842 and +389% bias, an order of magnitude worse than
naive — and needed shallower trees, a lower learning rate and stronger leaf
regularisation to stabilise at 93.0%.

**Non-reproducible sampling.** The training subsample used `sampleBy`, which
is only deterministic for a fixed partitioning. Because the feature table is
rebuilt each run, the sample drifted and the champion flipped between
XGBoost and LightGBM on consecutive executions (72.73 vs 71.41 WAPE for the
same algorithm). Membership is now a hash of the row key, independent of how
Spark laid the data out.

## 4. Where the forecast is good and where it is not

The portfolio number hides the shape of the problem. Queried through the
governed metric view `gold.mv_forecast_accuracy`, not recomputed ad hoc:

```sql
SELECT `License Family`,
       ROUND(MEASURE(`WAPE Pct`), 2)         AS wape_pct,
       ROUND(MEASURE(`Bias Pct`), 2)         AS bias_pct,
       ROUND(MEASURE(`P90 Coverage Pct`), 2) AS p90_coverage_pct,
       MEASURE(`Actual Units`)               AS actual_units,
       MEASURE(`Forecast Rows`)              AS forecast_rows
FROM toy_demand_allocation.gold.mv_forecast_accuracy
GROUP BY ALL ORDER BY wape_pct
```

```text
License Family  wape_pct  bias_pct  p90_coverage_pct  actual_units  forecast_rows
--------------  --------  --------  ----------------  ------------  -------------
EVERGREEN       20.41     1.2       92.00             547171.0      26742        
FILM_TIE_IN     56.41     34.64     96.18             433632.0      14383        
SOCIAL_TREND    109.92    -12.37    74.13             717500.0      9449         
(3 rows)
```

This is the most useful table in the stage:

* **`EVERGREEN` forecasts at 20.4% WAPE with +1.2% bias and 92.0% p90
  coverage.** On predictable demand the model is genuinely good and the
  interval is calibrated.
* **`SOCIAL_TREND` forecasts at 109.9% WAPE with 74.1% p90 coverage.** Viral
  spikes are close to unforecastable from history, *and* the upper quantile
  under-covers by 16 points precisely where cover matters most. Anyone
  claiming a single portfolio accuracy number for this business is hiding
  this row.
* **`FILM_TIE_IN` sits between, over-forecasting by 35%** — release windows
  are partly predictable, and the model errs long.

The operational consequence is specific: the p90 drives `fct_stockout_risk`,
so on trend items the risk table understates exposure. That is the honest
limitation to put in front of a planner, and it is why Stage C2's allocator
takes contractual commitment and margin into account rather than trusting
the quantile alone.

### Accuracy decay by horizon

```text
horizon  wape_pct  bias_pct  actual_units
-------  --------  --------  ------------
1        39.3      5.9       151551.0    
2        46.55     2.14      152495.0    
3        56.49     2.55      153318.0    
4        63.79     0.1       152787.0    
5        71.53     -0.27     148362.0    
6        76.15     -0.88     142875.0    
7        75.43     -0.1      130683.0    
8        76.56     1.35      121495.0    
9        78.1      5.64      113349.0    
10       78.33     5.53      110499.0    
11       78.7      11.06     105812.0    
12       76.7      10.87     105150.0    
13       77.18     14.84     109927.0    
(13 rows)
```

## 5. p90: derived interval beat a native quantile model

Two methods were scored on the same holdout. The derived interval scales the
point forecast by the per-horizon 90th percentile of the validation residual
ratio; the native model is a LightGBM quantile regressor at alpha 0.9.

| p90 method | pinball_p90 | coverage % | mean p90 |
|---|---:|---:|---:|
| derived residual ratio | **9.5261** | 89.95 | 56.5 |
| native LightGBM quantile | 9.7005 | 89.61 | 74.8 |

The simpler method won on both pinball loss and calibration, and is far less
conservative — a mean p90 of 56.5 units against 74.8. The native quantile
model buys its coverage by over-reserving, which in this business means
inventory that ends up cleared at markdown. The derived interval was
selected on that evidence rather than on the assumption that the more
sophisticated option is better.

## 6. Registered in Unity Catalog with both aliases

Both the winner and the runner-up are logged through
`FeatureEngineeringClient.log_model`, so each carries its `FeatureLookup`
spec and inference can resolve features by key instead of requiring the
caller to rebuild 35 columns correctly.

```text
 version  aliases                  status     source_run_id                     
--------  ------------------------ ---------- ----------------------------------
       9  -                        READY      71e170aa1ae7404189a3e31807b054e6  
      10  -                        READY      1c248f98586e48e7abb63752e98b6fb7  
      11  -                        READY      d695f9a22f6142e29730115da80fa35d  
      12  -                        READY      856d434cfd374f75a1e0305942840ed3  
      13  @champion                READY      66faa49606d6458a92d9218ad6012337  
      14  @challenger              READY      2849ddaafa20499bae664c9f44b4db42  
(6 versions)
```

Alias assignments — this is what every downstream consumer loads, so no
notebook, app or serving endpoint references a version number:

| Alias | Version | Algorithm | Holdout WAPE % | Resolved reference |
|---|---:|---|---:|---|
| `@champion` | 13 | lightgbm | 67.419 | `models:/toy_demand_allocation.ml.demand_forecaster@champion` |
| `@challenger` | 14 | xgboost | 72.386 | `models:/toy_demand_allocation.ml.demand_forecaster@challenger` |

Versions accumulate across notebook runs, which is the point of a registry:
earlier versions stay auditable and the aliases always resolve to the
current sweep. Promotion of the challenger is an alias move, not a re-export.

## 7. 13 weeks forward

Scored in a single pass from the last actual week, p50 and p90. The window
lands in the Q4 build, which is when a licensed-toy planner is most exposed.

```text
week_end_date  horizon  series  total_p50  total_p90  model_alias  model_version
-------------  -------  ------  ---------  ---------  -----------  -------------
2026-10-03     1        4498    214327.0   344252.0   champion     13           
2026-10-10     2        4498    242887.0   384409.0   champion     13           
2026-10-17     3        4498    260854.0   400022.0   champion     13           
2026-10-24     4        4498    293837.0   471830.0   champion     13           
2026-10-31     5        4498    308786.0   501658.0   champion     13           
2026-11-07     6        4498    322579.0   524827.0   champion     13           
2026-11-14     7        4498    323268.0   541872.0   champion     13           
2026-11-21     8        4498    309294.0   512438.0   champion     13           
2026-11-28     9        4498    284603.0   455326.0   champion     13           
2026-12-05     10       4498    252889.0   403727.0   champion     13           
2026-12-12     11       4498    224350.0   360904.0   champion     13           
2026-12-19     12       4498    198911.0   318257.0   champion     13           
2026-12-26     13       4498    173929.0   265616.0   champion     13           
(13 rows)
```

### Forecast vs actual on the holdout

```text
sku_id     retailer_code  week_end_date  horizon  actual_units  forecast_p50  forecast_p90  abs_error  license_family  lifecycle_stage
---------  -------------  -------------  -------  ------------  ------------  ------------  ---------  --------------  ---------------
SKU-02034  ETAIL_C        2026-08-01     5        5057.0        308.67        501.46        4748.33    SOCIAL_TREND    MATURE         
SKU-02034  ETAIL_C        2026-07-25     4        4328.0        287.99        462.45        4040.01    SOCIAL_TREND    MATURE         
SKU-02034  ETAIL_C        2026-08-08     6        4132.0        300.21        488.43        3831.79    SOCIAL_TREND    MATURE         
SKU-01969  MASS_B         2026-07-25     4        3972.0        3627.67       5825.15       344.33     FILM_TIE_IN     MATURE         
SKU-01089  ETAIL_C        2026-08-29     9        3686.0        230.52        368.81        3455.48    SOCIAL_TREND    MATURE         
SKU-01089  ETAIL_C        2026-08-15     7        3574.0        273.29        458.09        3300.71    SOCIAL_TREND    MATURE         
SKU-01969  MASS_B         2026-07-04     1        3208.0        4786.02       7687.33       1578.02    FILM_TIE_IN     MATURE         
SKU-02034  ETAIL_C        2026-07-18     3        3177.0        319.69        490.25        2857.31    SOCIAL_TREND    MATURE         
SKU-01969  MASS_B         2026-07-11     2        3161.0        4865.7        7700.77       1704.7     FILM_TIE_IN     MATURE         
SKU-01089  ETAIL_C        2026-08-22     8        2961.0        241.68        400.41        2719.32    SOCIAL_TREND    MATURE         
SKU-01815  ETAIL_C        2026-09-12     11       2772.0        160.68        258.48        2611.32    SOCIAL_TREND    MATURE         
SKU-02034  ETAIL_C        2026-08-15     7        2724.0        280.87        470.81        2443.13    SOCIAL_TREND    MATURE         
(12 rows)
```

## 8. `gold.fct_stockout_risk` — the spine of C2, D, E and F

One row per at-risk SKU x retailer: will it run out, when, and what is it
worth. Supply is deliberately **shared** — inventory is held at SKU x DC, not
per retailer, so each retailer's claim on a SKU is scaled by its share of
forecast demand. That is what makes the allocation genuinely constrained.

### Tiering, and why it is necessary

```text
risk_tier  combinations  min_weeks  max_weeks  units_at_risk  revenue_at_risk_usd  avg_severity
---------  ------------  ---------  ---------  -------------  -------------------  ------------
CRITICAL   434           1          2          54322.0        509958.0             77.2        
HIGH       440           3          4          26038.0        216010.0             69.7        
MEDIUM     1479          5          8          59659.0        556576.0             60.5        
LOW        663           9          13         10233.0        98848.0              43.2        
(4 rows)
```

A 13-week cumulative projection flags about two thirds of the portfolio, and
that is not an arithmetic error — it is what *will demand exceed supply
within 13 weeks* means for a business carrying roughly 13 weeks of cover. It
is a replenishment signal, not a 3,000-item alarm, so rows are tiered by how
soon the shortfall lands. **CRITICAL and HIGH — 884 combinations — are the
planner's actual Monday queue**; the rest is the ordering book. The app, the
agent and Genie all filter on this tier rather than each re-deriving a
threshold.

One statistical correction along the way: cumulative p90 is **not** the
running sum of weekly p90s. The sum of quantiles is not the quantile of the
sum — adding thirteen independent weekly p90s produces roughly a p99.9 of the
13-week total. The weekly sigma is recovered from the p90-p50 gap, variances
accumulate, and the cumulative quantile is rebuilt from the cumulative mean,
so the interval widens with the square root of the horizon rather than
linearly.

### Top 15 by severity — the head of the worklist

```text
sku_id     retailer_code  brand           license_family  lifecycle_stage  risk_tier  projected_stockout_week  weeks_to_stockout  units_at_risk  revenue_at_risk_usd  severity_score
---------  -------------  --------------  --------------  ---------------  ---------  -----------------------  -----------------  -------------  -------------------  --------------
SKU-01723  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  3460.0         40623.8              99.53         
SKU-00135  MASS_A         Snugglewick     FILM_TIE_IN     LAUNCH           CRITICAL   2026-10-03               1                  1766.0         11691.66             99.45         
SKU-02079  MASS_A         Snugglewick     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  706.0          8782.16              99.3          
SKU-02016  MASS_A         Titan Squad     SOCIAL_TREND    LAUNCH           CRITICAL   2026-10-03               1                  391.0          7285.82              99.19         
SKU-01932  MASS_B         Titan Squad     FILM_TIE_IN     LAUNCH           CRITICAL   2026-10-03               1                  910.0          20556.67             99.18         
SKU-00920  MASS_A         Snugglewick     SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  1228.0         6547.05              99.15         
SKU-01826  MASS_B         Glowberry Pets  SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  1334.0         9841.3               99.08         
SKU-02282  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  535.0          5347.92              99.04         
SKU-00136  MASS_B         Titan Squad     SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  364.0          7811.84              98.97         
SKU-01815  ETAIL_C        Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  6736.0         87236.9              98.95         
SKU-00099  MASS_B         Titan Squad     FILM_TIE_IN     LAUNCH           CRITICAL   2026-10-03               1                  504.0          7398.08              98.92         
SKU-01674  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  218.0          4540.32              98.92         
SKU-00457  MASS_B         Snugglewick     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  898.0          7352.07              98.9          
SKU-02034  ETAIL_C        Glowberry Pets  SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  3789.0         15763.45             98.87         
SKU-01020  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  224.0          4169.56              98.85         
(15 rows)
```

### The constrained-allocation decision

SKUs where two or more retailers are short of the *same* pool. This is
precisely the decision Stage C2's allocator has to make and that today
happens verbally in a Monday meeting with no system of record.

```text
sku_id     brand           license_family  retailers_at_risk  sku_on_hand_units  sku_inbound_units  combined_units_at_risk  combined_revenue_at_risk_usd
---------  --------------  --------------  -----------------  -----------------  -----------------  ----------------------  ----------------------------
SKU-01632  Snugglewick     FILM_TIE_IN     2                  7934               0                  2980.0                  31087.0                     
SKU-01932  Titan Squad     FILM_TIE_IN     2                  15                 0                  982.0                   22179.0                     
SKU-00135  Snugglewick     FILM_TIE_IN     3                  32                 0                  2951.0                  19535.0                     
SKU-00136  Titan Squad     SOCIAL_TREND    2                  340                2534               774.0                   16610.0                     
SKU-00457  Snugglewick     SOCIAL_TREND    3                  1                  5127               1954.0                  16002.0                     
SKU-02079  Snugglewick     SOCIAL_TREND    2                  0                  1999               1116.0                  13887.0                     
SKU-02302  Titan Squad     EVERGREEN       2                  1706               3360               815.0                   12724.0                     
SKU-02104  Titan Squad     EVERGREEN       2                  2349               2124               989.0                   12690.0                     
SKU-01096  Titan Squad     FILM_TIE_IN     2                  5685               32                 630.0                   10728.0                     
SKU-01426  Glowberry Pets  SOCIAL_TREND    3                  8                  936                1174.0                  10612.0                     
(10 rows)
```

### Risk concentration

```text
license_family  at_risk_combinations  avg_weeks_to_stockout  units_at_risk  revenue_at_risk_usd
--------------  --------------------  ---------------------  -------------  -------------------
SOCIAL_TREND    645                   3.7                    64521.0        579004.0           
EVERGREEN       1807                  6.9                    53609.0        503546.0           
FILM_TIE_IN     564                   6.3                    32122.0        298842.0           
(3 rows)
```

```text
retailer_code  retailer_label         at_risk_combinations  revenue_at_risk_usd  avg_severity
-------------  ---------------------  --------------------  -------------------  ------------
ETAIL_C        E-tail Account C       1024                  549020.0             60.3        
MASS_A         Mass Retail Account A  997                   429178.0             60.7        
MASS_B         Mass Retail Account B  995                   403194.0             60.3        
(3 rows)
```

## 9. Lineage: Gold fact to feature table to model to forecast

```text
source                                             target                                             
-------------------------------------------------  ---------------------------------------------------
toy_demand_allocation.gold.fct_demand_forecast     NULL                                               
toy_demand_allocation.ml.demand_features_weekly    NULL                                               
NULL                                               toy_demand_allocation.gold.fct_demand_forecast     
NULL                                               toy_demand_allocation.gold.fct_demand_forecast_eval
toy_demand_allocation.gold.dim_retailer_dc         toy_demand_allocation.gold.fct_stockout_risk       
toy_demand_allocation.gold.fct_demand_forecast     toy_demand_allocation.gold.fct_stockout_risk       
toy_demand_allocation.gold.fct_inbound_eta         toy_demand_allocation.gold.fct_stockout_risk       
toy_demand_allocation.gold.fct_inventory_position  toy_demand_allocation.gold.fct_stockout_risk       
toy_demand_allocation.silver.product_master        toy_demand_allocation.gold.fct_stockout_risk       
NULL                                               toy_demand_allocation.ml.demand_features_weekly    
toy_demand_allocation.gold.dim_retailer_dc         toy_demand_allocation.ml.demand_features_weekly    
toy_demand_allocation.gold.fct_demand_weekly       toy_demand_allocation.ml.demand_features_weekly    
toy_demand_allocation.silver.product_master        toy_demand_allocation.ml.demand_features_weekly    
(13 rows)
```
