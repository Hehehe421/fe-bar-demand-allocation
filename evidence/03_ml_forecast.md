# Evidence 03 — Stage C1: demand forecast, sweep, champion/challenger

| | |
|---|---|
| Produced by | `notebooks/03_demand_forecast.ipynb` on cluster `1008-205814-qhbfm7tc` (DBR 16.4 LTS ML), then `tools/evidence_03_ml_forecast.py` reading MLflow and Unity Catalog back |
| Generated at | 2026-10-09 20:35:06 UTC |
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

The training set is assembled through a `FeatureLookup` keyed on
(`sku_id`, `retailer_code`) with `origin_week` as the timestamp lookup key, so
the point-in-time join is declared once and reused at inference instead of
being reimplemented in a scoring script. `retailer_code` is deliberately a
primary key of the feature table rather than a feature column — including it
as both makes `create_table` fail with `COLUMN_ALREADY_EXISTS`.

## 2. The sweep — leaderboard

Seven candidates across six families, one MLflow experiment, identical
metrics on an identical holdout. Pulled back out of the tracking server with
`mlflow.search_runs()`, filtered to a single `sweep_id` so the table is an
exact record of one execution rather than an accumulation across runs.

| rank | algorithm | family | wape_pct | mape_nonzero_pct | bias_pct | rmse | pinball_p50 | pinball_p90 | p90_coverage_pct | train_seconds | run_id |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lightgbm | gradient_boosting | 67.36 | 183.373 | 5.224 | 93.215 | 11.31 | 9.526 | 89.951 | 43.3 | b538cea89e7c4c768c28da6650d8ed72 |
| 2 | xgboost | gradient_boosting | 72.883 | 206.873 | 11.159 | 98.517 | 12.237 | 9.809 | 89.245 | 75.7 | 8ac9704203a8443aa19df6a888dc88ed |
| 3 | random_forest | sklearn_ensemble | 90.64 | 291.367 | 32.146 | 112.641 | 15.219 | 10.635 | 91.547 | 146.2 | 11b90f25c54543e8ac27ee22a6ca5e2a |
| 4 | ridge_linear | linear | 91.665 | 406.062 | 31.89 | 96.371 | 15.391 | 18.144 | 85.317 | 12.9 | 1890c13e7293446f81dd140bfac90dfb |
| 5 | sklearn_hist_gbm | sklearn_boosting | 92.993 | 259.888 | 26.633 | 270.228 | 15.614 | 11.324 | 90.762 | 26.1 | 2a5fc893c1334b06bce1352350d25e79 |
| 6 | seasonal_naive | baseline | 95.342 | 269.2 | 20.241 | 119.124 | 16.008 | 16.477 | 89.629 | 0.6 | 558bf29ad98e44888a01fb1e152b9f9d |
| 7 | trailing_mean_13 | statistical | 96.724 | 258.484 | 16.466 | 114.866 | 16.24 | 15.101 | 91.173 | 0.4 | 3d50b737a50a4ad28beb39006fed2a59 |

**WAPE is the headline** because it is volume-weighted and therefore stable
on intermittent series. The `mape_nonzero_pct` column shows why a per-row
MAPE average is not usable here — 183% to 406% — since it is dominated by
near-zero denominators on slow sellers. It is reported because buyers ask
for it, and the gap between the two is itself the argument.

Three findings worth stating:

1. **Gradient boosting wins decisively** — 67.4% WAPE against a 95.3%
   seasonal-naive baseline, a 28.0-point / 29.4% relative improvement.
2. **Four of seven candidates barely beat or lose to the baseline.** Ridge,
   RandomForest and sklearn's `HistGradientBoosting` all land in the 90s.
   Not every family suits this problem and the leaderboard says so.
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
EVERGREEN       20.54     1.19      92.04             547171.0      26742        
FILM_TIE_IN     55.62     36.05     96.21             433632.0      14383        
SOCIAL_TREND    110.16    -10.33    74.70             717500.0      9449         
(3 rows)
```

This is the most useful table in the stage:

* **`EVERGREEN` forecasts at 20.5% WAPE with +1.2% bias and 92.0% p90
  coverage.** On predictable demand the model is genuinely good and the
  interval is calibrated.
* **`SOCIAL_TREND` forecasts at 110.2% WAPE with 74.7% p90 coverage.** Viral
  spikes are close to unforecastable from history, *and* the upper quantile
  under-covers by 15 points precisely where cover matters most. Anyone
  claiming a single portfolio accuracy number for this business is hiding
  this row.
* **`FILM_TIE_IN` sits between, over-forecasting by 36%** — release windows
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
1        36.66     6.14      151551.0    
2        44.53     1.5       152495.0    
3        54.17     0.33      153318.0    
4        63.12     -0.34     152787.0    
5        70.32     -1.94     148362.0    
6        76.19     -0.25     142875.0    
7        76.05     1.36      130683.0    
8        78.58     5.28      121495.0    
9        79.28     9.27      113349.0    
10       80.29     10.19     110499.0    
11       80.65     15.52     105812.0    
12       79.42     15.83     105150.0    
13       77.82     15.79     109927.0    
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
       1  -                        READY      0d1665e5a8ca439ab67909ef7502ac8f  
       2  -                        READY      57bf4c0b28f44899b8f60be33fec83ae  
       3  -                        READY      46107ae8b358424fa45dabd92a175708  
       4  -                        READY      4a8938be1dae4154b98a4d82ff32d8b8  
       5  -                        READY      cc7b432b28884cf8b49df1f168a00d0e  
       6  -                        READY      c7d67e43228f4854a2f1193d7f0e8e8d  
       7  -                        READY      157ee79fb58a44228a118a5e5a906f11  
       8  -                        READY      a12abb0d2eb64303ae6cbad4f635bfba  
       9  @champion                READY      71e170aa1ae7404189a3e31807b054e6  
      10  @challenger              READY      1c248f98586e48e7abb63752e98b6fb7  
(10 versions)
```

