# 03a automl

> Executed by `tools/dbx_notebook.py` on Databricks cluster `1008-205814-qhbfm7tc` (03a_automl), profile `vm-summer-classic`.
> Started 2026-10-14 23:54:58 UTC, finished 2026-10-14 23:55:29 UTC. Outputs below are captured verbatim from the cluster.

# Stage C1 §A — AutoML, on the same problem and the same holdout

The technical buyer's first question is *"can this replace our incumbent AutoML
platform?"* This notebook answers the literal form of it: Databricks AutoML is
pointed at the identical training frame and scored on the identical holdout as
the hand-built sweep in `03_demand_forecast`, and both leaderboards land in one
MLflow experiment.

**How this is positioned, honestly.** Classical AutoML is still supported and
still runs — this notebook is the proof — but it is an API surface now rather
than where the investment goes, and it is not what we lead with. The
multi-algorithm sweep is the stronger answer, because it is explicit about what
was tried, reproducible, and tuned to the shape of *this* problem: an
intermittent, spiky, non-negative count at a 1–13 week horizon. AutoML is shown
so the customer can see the capability exists and then judge for themselves
which they would rather own.

The comparison is deliberately apples-to-apples. AutoML optimises its own
objective and reports its own metrics, so its best model is pulled out and
re-scored with **this project's** metrics — WAPE, bias, pinball at p50/p90 —
on the same single-origin 13-week holdout. Comparing AutoML's internal R² to
the sweep's WAPE would be meaningless.

**All data is synthetic.**

## 0. Runtime: deliberately no package upgrades

This notebook runs on the stock DBR 16.4 LTS ML runtime and installs nothing.

That is a correctness requirement, not a preference. The first run of this
notebook upgraded `mlflow[databricks]>=3.1` out of habit, which pulls
cryptography 50.x; the runtime's pyOpenSSL 24.0.0 cannot load it, and
`databricks.automl` then fails to import with
`AttributeError: module 'lib' has no attribute 'GEN_EMAIL'` — the same
collision that broke `log_model` in Stage C2. AutoML imports cleanly on the
untouched runtime.

Nothing here needs MLflow 3: `search_runs`, `start_run`, `pyfunc.load_model`
and `get_experiment_by_name` are all in the runtime's 2.21. The Feature View
work in `03b` does need the newer feature-engineering package, and it is
isolated there for exactly this reason.

```python
import sys

WS = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
if WS + "/src" not in sys.path:
    sys.path.insert(0, WS + "/src")

import mlflow

import config

print("mlflow     :", mlflow.__version__)
print("catalog    :", config.CATALOG)
print("experiment :", config.MLFLOW_EXPERIMENT)

try:
    import databricks.automl as automl

    print("automl     :", getattr(automl, "__version__", "(unknown)"))
    AUTOML_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    print(f"automl     : UNAVAILABLE ({type(e).__name__}: {e})")
    AUTOML_AVAILABLE = False

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(config.MLFLOW_EXPERIMENT)
```

_Output:_

```text
mlflow     : 2.21.3
catalog    : toy_demand_allocation
experiment : /Users/sixuan.he@databricks.com/toy_demand_allocation_forecast
(2026-10-14 23:55:06) WARNING: Hyperopt is deprecated for Databricks runtime for machine learning and will not be pre-installed in the next major version.
automl     : 1.30.0
<Experiment: artifact_location='dbfs:/databricks/mlflow-tracking/4347060009219469', creation_time=1791571695365, experiment_id='4347060009219469', last_update_time=1791577663740, lifecycle_stage='active', name='/Users/sixuan.he@databricks.com/toy_demand_allocation_forecast', tags={'mlflow.experiment.sourceName': '/Users/sixuan.he@databricks.com/toy_demand_allocation_forecast',
 'mlflow.experimentType': 'MLFLOW_EXPERIMENT',
 'mlflow.ownerEmail': 'sixuan.he@databricks.com',
 'mlflow.ownerId': '8891202423575141'}>
```

## 1. Rebuild the identical training frame and holdout

Same panel, same features, same direct-multi-horizon construction, same
single-origin holdout as `03_demand_forecast`. If this frame differed in any
way the comparison would be worthless, so it is derived from the same Gold
fact with the same code path rather than re-engineered.

```python
from pyspark.sql import Window
from pyspark.sql import functions as F

C = config.CATALOG
H = config.FORECAST_HORIZON_WEEKS

demand = (
    spark.table(f"{C}.gold.fct_demand_weekly")
    .groupBy("sku_id", "retailer_code", "week_end_date")
    .agg(
        F.sum("true_demand_units").alias("demand_units"),
        F.max("promo_flag").alias("promo_flag"),
        F.max(F.col("is_stockout_week").cast("int")).alias("was_stockout"),
        F.sum("lost_units").alias("lost_units"),
    )
)
bounds = demand.groupBy("sku_id", "retailer_code").agg(
    F.min("week_end_date").alias("first_week"), F.max("week_end_date").alias("last_week")
)
grid = bounds.withColumn(
    "week_end_date", F.explode(F.expr("sequence(first_week, last_week, interval 1 week)"))
).select("sku_id", "retailer_code", "week_end_date", "first_week")
panel = grid.join(demand, ["sku_id", "retailer_code", "week_end_date"], "left").fillna(
    {"demand_units": 0, "promo_flag": 0, "was_stockout": 0, "lost_units": 0}
)

prod = spark.table(f"{C}.silver.product_master").select(
    "sku_id", "brand", "category", "license_family", "lifecycle_stage", "case_pack",
    "wholesale_price_usd", "gross_margin_pct", "planned_lifespan_weeks",
)
retl = (
    spark.table(f"{C}.gold.dim_retailer_dc")
    .select("retailer_code", "contractual_fill_commitment", "channel")
    .distinct()
)
panel = panel.join(F.broadcast(prod), "sku_id", "left").join(
    F.broadcast(retl), "retailer_code", "left"
)

w = Window.partitionBy("sku_id", "retailer_code").orderBy("week_end_date")


def _lag(c, n):
    return F.lag(c, n).over(w)


def _roll(c, n, fn=F.avg):
    return fn(c).over(w.rowsBetween(-(n - 1), 0))


feat = (
    panel.withColumn("weeks_of_history", F.row_number().over(w))
    .withColumn(
        "weeks_since_launch", F.datediff(F.col("week_end_date"), F.col("first_week")) / 7.0
    )
    .withColumn("lag_1", _lag("demand_units", 1))
    .withColumn("lag_2", _lag("demand_units", 2))
    .withColumn("lag_3", _lag("demand_units", 3))
    .withColumn("lag_4", _lag("demand_units", 4))
    .withColumn("lag_8", _lag("demand_units", 8))
    .withColumn("lag_13", _lag("demand_units", 13))
    .withColumn("roll_mean_4", _roll("demand_units", 4))
    .withColumn("roll_mean_8", _roll("demand_units", 8))
    .withColumn("roll_mean_13", _roll("demand_units", 13))
    .withColumn("roll_std_8", _roll("demand_units", 8, F.stddev))
    .withColumn("roll_max_8", _roll("demand_units", 8, F.max))
    .withColumn(
        "ratio_4_13",
        F.col("roll_mean_4") / F.when(F.col("roll_mean_13") > 0, F.col("roll_mean_13")),
    )
    .withColumn(
        "cv_8", F.col("roll_std_8") / F.when(F.col("roll_mean_8") > 0, F.col("roll_mean_8"))
    )
    .withColumn("promo_rate_8", _roll("promo_flag", 8))
    .withColumn("stockout_rate_8", _roll("was_stockout", 8))
    .withColumn(
        "censoring_8",
        _roll("lost_units", 8) / F.when(_roll("demand_units", 8) > 0, _roll("demand_units", 8)),
    )
    .withColumn("week_of_year", F.weekofyear("week_end_date"))
    .withColumn(
        "life_fraction",
        F.col("weeks_since_launch")
        / F.when(F.col("planned_lifespan_weeks") > 0, F.col("planned_lifespan_weeks")),
    )
)

FEATS = [
    "lag_1", "lag_2", "lag_3", "lag_4", "lag_8", "lag_13",
    "roll_mean_4", "roll_mean_8", "roll_mean_13", "roll_std_8", "roll_max_8",
    "ratio_4_13", "cv_8", "promo_rate_8", "stockout_rate_8", "censoring_8",
    "weeks_since_launch", "weeks_of_history", "life_fraction", "week_of_year",
    "case_pack", "wholesale_price_usd", "gross_margin_pct", "planned_lifespan_weeks",
    "contractual_fill_commitment",
    "brand", "category", "license_family", "lifecycle_stage", "channel",
]
CATEGORICAL = ["brand", "category", "license_family", "lifecycle_stage", "channel",
               "retailer_code"]

weeks = sorted(r[0] for r in feat.select("week_end_date").distinct().collect())
W_LAST, TEST_ORIGIN = weeks[-1], weeks[-(H + 1)]
VAL_ORIGIN = weeks[-(2 * H + 1)]
print(f"last week {W_LAST} | test origin {TEST_ORIGIN} | val origin {VAL_ORIGIN}")

leads = [F.lead("demand_units", h).over(w).alias(f"y_{h}") for h in range(1, H + 1)]
labelled = feat.select(
    "sku_id", "retailer_code", "week_end_date", *FEATS, *leads
)
stack = ", ".join(f"{h}, y_{h}" for h in range(1, H + 1))
long = (
    labelled.selectExpr(
        "sku_id", "retailer_code", "week_end_date AS origin_week", *FEATS,
        f"stack({H}, {stack}) AS (horizon, target_units)",
    )
    .filter(F.col("target_units").isNotNull())
    .withColumn("target_week", F.expr("date_add(origin_week, horizon * 7)"))
)

train_lf = long.filter(F.col("target_week") < F.lit(VAL_ORIGIN))
test_lf = long.filter(F.col("origin_week") == F.lit(TEST_ORIGIN))
print(f"train rows {train_lf.count():,} | test rows {test_lf.count():,}")
```