Alias assignments — this is what every downstream consumer loads, so no
notebook, app or serving endpoint references a version number:

| Alias | Version | Algorithm | Holdout WAPE % | Resolved reference |
|---|---:|---|---:|---|
| `@champion` | 9 | lightgbm | 67.36 | `models:/toy_demand_allocation.ml.demand_forecaster@champion` |
| `@challenger` | 10 | xgboost | 72.883 | `models:/toy_demand_allocation.ml.demand_forecaster@challenger` |

Versions accumulate across notebook runs, which is the point of a registry:
earlier versions stay auditable and the aliases always resolve to the
current sweep. Promotion of the challenger is an alias move, not a re-export.

## 7. 13 weeks forward

Scored in a single pass from the last actual week, p50 and p90. The window
lands in the Q4 build, which is when a licensed-toy planner is most exposed.

```text
week_end_date  horizon  series  total_p50  total_p90  model_alias  model_version
-------------  -------  ------  ---------  ---------  -----------  -------------
2026-10-03     1        4498    203574.0   328011.0   champion     9            
2026-10-10     2        4498    228621.0   361667.0   champion     9            
2026-10-17     3        4498    248159.0   378796.0   champion     9            
2026-10-24     4        4498    275656.0   439380.0   champion     9            
2026-10-31     5        4498    285384.0   461311.0   champion     9            
2026-11-07     6        4498    298409.0   480052.0   champion     9            
2026-11-14     7        4498    298833.0   498995.0   champion     9            
2026-11-21     8        4498    291680.0   485198.0   champion     9            
2026-11-28     9        4498    268334.0   426690.0   champion     9            
2026-12-05     10       4498    243731.0   387938.0   champion     9            
2026-12-12     11       4498    218756.0   353265.0   champion     9            
2026-12-19     12       4498    193369.0   309146.0   champion     9            
2026-12-26     13       4498    173006.0   263046.0   champion     9            
(13 rows)
```

### Forecast vs actual on the holdout

```text
sku_id     retailer_code  week_end_date  horizon  actual_units  forecast_p50  forecast_p90  abs_error  license_family  lifecycle_stage
---------  -------------  -------------  -------  ------------  ------------  ------------  ---------  --------------  ---------------
SKU-02034  ETAIL_C        2026-08-01     5        5057.0        332.05        536.74        4724.95    SOCIAL_TREND    MATURE         
SKU-02034  ETAIL_C        2026-07-25     4        4328.0        382.74        610.07        3945.26    SOCIAL_TREND    MATURE         
SKU-02034  ETAIL_C        2026-08-08     6        4132.0        356.88        574.12        3775.12    SOCIAL_TREND    MATURE         
SKU-01969  MASS_B         2026-07-25     4        3972.0        3051.77       4864.34       920.23     FILM_TIE_IN     MATURE         
SKU-01089  ETAIL_C        2026-08-29     9        3686.0        262.62        417.61        3423.38    SOCIAL_TREND    MATURE         
SKU-01089  ETAIL_C        2026-08-15     7        3574.0        288.58        481.88        3285.42    SOCIAL_TREND    MATURE         
SKU-01969  MASS_B         2026-07-04     1        3208.0        3453.69       5564.81       245.69     FILM_TIE_IN     MATURE         
SKU-02034  ETAIL_C        2026-07-18     3        3177.0        388.89        593.61        2788.11    SOCIAL_TREND    MATURE         
SKU-01969  MASS_B         2026-07-11     2        3161.0        3698.1        5850.22       537.1      FILM_TIE_IN     MATURE         
SKU-01089  ETAIL_C        2026-08-22     8        2961.0        275.12        457.65        2685.88    SOCIAL_TREND    MATURE         
SKU-01815  ETAIL_C        2026-09-12     11       2772.0        171.04        276.2         2600.96    SOCIAL_TREND    MATURE         
SKU-02034  ETAIL_C        2026-08-15     7        2724.0        342.48        571.88        2381.52    SOCIAL_TREND    MATURE         
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
CRITICAL   445           1          2          44652.0        410474.0             77.8        
HIGH       439           3          4          19932.0        165746.0             69.0        
MEDIUM     1451          5          8          53416.0        505177.0             60.6        
LOW        672           9          13         11211.0        102986.0             43.2        
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
SKU-01723  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  2093.0         24569.94             99.55         
SKU-00135  MASS_A         Snugglewick     FILM_TIE_IN     LAUNCH           CRITICAL   2026-10-03               1                  1864.0         12340.32             99.5          
SKU-01417  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  994.0          11167.65             99.48         
SKU-02079  MASS_A         Snugglewick     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  643.0          8002.15              99.35         
SKU-01932  MASS_B         Titan Squad     FILM_TIE_IN     LAUNCH           CRITICAL   2026-10-03               1                  690.0          15584.08             99.23         
SKU-00920  MASS_A         Snugglewick     SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  1178.0         6278.95              99.22         
SKU-01826  MASS_B         Glowberry Pets  SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  1338.0         9873.63              99.12         
SKU-02016  MASS_A         Titan Squad     SOCIAL_TREND    LAUNCH           CRITICAL   2026-10-03               1                  256.0          4762.71              99.08         
SKU-00457  MASS_B         Snugglewick     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  915.0          7491.67              99.02         
SKU-02282  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  455.0          4549.71              99.02         
SKU-01674  MASS_A         Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  192.0          4008.15              98.95         
SKU-00099  MASS_B         Titan Squad     FILM_TIE_IN     LAUNCH           CRITICAL   2026-10-03               1                  430.0          6306.63              98.93         
SKU-00862  MASS_B         Titan Squad     SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  531.0          6218.83              98.9          
SKU-01815  ETAIL_C        Titan Squad     SOCIAL_TREND    MATURE           CRITICAL   2026-10-03               1                  805.0          10420.74             98.85         
SKU-00136  ETAIL_C        Titan Squad     SOCIAL_TREND    GROWTH           CRITICAL   2026-10-03               1                  388.0          8340.27              98.78         
(15 rows)
```