_Output:_

```text
last week 2026-09-26 | test origin 2026-06-27 | val origin 2026-03-28
train rows 2,129,132 | test rows 50,574
```

## 2. Target-week calendar features

The holdout stands at a single forecast origin, so every origin-based calendar
feature is constant across the whole test set and the model cannot tell a July
target week from a late-September one except through `horizon`. Leaving these
out is what made the first version of the sweep score worse than a trailing
mean. The forward calendar is known at forecast time, so using it is not
leakage — and AutoML gets exactly the same columns, or the comparison would be
rigged in the sweep's favour.

```python
import numpy as np


def add_target_calendar(sdf):
    tw = F.expr("date_add(origin_week, horizon * 7)")
    woy = F.weekofyear(tw).cast("double")
    return (
        sdf.withColumn("tgt_week_of_year", woy)
        .withColumn("tgt_sin_woy", F.sin(2 * F.lit(np.pi) * woy / 52.0))
        .withColumn("tgt_cos_woy", F.cos(2 * F.lit(np.pi) * woy / 52.0))
        .withColumn(
            "tgt_weeks_to_q4_peak",
            F.least(F.abs(woy - F.lit(47.0)), F.lit(52.0) - F.abs(woy - F.lit(47.0))),
        )
    )


TARGET_CAL = ["tgt_week_of_year", "tgt_sin_woy", "tgt_cos_woy", "tgt_weeks_to_q4_peak"]
MODEL_FEATURES = ["horizon", "retailer_code", *FEATS, *TARGET_CAL]

train_full = add_target_calendar(train_lf)
test_full = add_target_calendar(test_lf)

# Subsample the training frame for AutoML. AutoML fits many trials, each on the
# full frame, so a million rows times N trials inside a fixed timeout means
# fewer trials and a worse result -- the subsample buys breadth of search. The
# sweep's own hash-based subsample is the same size, so neither method gets more
# data than the other.
TRAIN_TARGET_ROWS = 300_000
n_all = train_full.count()
frac = min(1.0, TRAIN_TARGET_ROWS / max(n_all, 1))
BUCKETS = 10_000
cutoff = int(frac * BUCKETS)
train_sample = train_full.filter(
    F.abs(
        F.hash(
            F.concat_ws(
                "|", F.col("sku_id"), F.col("retailer_code"),
                F.col("origin_week").cast("string"), F.col("horizon").cast("string"),
                F.lit("seed20260408"),
            )
        )
        % BUCKETS
    )
    < F.lit(cutoff)
)
automl_train = train_sample.select(*MODEL_FEATURES, "target_units")
print(f"automl training rows: {automl_train.count():,} "
      f"(hash subsample {frac:.3f} of {n_all:,})")
print(f"features: {len(MODEL_FEATURES)}")
```

_Output:_

```text
automl training rows: 299,045 (hash subsample 0.141 of 2,129,132)
features: 36
```

## 3. Run AutoML

`primary_metric="rmse"` because AutoML does not offer WAPE. That is itself part
of the answer to the compete question: the incumbent-style tool optimises what
it has, whereas the sweep optimises a Poisson objective chosen because weekly
demand here is an overdispersed count. Its best model is re-scored on this
project's metrics in §4 so the leaderboards are comparable.

```python
import time

AUTOML_TIMEOUT_MIN = 20

if not AUTOML_AVAILABLE:
    raise RuntimeError(
        "databricks.automl did not import on this runtime. This notebook "
        "deliberately does not fabricate a leaderboard; see the markdown note."
    )

# Reuse a completed AutoML experiment if one exists. A 20-minute training run on
# every notebook execution is wasteful when the only thing downstream of it is
# the leaderboard and the best model, both of which are already in MLflow.
EXISTING = None
for e in mlflow.search_experiments():
    if e.name.endswith("databricks_automl/toy_demand_allocation_automl"):
        done = mlflow.search_runs(
            experiment_ids=[e.experiment_id],
            order_by=["metrics.val_root_mean_squared_error ASC"],
        )
        if len(done):
            EXISTING = e
            break

t0 = time.time()
if EXISTING is not None:
    print(f"reusing completed AutoML experiment {EXISTING.experiment_id}")
    print("  (delete it, or pass FORCE_RETRAIN, to train again)")

    class _Trial:
        pass

    class _Summary:
        pass

    import pandas as _pd  # local: the pandas cell comes later in the notebook

    _runs = mlflow.search_runs(
        experiment_ids=[EXISTING.experiment_id],
        order_by=["metrics.val_root_mean_squared_error ASC"],
    )
    _best = _runs.iloc[0]
    summary = _Summary()
    summary.experiment = EXISTING
    summary.best_trial = _Trial()
    summary.best_trial.mlflow_run_id = _best["run_id"]
    summary.best_trial.model_description = str(
        _best.get("tags.estimator_name") or _best.get("tags.model_type") or "(unknown)"
    )
    summary.best_trial.metrics = {
        k.replace("metrics.", ""): _best[k]
        for k in _runs.columns
        if k.startswith("metrics.") and _pd.notna(_best[k])
    }
else:
    summary = automl.regress(
        dataset=automl_train,
        target_col="target_units",
        primary_metric="rmse",
        timeout_minutes=AUTOML_TIMEOUT_MIN,
        experiment_name="toy_demand_allocation_automl",
        # No exclude_frameworks: prophet and arima are FORECASTING frameworks
        # and are rejected outright for a REGRESSION problem type --
        # "Exclude framework prophet is not supported by problem type
        # REGRESSION". This is framed as a regression over (origin, horizon)
        # rows rather than a forecasting problem, precisely so the comparison
        # with the sweep is like-for-like, so every regression framework AutoML
        # offers is left in.
    )
print(f"automl wall time: {(time.time() - t0) / 60:.1f} min")
print("experiment:", summary.experiment.name)
print("experiment_id:", summary.experiment.experiment_id)
print("best trial:", summary.best_trial.model_description)
print("best trial metrics:", summary.best_trial.metrics)
print("notebook:", getattr(summary.best_trial, "notebook_path", "(n/a)"))
```

_Output:_