### The constrained-allocation decision

SKUs where two or more retailers are short of the *same* pool. This is
precisely the decision Stage C2's allocator has to make and that today
happens verbally in a Monday meeting with no system of record.

```text
sku_id     brand           license_family  retailers_at_risk  sku_on_hand_units  sku_inbound_units  combined_units_at_risk  combined_revenue_at_risk_usd
---------  --------------  --------------  -----------------  -----------------  -----------------  ----------------------  ----------------------------
SKU-00135  Snugglewick     FILM_TIE_IN     3                  32                 0                  3266.0                  21625.0                     
SKU-01932  Titan Squad     FILM_TIE_IN     2                  15                 0                  763.0                   17233.0                     
SKU-00457  Snugglewick     SOCIAL_TREND    3                  1                  5127               2087.0                  17089.0                     
SKU-01096  Titan Squad     FILM_TIE_IN     2                  5685               32                 891.0                   15179.0                     
SKU-01417  Titan Squad     SOCIAL_TREND    3                  275                3543               1243.0                  13966.0                     
SKU-02079  Snugglewick     SOCIAL_TREND    2                  0                  1999               1014.0                  12619.0                     
SKU-00136  Titan Squad     SOCIAL_TREND    2                  340                2534               562.0                   12068.0                     
SKU-00840  Titan Squad     FILM_TIE_IN     2                  4284               141                529.0                   11345.0                     
SKU-00257  Titan Squad     EVERGREEN       2                  2725               3111               609.0                   9736.0                      
SKU-01426  Glowberry Pets  SOCIAL_TREND    3                  8                  936                1048.0                  9480.0                      
(10 rows)
```

### Risk concentration

```text
license_family  at_risk_combinations  avg_weeks_to_stockout  units_at_risk  revenue_at_risk_usd
--------------  --------------------  ---------------------  -------------  -------------------
EVERGREEN       1810                  7.0                    50493.0        467061.0           
SOCIAL_TREND    644                   3.6                    51984.0        456476.0           
FILM_TIE_IN     553                   6.2                    26734.0        260847.0           
(3 rows)
```

```text
retailer_code  retailer_label         at_risk_combinations  revenue_at_risk_usd  avg_severity
-------------  ---------------------  --------------------  -------------------  ------------
MASS_B         Mass Retail Account B  989                   404902.0             60.7        
ETAIL_C        E-tail Account C       1023                  404807.0             60.2        
MASS_A         Mass Retail Account A  995                   374675.0             60.5        
(3 rows)
```

## 9. Lineage: Gold fact to feature table to model to forecast

```text
source                                           target                                         
-----------------------------------------------  -----------------------------------------------
toy_demand_allocation.ml.demand_features_weekly  NULL                                           
NULL                                             toy_demand_allocation.ml.demand_features_weekly
toy_demand_allocation.gold.dim_retailer_dc       toy_demand_allocation.ml.demand_features_weekly
toy_demand_allocation.gold.fct_demand_weekly     toy_demand_allocation.ml.demand_features_weekly
toy_demand_allocation.silver.product_master      toy_demand_allocation.ml.demand_features_weekly
(5 rows)
```