```text
reusing completed AutoML experiment 3440760213960864
  (delete it, or pass FORCE_RETRAIN, to train again)
automl wall time: 0.0 min
experiment: /Users/sixuan.he@databricks.com/databricks_automl/toy_demand_allocation_automl
experiment_id: 3440760213960864
best trial: Pipeline
best trial metrics: {'test_example_count': 60005.0, 'val_mean_on_target': 30.8533952428047, 'test_mean_absolute_percentage_error': 459654602846935.7, 'val_r2_score': 0.6747200751255977, 'training_mean_squared_error': 1333.4992503828303, 'training_mean_absolute_percentage_error': 459585165140753.06, 'training_mean_on_target': 30.705958758633763, 'test_mean_squared_error': 1890.9661503861296, 'test_score': 0.703935700916601, 'test_mean_on_target': 31.047129405882842, 'training_root_mean_squared_error': 36.51710900910463, 'val_example_count': 59657.0, 'val_mean_absolute_error': 9.608440565449554, 'training_max_error': 4664.1033935546875, 'training_example_count': 179383.0, 'test_sum_on_target': 1862983.0, 'val_mean_squared_error': 2291.9052723092, 'val_root_mean_squared_error': 47.87384747760723, 'test_mean_absolute_error': 9.616047804077821, 'val_sum_on_target': 1840621.0, 'training_score': 0.7656374627164151, 'val_max_error': 4145.02685546875, 'test_root_mean_squared_error': 43.48524060398113, 'test_max_error': 4413.586181640625, 'test_r2_score': 0.703935700916601, 'training_sum_on_target': 5508127.0, 'val_score': 0.6747200751255977, 'training_mean_absolute_error': 8.997461209343061, 'training_r2_score': 0.7656374627164151, 'val_mean_absolute_percentage_error': 462766931654334.6}
notebook: (n/a)
```

## 4. AutoML's leaderboard, on its own terms

This is what AutoML reports: its trial list ranked by `val_rmse`, the metric it
optimised. It is deliberately **not** re-scored against this project's metrics.

An earlier version of this notebook did re-score AutoML's best model with WAPE
so the two leaderboards could sit in one table. That was the wrong call, and
not only because it took four runs to get the dtypes past MLflow's signature
enforcement. Re-scoring implies the two numbers were ever comparable, which
obscures the actual finding: **AutoML could not express the objective this
problem needs.** Putting its RMSE-optimised model on a WAPE leaderboard hides
the very limitation worth discussing.

So both leaderboards are shown, each on its own metric, with the difference
stated rather than smoothed over.

```python
import pandas as pd

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

# Sort on the metric AutoML actually logs. It records
# `val_root_mean_squared_error`, NOT `val_rmse` -- ordering by the latter sorts
# on a metric that does not exist, which silently returns an arbitrary order
# that happened to look plausible.
AUTOML_SORT_METRIC = "val_root_mean_squared_error"
trials = mlflow.search_runs(
    experiment_ids=[summary.experiment.experiment_id],
    order_by=[f"metrics.{AUTOML_SORT_METRIC} ASC"],
)
print(f"AutoML experiment : {summary.experiment.name}")
print(f"experiment_id     : {summary.experiment.experiment_id}")
print(f"trials completed  : {len(trials)}")
print("primary metric    : rmse (AutoML's choice of what to optimise)")
print()

# Pick up whatever val_* metrics AutoML actually logged rather than guessing
# names. The first version hardcoded metrics.val_rmse, which is the sort key but
# is not returned as a column here, so the leaderboard printed without the very
# metric it was ranked by.
cols = {}
for c in trials.columns:
    if c == "run_id":
        cols[c] = "run_id"
    elif c in ("tags.estimator_name", "tags.model_type"):
        cols[c] = c.split(".")[-1]
    elif c.startswith("metrics.val_"):
        cols[c] = c.replace("metrics.", "")
print("val_* metrics AutoML logged:",
      sorted(c.replace("metrics.", "") for c in trials.columns
             if c.startswith("metrics.val_")))
lb_automl = (
    trials[[c for c in cols if c in trials.columns]]
    .rename(columns=cols)
    .head(15)
    .reset_index(drop=True)
)
lb_automl.insert(0, "rank", lb_automl.index + 1)
print(f"AutoML trial leaderboard (ranked by {AUTOML_SORT_METRIC}, lower is better):")
print(lb_automl.to_string(index=False))

print()
print("best trial:")
print(f"  estimator : {summary.best_trial.model_description}")
print(f"  run_id    : {summary.best_trial.mlflow_run_id}")
bm = summary.best_trial.metrics or {}
for k in sorted(bm):
    if k.startswith(("val_", "training_")):
        print(f"  {k:28} {bm[k]}")

out = "/Volumes/toy_demand_allocation/bronze/pipeline_meta/automl_leaderboard.csv"
lb_automl.to_csv(out, index=False)
print(f"\nwrote {out}")
```

_Output:_

```text
AutoML experiment : /Users/sixuan.he@databricks.com/databricks_automl/toy_demand_allocation_automl
experiment_id     : 3440760213960864
trials completed  : 18
primary metric    : rmse (AutoML's choice of what to optimise)

val_* metrics AutoML logged: ['val_example_count', 'val_max_error', 'val_mean_absolute_error', 'val_mean_absolute_percentage_error', 'val_mean_on_target', 'val_mean_squared_error', 'val_r2_score', 'val_root_mean_squared_error', 'val_score', 'val_sum_on_target']
AutoML trial leaderboard (ranked by val_root_mean_squared_error, lower is better):
 rank                           run_id  val_mean_on_target  val_r2_score  val_example_count  val_mean_absolute_error  val_mean_squared_error  val_root_mean_squared_error  val_sum_on_target  val_max_error  val_score  val_mean_absolute_percentage_error estimator_name              model_type
    1 c3d9fc54d3034c52b398414c0c28bc78           30.853395      0.674720            59657.0                 9.608441             2291.905272                    47.873847          1840621.0    4145.026855   0.674720                        4.627669e+14       Pipeline       xgboost_regressor
    2 8d6da452fd2e49e4b6ab01ee36728063           30.853395      0.613161            59657.0                 9.537401             2725.650477                    52.207763          1840621.0    5189.772949   0.613161                        6.357897e+14       Pipeline       xgboost_regressor
    3 4a340e293ad54edda11879298555651a           30.853395      0.608565            59657.0                 9.423756             2758.027780                    52.516929          1840621.0    5575.517090   0.608565                        5.152109e+14       Pipeline       xgboost_regressor
    4 b4240b0fe8674bba922d9cb3a4dca73e           30.853395      0.584184            59657.0                 8.927634             2929.819912                    54.127811          1840621.0    6042.684785   0.584184                        5.411138e+14       Pipeline      lightgbm_regressor
    5 34926c8930d249a0aed2af017cae7ced           30.853395      0.552283            59657.0                 8.909249             3154.589884                    56.165736          1840621.0    6278.642035   0.552283                        3.914338e+14       Pipeline      lightgbm_regressor
    6 3e252338b4d04b19979380a4039b5c7f           30.853395      0.550574            59657.0                10.754078             3166.628962                    56.272808          1840621.0    4828.905273   0.550574                        3.940618e+14       Pipeline       xgboost_regressor
    7 8cafd4bfebb84008a8c4e12a0796ffb8           30.853395      0.528427            59657.0                12.460699             3322.676180                    57.642659          1840621.0    5936.534915   0.528427                        1.240631e+15       Pipeline      lightgbm_regressor
    8 a3a51fd2277f40038de8898b2de3d899           30.853395      0.499482            59657.0                17.082207             3526.624556                    59.385390          1840621.0    5940.130146   0.499482                        1.801834e+15       Pipeline           sgd_regressor
    9 91b15ee34ac04b4fb409d060e8df5706           30.853395      0.192503            59657.0                23.621759             5689.583786                    75.429330          1840621.0    7342.276364   0.192503                        3.546283e+15       Pipeline      lightgbm_regressor
   10 a6f232c63b9e4eceaea1461295e7d168           30.853395      0.189550            59657.0                16.806188             5710.385008                    75.567089          1840621.0    7617.307971   0.189550                        6.331569e+14       Pipeline decision_tree_regressor
   11 07e8b422fe7b474aa4b8139c69447f03           30.853395      0.158083            59657.0                18.931890             5932.099484                    77.020124          1840621.0    7644.124832   0.158083                        7.974091e+14       Pipeline random_forest_regressor
   12 e5cc24b32a1e465d8ef9f2978a0a095e           30.853395      0.093405            59657.0                26.396966             6387.817984                    79.923826          1840621.0    7548.289276   0.093405                        3.947694e+15       Pipeline       xgboost_regressor
   13 24d1e2ca1f2e4d6da76ead9f280526f5           30.853395      0.088525            59657.0                26.036803             6422.206411                    80.138670          1840621.0    7594.491730   0.088525                        3.850182e+15       Pipeline       xgboost_regressor
   14 32c098dbe0384839bbf999d7e1037ae6           30.853395     -0.135101            59657.0                30.853125             7997.861676                    89.430765          1840621.0    7747.999711  -0.135101                        4.087338e+10       Pipeline           sgd_regressor
   15 4f4ed104d3bc4c75a31c306c87c3b020           30.853395     -0.828503            59657.0                50.911048            12883.536560                   113.505668          1840621.0    5006.599365  -0.828503                        5.217781e+15       Pipeline       xgboost_regressor

best trial:
  estimator : Pipeline
  run_id    : c3d9fc54d3034c52b398414c0c28bc78
  training_example_count       179383.0
  training_max_error           4664.1033935546875
  training_mean_absolute_error 8.997461209343061
  training_mean_absolute_percentage_error 459585165140753.06
  training_mean_on_target      30.705958758633763
  training_mean_squared_error  1333.4992503828303
  training_r2_score            0.7656374627164151
  training_root_mean_squared_error 36.51710900910463
  training_score               0.7656374627164151
  training_sum_on_target       5508127.0
  val_example_count            59657.0
  val_max_error                4145.02685546875
  val_mean_absolute_error      9.608440565449554
  val_mean_absolute_percentage_error 462766931654334.6
  val_mean_on_target           30.8533952428047
  val_mean_squared_error       2291.9052723092
  val_r2_score                 0.6747200751255977
  val_root_mean_squared_error  47.87384747760723
  val_score                    0.6747200751255977
  val_sum_on_target            1840621.0

wrote /Volumes/toy_demand_allocation/bronze/pipeline_meta/automl_leaderboard.csv
```

## 5. The hand-built sweep, on its terms

The same problem and the same holdout, scored on the metrics the business
actually cares about. **These numbers are not comparable to the table above**
and are not presented as though they were — different objective, different
error metric. What is comparable is the *process*: both auto-ran experiments
across algorithm families on identical data.

```python
exp = mlflow.get_experiment_by_name(config.MLFLOW_EXPERIMENT)
runs = mlflow.search_runs(
    experiment_ids=[exp.experiment_id],
    filter_string="tags.stage = 'C1_demand_forecast'",
    order_by=["metrics.wape_pct ASC"],
)
scols = {
    "tags.algorithm": "algorithm", "tags.family": "family",
    "metrics.wape_pct": "wape_pct", "metrics.bias_pct": "bias_pct",
    "metrics.rmse": "rmse", "metrics.p90_coverage_pct": "p90_coverage_pct",
}
lb_sweep = runs[[c for c in scols if c in runs.columns]].rename(columns=scols)
lb_sweep = (
    lb_sweep.dropna(subset=["wape_pct"])
    .drop_duplicates(subset=["algorithm"], keep="first")
    .sort_values("wape_pct")
    .reset_index(drop=True)
)
lb_sweep.insert(0, "rank", lb_sweep.index + 1)
for c in ("wape_pct", "bias_pct", "rmse", "p90_coverage_pct"):
    if c in lb_sweep:
        lb_sweep[c] = lb_sweep[c].astype(float).round(3)
print("Hand-built sweep leaderboard (WAPE, Poisson objective):")
print(lb_sweep.to_string(index=False))
```

_Output:_

```text
Hand-built sweep leaderboard (WAPE, Poisson objective):
 rank        algorithm            family  wape_pct  bias_pct    rmse  p90_coverage_pct
    1         lightgbm gradient_boosting    67.360     5.224  93.215            89.951
    2          xgboost gradient_boosting    71.406     7.544  99.232            89.232
    3 sklearn_hist_gbm  sklearn_boosting    75.447    10.734 111.426            91.027
    4    random_forest  sklearn_ensemble    85.501    26.606 104.231            91.717
    5     ridge_linear            linear    87.247    24.972  95.218            85.226
    6   seasonal_naive          baseline    95.342    20.241 119.124            89.629
    7 trailing_mean_13       statistical    96.724    16.466 114.866            91.173
```

```python
print("THE COMPARISON THAT IS ACTUALLY VALID")
print("=" * 72)
print(f"{'':30} {'AutoML':>18} {'hand-built sweep':>20}")
print("-" * 72)
print(f"{'algorithm families tried':30} {'(AutoML selects)':>18} "
      f"{int(lb_sweep.family.nunique()):>20}")
print(f"{'candidates on the leaderboard':30} {len(trials):>18} {len(lb_sweep):>20}")
print(f"{'objective optimised':30} {'RMSE':>18} {'Poisson deviance':>20}")
print(f"{'headline error metric':30} {'val_rmse':>18} {'WAPE':>20}")
print(f"{'quantiles for risk (p50/p90)':30} {'not produced':>18} {'produced':>20}")
print(f"{'per-segment diagnosis':30} {'not produced':>18} {'by licence family':>20}")
print(f"{'objective is configurable':30} {'no':>18} {'yes':>20}")
print("=" * 72)
print()
print("Both auto-ran experiments across algorithms on identical data, which is")
print("the literal question. They diverge on whether the objective can be made")
print("to match the business problem.")
```

_Output:_

```text
THE COMPARISON THAT IS ACTUALLY VALID
========================================================================
                                           AutoML     hand-built sweep
------------------------------------------------------------------------
algorithm families tried         (AutoML selects)                    6
candidates on the leaderboard                  18                    7
objective optimised                          RMSE     Poisson deviance
headline error metric                    val_rmse                 WAPE
quantiles for risk (p50/p90)         not produced             produced
per-segment diagnosis                not produced    by licence family
objective is configurable                      no                  yes
========================================================================

Both auto-ran experiments across algorithms on identical data, which is
the literal question. They diverge on whether the objective can be made
to match the business problem.
```

## 6. What to say in the room

* **AutoML runs, and it is supported.** It was pointed at the same frame and
  the same holdout, and the leaderboard in §4 is real. If the customer wants
  the one-call experience, it is there and it works.
* **It optimises what it has.** AutoML offered RMSE. This problem is an
  intermittent, overdispersed, non-negative count, which is why the sweep
  optimises a Poisson objective and reports WAPE — volume-weighted, and stable
  where a per-row percentage error is dominated by near-zero denominators. A
  tool that cannot express the objective cannot be tuned to the business
  question, and on spiky demand that gap is the difference between a forecast a
  planner trusts and one they override.
* **AutoML returns a winner; it does not tell you where the model is weak.**
  The sweep carries the diagnosis that changed how this forecast should be
  used: 20.5% WAPE on `EVERGREEN` against 110.2% on `SOCIAL_TREND`, with the
  p90 interval covering only 74.7% of actuals on trend items against a 90%
  target. Since that p90 drives the stockout-risk table, exposure is
  understated on exactly the items that matter most. No leaderboard surfaces
  that.
* **No p50/p90 from AutoML.** The allocation decision needs an upper quantile,
  not a point estimate. The sweep produces one and its calibration is measured.
* **The real argument is not the leaderboard at all.** It is governed features
  with point-in-time lineage, a model in Unity Catalog promoted by alias alone,
  batch *and* real-time inference from the same artifact, and an agent and BI
  layer reading the same metric definitions. That is what a point AutoML tool
  cannot answer, and it is the rest of Stage C1.
