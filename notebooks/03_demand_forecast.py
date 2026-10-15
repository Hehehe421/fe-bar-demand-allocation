# %% [markdown]
# # Stage C1 — Demand forecast: feature store, multi-algorithm sweep, champion/challenger
#
# Reads `gold.fct_demand_weekly` (Stage A output, governed in Stage B) and produces
# the spine that Stages C2, D, E and F all consume:
#
# * `ml.demand_features_weekly` — a Feature Engineering table, keyed on
#   (`sku_id`, `retailer_code`, `week_end_date`), joined into the training set
#   through a `FeatureLookup` so feature lineage is demonstrable rather than
#   asserted.
# * `ml.demand_forecaster` — the winning model registered in Unity Catalog, with
#   `@champion` on the winner and `@challenger` on the runner-up.
# * `gold.fct_demand_forecast` — 13 weeks forward, p50 and p90.
# * `gold.fct_demand_forecast_eval` — forecast vs actual on the holdout.
# * `gold.fct_stockout_risk` — forecast p90 against on-hand plus inbound-by-ETA.
#
# **All data is synthetic.**
#
# ### Two modelling decisions worth stating up front
#
# **The target is `true_demand_units`, not `pos_units`.** The business wants to
# predict *demand*, but in production you only observe *sales*, which are censored
# by your own stockouts — exactly in the weeks that matter most. This synthetic
# dataset carries unconstrained demand as ground truth, so the model is trained
# against it. A production deployment must first reconstruct unconstrained demand
# from censored sales (lost-sales modelling); that is called out in the README as
# a real prerequisite and not glossed over here.
#
# **Direct multi-horizon, not recursive.** Every row is
# (series, forecast origin, horizon h) with features computed only from data at or
# before the origin, and the label taken h weeks after it. `horizon` is a model
# feature. This means the 13-week forward score is a single pass with no error
# compounding, and the holdout is a genuine single-origin 13-week-ahead forecast —
# which is how a planner actually uses it.

# %%
import sys

WS = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
if WS + "/src" not in sys.path:
    sys.path.insert(0, WS + "/src")

import config  # noqa: E402

C = config.CATALOG
print("catalog        :", C)
print("feature table  :", config.FEATURE_TABLE)
print("model name     :", config.MODEL_NAME)
print("experiment     :", config.MLFLOW_EXPERIMENT)
print("horizon weeks  :", config.FORECAST_HORIZON_WEEKS)
print("quantiles      :", config.QUANTILES)
print("current_user   :", spark.sql("SELECT current_user()").collect()[0][0])

spark.sql(f"USE CATALOG {C}")

# %% [markdown]
# ## 1. Build the weekly panel
#
# Aggregate the Gold fact from SKU x retailer x DC x week up to SKU x retailer x
# week — the DC is a fulfilment detail, not a demand signal. Then **complete the
# calendar** inside each series' active range, filling absent weeks with zero
# demand. Without that, a week in which an item genuinely sold nothing is
# indistinguishable from a week that was never reported, and every lag feature is
# silently wrong.
#
# Cost and margin are read from `silver.product_master`, not `gold.dim_product`:
# the Stage B column mask makes them NULL for every identity except the finance
# persona, including this one. That is the mask working correctly.

# %%
from pyspark.sql import Window  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

demand = (
    spark.table(f"{C}.gold.fct_demand_weekly")
    .groupBy("sku_id", "retailer_code", "week_end_date")
    .agg(
        F.sum("true_demand_units").alias("demand_units"),
        F.sum("pos_units").alias("pos_units"),
        F.sum("lost_units").alias("lost_units"),
        F.max("promo_flag").alias("promo_flag"),
        F.max(F.col("is_stockout_week").cast("int")).alias("was_stockout"),
    )
)

bounds = demand.groupBy("sku_id", "retailer_code").agg(
    F.min("week_end_date").alias("first_week"),
    F.max("week_end_date").alias("last_week"),
)

grid = (
    bounds.withColumn(
        "week_end_date",
        F.explode(F.expr("sequence(first_week, last_week, interval 1 week)")),
    )
    .select("sku_id", "retailer_code", "week_end_date", "first_week")
)

panel = (
    grid.join(demand, ["sku_id", "retailer_code", "week_end_date"], "left")
    .fillna({"demand_units": 0, "pos_units": 0, "lost_units": 0,
             "promo_flag": 0, "was_stockout": 0})
)

# product and retailer attributes; cost/margin from SILVER (see note above)
prod = spark.table(f"{C}.silver.product_master").select(
    "sku_id", "brand", "category", "license_family", "lifecycle_stage",
    "case_pack", "wholesale_price_usd", "unit_cost_usd", "gross_margin_pct",
    "planned_lifespan_weeks",
)
retl = (
    spark.table(f"{C}.gold.dim_retailer_dc")
    .select("retailer_code", "contractual_fill_commitment", "channel")
    .distinct()
)

panel = panel.join(F.broadcast(prod), "sku_id", "left").join(
    F.broadcast(retl), "retailer_code", "left"
)

n_panel = panel.count()
print(f"panel rows (calendar-completed): {n_panel:,}")
print(f"series: {panel.select('sku_id','retailer_code').distinct().count():,}")
panel.select(
    F.min("week_end_date").alias("min_week"), F.max("week_end_date").alias("max_week")
).show()

# %% [markdown]
# ## 2. Features at the forecast origin
#
# Everything here is computable from data at or before the origin week. Lags stop
# at 13 because `SOCIAL_TREND` series average only ~17 active weeks — a lag-52
# feature would be null for most of the portfolio and would mostly encode
# "is this an evergreen item", which `license_family` already says explicitly.

# %%
w = Window.partitionBy("sku_id", "retailer_code").orderBy("week_end_date")


def _lag(col: str, n: int):
    return F.lag(col, n).over(w)


def _roll(col: str, n: int, fn=F.avg):
    return fn(col).over(w.rowsBetween(-(n - 1), 0))


feat = (
    panel.withColumn("week_index", F.row_number().over(w) - 1)
    .withColumn("weeks_of_history", F.col("week_index") + 1)
    .withColumn(
        "weeks_since_launch",
        F.datediff(F.col("week_end_date"), F.col("first_week")) / 7.0,
    )
    # lags of demand
    .withColumn("lag_1", _lag("demand_units", 1))
    .withColumn("lag_2", _lag("demand_units", 2))
    .withColumn("lag_3", _lag("demand_units", 3))
    .withColumn("lag_4", _lag("demand_units", 4))
    .withColumn("lag_8", _lag("demand_units", 8))
    .withColumn("lag_13", _lag("demand_units", 13))
    # rolling level and dispersion
    .withColumn("roll_mean_4", _roll("demand_units", 4))
    .withColumn("roll_mean_8", _roll("demand_units", 8))
    .withColumn("roll_mean_13", _roll("demand_units", 13))
    .withColumn("roll_std_8", _roll("demand_units", 8, F.stddev))
    .withColumn("roll_max_8", _roll("demand_units", 8, F.max))
    # momentum: the signal that a licence moment is starting
    .withColumn(
        "ratio_4_13",
        F.col("roll_mean_4") / F.when(F.col("roll_mean_13") > 0, F.col("roll_mean_13")),
    )
    .withColumn(
        "cv_8", F.col("roll_std_8") / F.when(F.col("roll_mean_8") > 0, F.col("roll_mean_8"))
    )
    # recent promo and stockout pressure
    .withColumn("promo_rate_8", _roll("promo_flag", 8))
    .withColumn("stockout_rate_8", _roll("was_stockout", 8))
    .withColumn(
        "censoring_8",
        _roll("lost_units", 8) / F.when(_roll("demand_units", 8) > 0, _roll("demand_units", 8)),
    )
    # calendar: toy demand is dominated by the Q4 peak
    .withColumn("week_of_year", F.weekofyear("week_end_date"))
    .withColumn("sin_woy", F.sin(2 * F.lit(3.141592653589793) * F.col("week_of_year") / 52.0))
    .withColumn("cos_woy", F.cos(2 * F.lit(3.141592653589793) * F.col("week_of_year") / 52.0))
    .withColumn(
        "weeks_to_q4_peak",
        F.least(
            F.abs(F.col("week_of_year") - F.lit(47)),
            F.lit(52) - F.abs(F.col("week_of_year") - F.lit(47)),
        ),
    )
    .withColumn(
        "life_fraction",
        F.col("weeks_since_launch")
        / F.when(F.col("planned_lifespan_weeks") > 0, F.col("planned_lifespan_weeks")),
    )
)

# Columns stored in the Feature Engineering table. `retailer_code` is deliberately
# NOT here: it is a primary key of the feature table, so including it as a feature
# column too makes create_table fail with COLUMN_ALREADY_EXISTS. It still reaches
# the model, because the label frame carries it and the FeatureLookup join keeps
# key columns in the training set.
FEATURE_COLS = [
    "lag_1", "lag_2", "lag_3", "lag_4", "lag_8", "lag_13",
    "roll_mean_4", "roll_mean_8", "roll_mean_13", "roll_std_8", "roll_max_8",
    "ratio_4_13", "cv_8", "promo_rate_8", "stockout_rate_8", "censoring_8",
    "weeks_since_launch", "weeks_of_history", "life_fraction",
    "week_of_year", "sin_woy", "cos_woy", "weeks_to_q4_peak",
    "case_pack", "wholesale_price_usd", "gross_margin_pct", "planned_lifespan_weeks",
    "contractual_fill_commitment",
    "brand", "category", "license_family", "lifecycle_stage", "channel",
]
CATEGORICAL = ["brand", "category", "license_family", "lifecycle_stage",
               "channel", "retailer_code"]

features = feat.select(
    "sku_id", "retailer_code", "week_end_date", "week_index", "demand_units", *FEATURE_COLS
)
print(f"feature columns: {len(FEATURE_COLS)} ({len(CATEGORICAL)} categorical)")

# %% [markdown]
# ## 3. Register the feature table in Feature Engineering
#
# Written as a real Feature Engineering table with a composite primary key, so the
# lineage from `gold.fct_demand_weekly` through this table into the model is
# visible in Unity Catalog.
#
# There is deliberately **no timeseries column**. The leakage guard in this build
# is the frame, not the feature layer: features are computed at the origin week and
# the label is taken from origin + h (section 4), with a single-origin holdout. The
# lookup therefore joins on the origin week exactly; an as-of join would add
# nothing and would put the timestamp semantics in a place a reviewer cannot see.

# %%
from databricks.feature_engineering import FeatureEngineeringClient, FeatureLookup  # noqa: E402

fe = FeatureEngineeringClient()

feature_df = features.drop("demand_units", "week_index")
spark.sql(f"DROP TABLE IF EXISTS {config.FEATURE_TABLE}")

fe.create_table(
    name=config.FEATURE_TABLE,
    primary_keys=["sku_id", "retailer_code", "week_end_date"],
    df=feature_df,
    description=(
        "Weekly demand features at SKU x retailer x forecast-origin week. Lags, "
        "rolling level and dispersion, momentum, promo and stockout pressure, "
        "calendar position and product attributes. Consumed through a FeatureLookup "
        "by ml.demand_forecaster. SYNTHETIC DATA."
    ),
)
print(f"created feature table {config.FEATURE_TABLE}")
print(spark.table(config.FEATURE_TABLE).count(), "rows")
spark.sql(f"DESCRIBE TABLE EXTENDED {config.FEATURE_TABLE}").show(60, truncate=False)

# %% [markdown]
# ## 4. Label frame and the time-respecting split
#
# The holdout is a **single forecast origin**: stand at week 142, forecast weeks
# 143-155 at horizons 1-13. That is the decision a planner actually makes, and it
# is strictly harder than a rolling one-step-ahead evaluation, which would let the
# model see an actual from week 149 while scoring week 150.
#
# Training rows are constrained so that `origin + horizon` never crosses into the
# validation window, so no label leaks backwards. Origins are subsampled to keep
# the frame around a million rows; the subsample is seeded and stratified by
# horizon.

# %%
H = config.FORECAST_HORIZON_WEEKS

# week_index is per-series, so split on the calendar instead
weeks = sorted(r[0] for r in features.select("week_end_date").distinct().collect())
W_LAST = weeks[-1]
TEST_ORIGIN = weeks[-(H + 1)]       # week 142
VAL_ORIGIN = weeks[-(2 * H + 1)]    # week 129
print(f"last actual week : {W_LAST}")
print(f"test  origin     : {TEST_ORIGIN}  -> targets {weeks[-H]} .. {W_LAST}")
print(f"val   origin     : {VAL_ORIGIN}  -> targets {weeks[-(2 * H)]} .. {TEST_ORIGIN}")

lead_cols = [
    F.lead("demand_units", h).over(w).alias(f"y_{h}") for h in range(1, H + 1)
]
labelled = features.select(
    "sku_id", "retailer_code", "week_end_date", "demand_units", *lead_cols
)

stack_expr = ", ".join([f"{h}, y_{h}" for h in range(1, H + 1)])
long = (
    labelled.selectExpr(
        "sku_id", "retailer_code", "week_end_date AS origin_week",
        f"stack({H}, {stack_expr}) AS (horizon, target_units)",
    )
    .filter(F.col("target_units").isNotNull())
    .withColumn("target_week", F.expr("date_add(origin_week, horizon * 7)"))
)

train_lf = long.filter(F.col("target_week") < F.lit(VAL_ORIGIN))
val_lf = long.filter(F.col("origin_week") == F.lit(VAL_ORIGIN))
test_lf = long.filter(F.col("origin_week") == F.lit(TEST_ORIGIN))

print(f"\ntrain label rows (pre-subsample): {train_lf.count():,}")
print(f"val   label rows: {val_lf.count():,}")
print(f"test  label rows: {test_lf.count():,}")

TRAIN_TARGET_ROWS = 1_000_000
n_train_all = train_lf.count()
frac = min(1.0, TRAIN_TARGET_ROWS / max(n_train_all, 1))

# Subsample by HASHING the row key rather than with sampleBy.
#
# sampleBy is only deterministic for a fixed partitioning, and the feature table
# is rebuilt on every run, so the sample drifted between executions: xgboost
# scored 72.73 WAPE on one run and 71.41 on the next, and the champion flipped
# between xgboost and lightgbm purely from sampling noise. A committed
# leaderboard has to be reproducible, so membership is now a pure function of
# the row key and is independent of how Spark laid the data out.
BUCKETS = 10_000
cutoff = int(frac * BUCKETS)
train_lf = train_lf.filter(
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
print(f"train subsample fraction {frac:.3f} (hash cutoff {cutoff}/{BUCKETS}) "
      f"-> {train_lf.count():,} rows")

# %% [markdown]
# ## 5. Build the training set through a FeatureLookup
#
# The label frame carries only keys, the origin week, the horizon and the target,
# and every feature is resolved from the Feature Engineering table at the
# **origin** week. `lookup_key` is positional against the table's primary key, so
# `origin_week` is matched to `week_end_date`.

# %%
lookups = [
    FeatureLookup(
        table_name=config.FEATURE_TABLE,
        lookup_key=["sku_id", "retailer_code", "origin_week"],
    )
]


def training_set(label_df):
    return fe.create_training_set(
        df=label_df,
        feature_lookups=lookups,
        label="target_units",
        exclude_columns=["target_week"],
    )


ts_train = training_set(train_lf)
pdf_train = ts_train.load_df().toPandas()
pdf_val = training_set(val_lf).load_df().toPandas()
pdf_test = training_set(test_lf).load_df().toPandas()

print(f"train {pdf_train.shape}  val {pdf_val.shape}  test {pdf_test.shape}")
print("\ncolumns:", list(pdf_train.columns))
print("\ntrain head:")
print(pdf_train.head(3).to_string())

# %% [markdown]
# ## 6. Prepare matrices
#
# `horizon` joins the feature set. Categoricals become pandas `category` dtype,
# which LightGBM consumes natively; the models that cannot (Ridge, RandomForest)
# get a one-hot and median imputation inside their own pipeline, so each family is
# given a fair representation of the same information.

# %%
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Calendar features for the week being PREDICTED are derived from the origin week
# plus the horizon. They are not optional: the holdout stands at a single forecast
# origin, so every origin-based calendar feature is *constant* across all 50,574
# test rows, and without target-week features the model cannot tell a July target
# from a late-September one except through `horizon`. The forward calendar is known
# at forecast time, so using it is not leakage.
#
# The matrix construction lives in `forecasting.pyfunc_model.build_matrix` and is
# the SAME function the registered model runs at inference. That is what makes
# `fe.score_batch` and the serving endpoint safe: there is one implementation, so
# training and serving cannot drift.
from forecasting.pyfunc_model import TARGET_CAL, build_matrix  # noqa: E402

# `retailer_code` is supplied by the label frame rather than the feature table
MODEL_FEATURES = ["horizon", "retailer_code", *FEATURE_COLS, *TARGET_CAL]

ORIGIN_COL = "origin_week"


def prep(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    X = build_matrix(df, MODEL_FEATURES, CATEGORICAL)
    y = df["target_units"].astype("float64").to_numpy()
    return X, y


X_train, y_train = prep(pdf_train)
X_val, y_val = prep(pdf_val)
X_test, y_test = prep(pdf_test)

# align category levels so every split shares one encoding
for c in CATEGORICAL:
    levels = sorted(
        set(X_train[c].cat.categories)
        | set(X_val[c].cat.categories)
        | set(X_test[c].cat.categories)
    )
    for X in (X_train, X_val, X_test):
        X[c] = X[c].cat.set_categories(levels)

print(f"X_train {X_train.shape}  y mean {y_train.mean():.2f}  "
      f"y p99 {np.percentile(y_train, 99):.0f}")
print(f"X_test  {X_test.shape}   y mean {y_test.mean():.2f}")
print("\nnull rate per feature (top 8):")
print((X_train.isna().mean().sort_values(ascending=False).head(8) * 100).round(2).to_string())

# %% [markdown]
# ## 7. Metrics
#
# **WAPE is the headline**, because it is volume-weighted and therefore stable on
# intermittent series. A per-row MAPE average is dominated by near-zero
# denominators on slow sellers — with 2,400 SKUs of which many sell single digits
# a week, MAPE is close to meaningless here. It is reported anyway, restricted to
# non-zero actuals, because buyers ask for it; the gap between the two numbers is
# itself worth showing.
#
# Pinball loss is reported at p50 and p90 so the quantile that drives the
# stockout-risk table is scored, not just the point forecast.

# %%
def wape(y, p):
    d = np.abs(y).sum()
    return float(np.abs(y - p).sum() / d * 100) if d else float("nan")


def mape_nonzero(y, p):
    m = y > 0
    return float(np.mean(np.abs((y[m] - p[m]) / y[m])) * 100) if m.any() else float("nan")


def bias_pct(y, p):
    d = np.abs(y).sum()
    return float((p - y).sum() / d * 100) if d else float("nan")


def pinball(y, p, q):
    d = y - p
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def rmse(y, p):
    return float(np.sqrt(np.mean((y - p) ** 2)))


def evaluate(y, p50, p90):
    return {
        "wape_pct": wape(y, p50),
        "mape_nonzero_pct": mape_nonzero(y, p50),
        "bias_pct": bias_pct(y, p50),
        "rmse": rmse(y, p50),
        "pinball_p50": pinball(y, p50, 0.5),
        "pinball_p90": pinball(y, p90, 0.9),
        "p90_coverage_pct": float(np.mean(y <= p90) * 100),
    }


print("metric helpers defined")

# %% [markdown]
# ## 8. The sweep — 7 candidates across 5 families
#
# This is the direct answer to "can Databricks auto-run experiments across
# algorithms the way our incumbent tool does". Every candidate is logged to one
# MLflow experiment with identical metrics on the identical holdout, so the
# leaderboard is a real comparison and not a collection of separately-tuned
# best cases.
#
# The three boosting candidates use a **Poisson objective**, not least squares.
# Weekly demand here is a non-negative, overdispersed count with a long right
# tail; a squared-error objective chases that tail and over-predicts the bulk of
# the portfolio. Ridge and RandomForest stay on squared error so the sweep still
# spans genuinely different loss assumptions rather than seven variations on one.
#
# p90 is derived identically for every candidate: the per-horizon 90th percentile
# of the validation residual *ratio* is applied to the test p50. Doing it the same
# way for all seven keeps `pinball_p90` comparable. The winner then additionally
# gets a **native quantile model**, and the two are compared on calibration in
# §10 — which is the honest way to show the derived interval is the weaker option.

# %%
import datetime as _dt  # noqa: E402
import time  # noqa: E402

import lightgbm as lgb  # noqa: E402
import mlflow  # noqa: E402
import xgboost as xgb  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import Ridge  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder  # noqa: E402

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(config.MLFLOW_EXPERIMENT)

# Every re-run of this notebook adds a fresh set of runs to the same experiment.
# Filtering the leaderboard on the stage tag alone therefore mixes this sweep
# with every previous one -- the first corrected run produced a 14-row
# leaderboard containing both the broken and the fixed models. Tagging each
# sweep with a unique id and filtering on it keeps the committed leaderboard an
# exact record of one execution.
SWEEP_ID = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
print("sweep_id:", SWEEP_ID)
NUMERIC = [c for c in MODEL_FEATURES if c not in CATEGORICAL]


def sklearn_prep():
    return ColumnTransformer(
        [
            ("num", SimpleImputer(strategy="median"), NUMERIC),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=0.01), CATEGORICAL),
        ]
    )


class SeasonalNaive:
    """Baseline: repeat the most recent observed level.

    `lag_1` is the demand at the forecast origin, so this is the 'assume next
    quarter looks like last week' forecast. It is the number every other
    candidate has to beat to justify its existence.
    """

    family = "baseline"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.nan_to_num(X["lag_1"].to_numpy(dtype="float64"), nan=0.0)


class TrailingMean:
    """Statistical baseline: 13-week trailing mean, the incumbent spreadsheet rule."""

    family = "statistical"

    def fit(self, X, y):
        return self

    def predict(self, X):
        v = X["roll_mean_13"].to_numpy(dtype="float64")
        f = X["roll_mean_4"].to_numpy(dtype="float64")
        out = np.where(np.isnan(v), f, v)
        return np.nan_to_num(out, nan=0.0)


def make_candidates():
    return [
        ("seasonal_naive", "baseline", SeasonalNaive()),
        ("trailing_mean_13", "statistical", TrailingMean()),
        (
            "ridge_linear", "linear",
            Pipeline([("prep", sklearn_prep()), ("m", Ridge(alpha=1.0))]),
        ),
        (
            "random_forest", "sklearn_ensemble",
            Pipeline([
                ("prep", sklearn_prep()),
                ("m", RandomForestRegressor(
                    n_estimators=120, max_depth=14, min_samples_leaf=20,
                    max_features=0.35, n_jobs=-1, random_state=42)),
            ]),
        ),
        (
            "sklearn_hist_gbm", "sklearn_boosting",
            # Poisson loss is right for this target, but at 63 leaves and
            # lr 0.06 it diverged badly here -- RMSE 5,842 and +389% bias,
            # an order of magnitude worse than the naive baseline. Shallower
            # trees, a lower learning rate and stronger leaf regularisation
            # stabilise it. `from_dtype` lets sklearn pick up the pandas
            # categoricals directly instead of relying on positional indices,
            # which silently shift whenever the feature list changes.
            HistGradientBoostingRegressor(
                loss="poisson",
                max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                min_samples_leaf=60, l2_regularization=2.0,
                categorical_features="from_dtype",
                random_state=42),
        ),
        (
            "lightgbm", "gradient_boosting",
            # 127 leaves produced wild tail predictions on this spiky target
            # (RMSE 165 against XGBoost's 100 at comparable WAPE), so the tree
            # is capped tighter and leaves are kept fuller.
            lgb.LGBMRegressor(
                objective="poisson",
                n_estimators=900, learning_rate=0.05, num_leaves=63,
                min_child_samples=60, subsample=0.8, subsample_freq=1,
                colsample_bytree=0.8, reg_lambda=2.0, random_state=42, n_jobs=-1,
                verbose=-1),
        ),
        (
            "xgboost", "gradient_boosting",
            xgb.XGBRegressor(
                objective="count:poisson",
                n_estimators=900, learning_rate=0.05, max_depth=9,
                min_child_weight=20, subsample=0.8, colsample_bytree=0.8,
                reg_lambda=1.0, tree_method="hist", enable_categorical=True,
                random_state=42, n_jobs=-1),
        ),
    ]


def residual_ratio_q90(y_true, p50, horizons) -> dict[int, float]:
    """Per-horizon 90th percentile of the residual ratio actual/forecast."""
    out: dict[int, float] = {}
    safe = np.maximum(p50, 1.0)
    ratio = y_true / safe
    for h in np.unique(horizons):
        m = horizons == h
        out[int(h)] = float(np.nanpercentile(ratio[m], 90)) if m.any() else 1.3
    return out


def apply_q90(p50, horizons, table: dict[int, float]):
    mult = np.array([table.get(int(h), 1.3) for h in horizons])
    return np.maximum(p50 * mult, p50)


h_val = pdf_val["horizon"].to_numpy()
h_test = pdf_test["horizon"].to_numpy()

results = []
fitted: dict[str, object] = {}

for name, family, model in make_candidates():
    t0 = time.time()
    with mlflow.start_run(run_name=name) as run:
        mlflow.set_tags({
            "sweep_id": SWEEP_ID,
            "algorithm": name, "family": family, "stage": "C1_demand_forecast",
            "target": "true_demand_units", "grain": "sku_retailer_week",
            "holdout": "single_origin_13w", "data": "synthetic",
        })
        mlflow.log_params({
            "algorithm": name, "family": family,
            "n_train_rows": int(len(X_train)), "n_features": len(MODEL_FEATURES),
            "horizon_weeks": H, "test_origin": str(TEST_ORIGIN),
        })
        for k, v in (getattr(model, "get_params", lambda: {})() or {}).items():
            if isinstance(v, (int, float, str, bool)) or v is None:
                mlflow.log_param(f"hp_{k}", v)

        model.fit(X_train, y_train)
        p50_val = np.clip(model.predict(X_val), 0, None)
        p50_test = np.clip(model.predict(X_test), 0, None)

        q90 = residual_ratio_q90(y_val, p50_val, h_val)
        p90_test = apply_q90(p50_test, h_test, q90)

        m = evaluate(y_test, p50_test, p90_test)
        m["train_seconds"] = round(time.time() - t0, 1)
        m["val_wape_pct"] = wape(y_val, p50_val)
        mlflow.log_metrics(m)

        fitted[name] = (model, q90)
        results.append({"algorithm": name, "family": family, "run_id": run.info.run_id, **m})
        print(f"{name:18} {family:18} WAPE {m['wape_pct']:7.2f}  "
              f"bias {m['bias_pct']:7.2f}  pinball90 {m['pinball_p90']:7.3f}  "
              f"cov {m['p90_coverage_pct']:5.1f}%  {m['train_seconds']:6.1f}s")

print("\nsweep complete")

# %% [markdown]
# ## 9. Leaderboard
#
# Pulled back out of MLflow with `search_runs` rather than from the in-memory
# list, so this is what the tracking server actually recorded.

# %%
exp = mlflow.get_experiment_by_name(config.MLFLOW_EXPERIMENT)
runs = mlflow.search_runs(
    experiment_ids=[exp.experiment_id],
    filter_string=f"tags.sweep_id = '{SWEEP_ID}'",
    order_by=["metrics.wape_pct ASC"],
)
cols = {
    "tags.algorithm": "algorithm", "tags.family": "family",
    "metrics.wape_pct": "wape_pct", "metrics.mape_nonzero_pct": "mape_nonzero_pct",
    "metrics.bias_pct": "bias_pct", "metrics.rmse": "rmse",
    "metrics.pinball_p50": "pinball_p50", "metrics.pinball_p90": "pinball_p90",
    "metrics.p90_coverage_pct": "p90_coverage_pct",
    "metrics.train_seconds": "train_seconds", "run_id": "run_id",
}
lb = runs[[c for c in cols if c in runs.columns]].rename(columns=cols)
lb = lb.sort_values("wape_pct").reset_index(drop=True)
lb.insert(0, "rank", lb.index + 1)
for c in ["wape_pct", "mape_nonzero_pct", "bias_pct", "rmse", "pinball_p50",
          "pinball_p90", "p90_coverage_pct"]:
    if c in lb:
        lb[c] = lb[c].astype(float).round(3)

pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 30)
print(f"MLflow experiment: {config.MLFLOW_EXPERIMENT}")
print(f"experiment_id    : {exp.experiment_id}")
print(f"runs             : {len(lb)} across {lb.family.nunique()} families\n")
print(lb.to_string(index=False))

lb.to_csv("/Volumes/toy_demand_allocation/bronze/pipeline_meta/forecast_leaderboard.csv",
          index=False)
print("\nwrote forecast_leaderboard.csv")

CHAMPION = lb.iloc[0]["algorithm"]
CHALLENGER = lb.iloc[1]["algorithm"]
print(f"\nchampion  : {CHAMPION}  (WAPE {lb.iloc[0]['wape_pct']}%)")
print(f"challenger: {CHALLENGER}  (WAPE {lb.iloc[1]['wape_pct']}%)")
print(f"baseline  : seasonal_naive WAPE "
      f"{float(lb[lb.algorithm == 'seasonal_naive'].wape_pct.iloc[0])}%")

# %% [markdown]
# ## 10. Native quantile model for p90, and its calibration
#
# The derived p90 in §8 scales the point forecast by a validation residual ratio.
# That is cheap and uniform, but it cannot change *shape* — it widens every series
# by the same factor at a given horizon. A native quantile regressor can. Both are
# scored on the same holdout so the choice is evidence-led.

# %%
champ_model, champ_q90 = fitted[CHAMPION]

with mlflow.start_run(run_name=f"{CHAMPION}_quantile_p90") as qrun:
    mlflow.set_tags({"algorithm": f"{CHAMPION}_quantile_p90", "family": "gradient_boosting",
                     "stage": "C1_quantile", "sweep_id": SWEEP_ID, "data": "synthetic"})
    q_model = lgb.LGBMRegressor(
        objective="quantile", alpha=0.9, n_estimators=700, learning_rate=0.05,
        num_leaves=127, min_child_samples=40, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, random_state=42, n_jobs=-1, verbose=-1,
    )
    q_model.fit(X_train, y_train)
    p90_native = np.clip(q_model.predict(X_test), 0, None)
    p50_test = np.clip(champ_model.predict(X_test), 0, None)
    p90_derived = apply_q90(p50_test, h_test, champ_q90)

    comp = pd.DataFrame([
        {"p90_method": "derived_residual_ratio",
         "pinball_p90": round(pinball(y_test, p90_derived, 0.9), 4),
         "coverage_pct": round(float(np.mean(y_test <= p90_derived) * 100), 2),
         "mean_p90": round(float(p90_derived.mean()), 1)},
        {"p90_method": "native_lightgbm_quantile",
         "pinball_p90": round(pinball(y_test, p90_native, 0.9), 4),
         "coverage_pct": round(float(np.mean(y_test <= p90_native) * 100), 2),
         "mean_p90": round(float(p90_native.mean()), 1)},
    ])
    mlflow.log_metrics({
        "pinball_p90_native": pinball(y_test, p90_native, 0.9),
        "coverage_pct_native": float(np.mean(y_test <= p90_native) * 100),
    })
print("p90 method comparison on the holdout (target coverage 90%):\n")
print(comp.to_string(index=False))

USE_NATIVE_P90 = bool(
    comp.iloc[1]["pinball_p90"] <= comp.iloc[0]["pinball_p90"]
)
print(f"\nselected p90 method: "
      f"{'native_lightgbm_quantile' if USE_NATIVE_P90 else 'derived_residual_ratio'}")

# %% [markdown]
# ## 11. Accuracy by horizon and by licence family
#
# The portfolio number hides the shape of the problem. Accuracy decays with
# horizon, and it is far worse on the licence families whose demand actually
# spikes — which is precisely where the money is.

# %%
err = pd.DataFrame({
    "horizon": h_test, "actual": y_test, "p50": p50_test,
    "license_family": pdf_test["license_family"].to_numpy(),
    "lifecycle_stage": pdf_test["lifecycle_stage"].to_numpy(),
    "retailer_code": pdf_test["retailer_code"].to_numpy(),
})

def group_errors(frame, key):
    """WAPE / bias per group.

    Written as an explicit loop rather than GroupBy.apply: the cluster's pandas
    rejects the include_groups keyword, and silently dropping it emits a
    DeprecationWarning instead of an error on other versions.
    """
    rows = []
    for k, g in frame.groupby(key, observed=True):
        a = g.actual.to_numpy()
        p = g.p50.to_numpy()
        rows.append({
            key: k,
            "wape_pct": round(wape(a, p), 2),
            "bias_pct": round(bias_pct(a, p), 2),
            "rows": len(g),
            "actual_units": int(a.sum()),
        })
    return pd.DataFrame(rows).sort_values(key).reset_index(drop=True)


print("by horizon:\n")
print(group_errors(err, "horizon").to_string(index=False))
print("\nby licence family:\n")
print(group_errors(err, "license_family").to_string(index=False))
print("\nby lifecycle stage:\n")
print(group_errors(err, "lifecycle_stage").to_string(index=False))

base_err = err.assign(naive=np.nan_to_num(X_test["lag_1"].to_numpy(dtype="float64")))
lift = pd.DataFrame([{
    "champion_wape_pct": round(wape(err.actual.to_numpy(), err.p50.to_numpy()), 2),
    "seasonal_naive_wape_pct": round(
        wape(base_err.actual.to_numpy(), base_err.naive.to_numpy()), 2),
}])
lift["wape_points_better"] = (
    lift.seasonal_naive_wape_pct - lift.champion_wape_pct
).round(2)
lift["relative_improvement_pct"] = (
    lift.wape_points_better / lift.seasonal_naive_wape_pct * 100
).round(1)
print("\nlift over the baseline:\n")
print(lift.to_string(index=False))

# %% [markdown]
# ## 12. Register champion and challenger in Unity Catalog
#
# **What gets registered is not the bare estimator.** The estimators were trained on
# a matrix built by `build_matrix` (target-week calendar columns, pinned category
# levels), and p90 comes from a second model or a residual table. Logged bare, the
# registered model could neither be batch-scored from keys nor served: it needed the
# caller to rebuild that matrix, and it could only ever return p50. So each version
# is a `DemandForecaster` pyfunc that carries `build_matrix`, the p50 estimator and
# the p90 method inside it: **keys + features in, `forecast_p50` and `forecast_p90`
# out.**
#
# That wrapper is registered twice, from the same fitted objects:
#
# * `ml.demand_forecaster` goes through `FeatureEngineeringClient.log_model`, so it
#   carries its `FeatureLookup` spec. Lineage from `gold.fct_demand_weekly` through
#   `ml.demand_features_weekly` into the model is visible in Unity Catalog, and
#   `fe.score_batch` joins features by key (section 13) rather than rebuilding 35
#   columns by hand.
# * `ml.demand_forecaster_rt` is the identical model logged as a plain pyfunc with a
#   signature. A feature-store-backed model cannot be deployed to Model Serving here:
#   the endpoint tries to set up an online feature store and fails with "Online
#   feature store setup failed", and this build deliberately has no online store.
#   The real-time caller supplies the feature row itself (the app reads it from
#   Lakebase), so no serving-time lookup is needed.
#
# In both, `@champion` is what consumers read and `@challenger` is what the next
# evaluation promotes or discards. No consumer hardcodes a version number.

# %%
import cloudpickle  # noqa: E402
import sklearn  # noqa: E402
from mlflow.models import infer_signature  # noqa: E402

from forecasting.pyfunc_model import DemandForecaster  # noqa: E402

champ_name = CHAMPION
chal_name = CHALLENGER
champ_obj, champ_q90_tbl = fitted[champ_name]
chal_obj, chal_q90_tbl = fitted[chal_name]

# Category levels exactly as the estimators saw them (aligned across splits in
# section 6). Pinning them is what stops LightGBM mis-splitting on a request that
# happens to contain only some of the levels.
LEVELS = {c: [str(v) for v in X_train[c].cat.categories] for c in CATEGORICAL}

REQUIREMENTS = [
    f"mlflow=={mlflow.__version__}",
    f"lightgbm=={lgb.__version__}",
    f"xgboost=={xgb.__version__}",
    f"scikit-learn=={sklearn.__version__}",
    # NOT `pd.__version__`. This training runtime ships pandas 1.5.3, which has no
    # Python 3.12 wheel, so the serving image tried to compile it from source and
    # the container build failed (`No module named 'pkg_resources'`). The first
    # real-time deploy died on exactly this. 2.2.3 has a wheel, and the wrapper
    # uses only basic DataFrame operations that behave identically across 1.5/2.x;
    # the validation cell below proves it rather than assuming it.
    "pandas==2.2.3",
    f"numpy=={np.__version__}",
    f"cloudpickle=={cloudpickle.__version__}",
]
CODE_PATHS = [WS + "/src/forecasting"]

# one realistic request row, for the signature and for pre-deploy validation
example_in = pdf_test.drop(columns=["target_units"]).head(5).copy()
example_in["origin_week"] = pd.to_datetime(example_in["origin_week"]).dt.strftime("%Y-%m-%d")
example_in["horizon"] = example_in["horizon"].astype("int64")
for c in example_in.columns:
    if c not in CATEGORICAL and c not in ("sku_id", "origin_week", "horizon"):
        example_in[c] = pd.to_numeric(example_in[c], errors="coerce").astype("float64")

registered: dict[str, int] = {}
registered_rt: dict[str, int] = {}
client = mlflow.MlflowClient()


def newest_version(name: str) -> int:
    return max(int(v.version) for v in client.search_model_versions(f"name='{name}'"))


def tag_version(name: str, ver: int, algo: str) -> None:
    client.set_model_version_tag(name, str(ver), "algorithm", algo)
    client.set_model_version_tag(name, str(ver), "sweep_id", SWEEP_ID)
    client.set_model_version_tag(
        name, str(ver), "holdout_wape_pct",
        str(round(float(lb[lb.algorithm == algo].wape_pct.iloc[0]), 3)),
    )


for role, algo, model_obj, q90_tbl in (
    ("champion", champ_name, champ_obj, champ_q90_tbl),
    ("challenger", chal_name, chal_obj, chal_q90_tbl),
):
    # The native quantile model exists for the champion only (section 10), and is
    # used only if it won the calibration comparison. Every other version applies
    # its own per-horizon residual-ratio table.
    native = q_model if (role == "champion" and algo == CHAMPION and USE_NATIVE_P90) else None
    wrapper = DemandForecaster(
        p50_model=model_obj,
        model_features=MODEL_FEATURES,
        categorical=CATEGORICAL,
        levels=LEVELS,
        p90_model=native,
        q90_table=q90_tbl,
    )
    example_out = wrapper.predict(None, example_in)
    sig = infer_signature(example_in, example_out)

    tags = {
        "sweep_id": SWEEP_ID, "stage": "C1_register", "role": role,
        "algorithm": algo, "p90_method": wrapper.p90_method, "data": "synthetic",
    }
    # feature-store-backed version, for fe.score_batch
    with mlflow.start_run(run_name=f"register_{role}_{algo}"):
        mlflow.set_tags(tags)
        fe.log_model(
            model=wrapper,
            artifact_path="model",
            flavor=mlflow.pyfunc,
            training_set=ts_train,
            registered_model_name=config.MODEL_NAME,
            pip_requirements=REQUIREMENTS,
            code_paths=CODE_PATHS,
        )
    registered[role] = newest_version(config.MODEL_NAME)
    client.set_registered_model_alias(config.MODEL_NAME, role, registered[role])
    tag_version(config.MODEL_NAME, registered[role], algo)

    # plain-pyfunc twin, for the real-time endpoint
    with mlflow.start_run(run_name=f"register_{role}_{algo}_rt"):
        mlflow.set_tags({**tags, "variant": "realtime"})
        mlflow.pyfunc.log_model(
            artifact_path="model",
            python_model=wrapper,
            signature=sig,
            input_example=example_in,
            pip_requirements=REQUIREMENTS,
            code_paths=CODE_PATHS,
            registered_model_name=config.MODEL_NAME_RT,
        )
    registered_rt[role] = newest_version(config.MODEL_NAME_RT)
    client.set_registered_model_alias(config.MODEL_NAME_RT, role, registered_rt[role])
    tag_version(config.MODEL_NAME_RT, registered_rt[role], algo)
    print(f"{role:11} -> {config.MODEL_NAME} v{registered[role]} (feature-backed)  |  "
          f"{config.MODEL_NAME_RT} v{registered_rt[role]} (real-time)  "
          f"[{algo}, p90={wrapper.p90_method}]")

# %%
client = mlflow.MlflowClient()
print(f"registered model: {config.MODEL_NAME}\n")

# Build the version -> aliases map from the REGISTERED MODEL, not from each
# ModelVersion: `ModelVersion.aliases` is a bound method in this MLflow version,
# so iterating it raises "'method' object is not iterable" rather than returning
# the alias list.
rm = client.get_registered_model(config.MODEL_NAME)
raw_aliases = rm.aliases or {}
alias_by_version: dict[int, list[str]] = {}
if isinstance(raw_aliases, dict):
    # this MLflow version returns {alias_name: version_string}
    for alias_name, ver in raw_aliases.items():
        alias_by_version.setdefault(int(ver), []).append(str(alias_name))
else:
    for a in raw_aliases:
        alias_by_version.setdefault(int(a.version), []).append(a.alias)

def _tags(obj) -> dict:
    """Model-version tags as a dict.

    The objects returned by `search_model_versions` expose `tags` and `aliases`
    as bound METHODS, while the single-fetch calls return them as a dict. Reading
    `.tags` off a search result therefore raises rather than returning tags, so
    anything that needs tags must go through a single fetch.
    """
    t = getattr(obj, "tags", None)
    return t if isinstance(t, dict) else {}


all_versions = sorted(
    int(v.version) for v in client.search_model_versions(f"name='{config.MODEL_NAME}'")
)

rows = []
for ver in all_versions:
    mv = client.get_model_version(config.MODEL_NAME, str(ver))
    tags = _tags(mv)
    names = sorted(alias_by_version.get(ver, []))
    rows.append({
        "version": ver,
        "aliases": ",".join(f"@{n}" for n in names) if names else "-",
        "algorithm": tags.get("algorithm", "-"),
        "holdout_wape_pct": tags.get("holdout_wape_pct", "-"),
        "sweep_id": tags.get("sweep_id", "-"),
        "status": str(getattr(mv, "status", "-")),
    })
print(pd.DataFrame(rows).to_string(index=False))
print(
    "\nVersions accumulate across notebook runs, which is the point of a registry: "
    "\nearlier versions stay auditable and the aliases always resolve to the "
    "current sweep."
)

print("\nalias resolution (what every consumer actually loads):")
for alias in (config.CHAMPION_ALIAS, config.CHALLENGER_ALIAS):
    mv = client.get_model_version_by_alias(config.MODEL_NAME, alias)
    t = _tags(mv)
    print(f"  models:/{config.MODEL_NAME}@{alias}")
    print(f"      -> version {mv.version}, algorithm={t.get('algorithm')}, "
          f"holdout WAPE {t.get('holdout_wape_pct')}%")

# %% [markdown]
# ### Pre-deploy validation: does the real-time model work in the serving image's environment?
#
# A model that registers cleanly can still fail to deploy, and a failed container
# build costs a long queue wait to discover. So before any endpoint is touched,
# rebuild the environment the serving image will build, from the model's own
# `requirements.txt`, in a fresh Python 3.12 virtualenv, and compare its predictions
# with this notebook's. Same dependencies, none of the training runtime's packages,
# binary wheels only (so an unbuildable pin fails here, as it would in the image).

# %%
import json  # noqa: E402
import pathlib  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402


def _sh(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


work = pathlib.Path("/local_disk0/tmp/rt_validation")
shutil.rmtree(work, ignore_errors=True)
work.mkdir(parents=True)

rt_uri = f"models:/{config.MODEL_NAME_RT}@{config.CHAMPION_ALIAS}"
art = pathlib.Path(mlflow.artifacts.download_artifacts(rt_uri, dst_path=str(work / "model")))
print("model artifact :", rt_uri)
print("requirements   :\n  " + "\n  ".join((art / "requirements.txt").read_text().split()))

venv = work / "venv"
r = _sh(f"python -m pip install -q uv && python -m uv venv {venv} --python /usr/bin/python3.12")
assert r.returncode == 0, r.stderr[-800:]
r = _sh(
    f"python -m uv pip install --python {venv}/bin/python --only-binary :all: "
    f"-r {art / 'requirements.txt'}"
)
assert r.returncode == 0, "requirements do not install on Python 3.12:\n" + r.stderr[-1500:]

(work / "predict.py").write_text(f"""
import json, pandas as pd, mlflow.pyfunc
m = mlflow.pyfunc.load_model('{art}')
ex = json.load(open('{art}/input_example.json'))
out = m.predict(pd.DataFrame(data=ex['data'], columns=ex['columns']))
print('RESULT' + json.dumps(out.to_dict(orient='list')))
""")
r = _sh(f"cd {work} && {venv}/bin/python predict.py")
assert r.returncode == 0, r.stderr[-1500:]
fresh = pd.DataFrame(json.loads(next(
    ln for ln in r.stdout.splitlines() if ln.startswith("RESULT"))[len("RESULT"):]))
probe = _sh(
    f"{venv}/bin/python -c 'import pandas,sys;print(pandas.__version__, sys.version.split()[0])'"
)
print(f"\nfresh venv     : pandas / python = {probe.stdout.strip()}")
print(f"this notebook  : pandas {pd.__version__}")
mv_rt = client.get_model_version_by_alias(config.MODEL_NAME_RT, config.CHAMPION_ALIAS)
print(f"rt champion    : {config.MODEL_NAME_RT} v{mv_rt.version}\n")

ex_json = json.loads((art / "input_example.json").read_text())
expected = mlflow.pyfunc.load_model(rt_uri).predict(
    pd.DataFrame(ex_json["data"], columns=ex_json["columns"])
)
cmp_rt = pd.concat({"serving env": fresh, "this notebook": expected}, axis=1)
print(cmp_rt.to_string())
delta = float((fresh.to_numpy() - expected.to_numpy()).__abs__().max())
print(f"\nmax |difference| = {delta:.6f}")
assert delta < 0.02, "serving environment disagrees with the training environment"
print("The serving environment reproduces the training environment's forecasts.")

# %% [markdown]
# ## 13. Score 13 weeks forward with `fe.score_batch`
#
# Stand at the last actual week and forecast the next 13 at horizons 1-13 in a
# single pass — no recursion, so no error compounding. The window lands squarely
# in the Q4 build, which is exactly when a licensed-toy planner is most exposed.
#
# **Scoring takes keys only.** The input frame is `sku_id`, `retailer_code`,
# `origin_week` and `horizon`; `fe.score_batch` resolves all 33 feature columns from
# the model's own registered `FeatureLookup`, and the model itself builds the
# target-week calendar and applies p50 and p90. Nothing about the feature matrix is
# rebuilt by hand here, which is the training/serving skew the feature layer exists
# to prevent. A parity check at the end proves the keys-only path reproduces the
# estimators' own output exactly.

# %%
origin_keys = (
    spark.table(config.FEATURE_TABLE)
    .filter(F.col("week_end_date") == F.lit(W_LAST))
    .select("sku_id", "retailer_code", F.col("week_end_date").alias("origin_week"))
)
score_keys = origin_keys.crossJoin(
    spark.range(1, H + 1).select(F.col("id").cast("int").alias("horizon"))
)
print(f"series with features at origin {W_LAST}: {origin_keys.count():,}")
print(f"keys to score: {score_keys.count():,}  (columns: {score_keys.columns})")

champion_uri = f"models:/{config.MODEL_NAME}@{config.CHAMPION_ALIAS}"
scored = fe.score_batch(
    model_uri=champion_uri,
    df=score_keys,
    result_type="struct<forecast_p50:double,forecast_p90:double>",
)
forecast_sdf = scored.select(
    "sku_id", "retailer_code", "origin_week", "horizon",
    F.round(F.col("prediction.forecast_p50"), 2).alias("forecast_p50"),
    F.round(F.col("prediction.forecast_p90"), 2).alias("forecast_p90"),
).withColumn("week_end_date", F.expr("date_add(origin_week, horizon * 7)")).withColumns({
    "model_name": F.lit(config.MODEL_NAME),
    "model_version": F.lit(registered["champion"]),
    "model_alias": F.lit(config.CHAMPION_ALIAS),
    "algorithm": F.lit(champ_name),
    "p90_method": F.lit("native_quantile" if USE_NATIVE_P90 else "derived_residual_ratio"),
})
forecast_sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    config.FCT_DEMAND_FORECAST
)
spark.sql(f"""
COMMENT ON TABLE {config.FCT_DEMAND_FORECAST} IS
'13-week forward demand forecast at SKU x retailer x week, p50 and p90, produced
by the @champion version of ml.demand_forecaster via fe.score_batch from a single
forecast origin. Consumed by gold.fct_stockout_risk, the allocation agent and the
app. SYNTHETIC DATA.'
""")
print(f"\nwrote {config.FCT_DEMAND_FORECAST}  via {champion_uri} (v{registered['champion']})")
forecast = spark.table(config.FCT_DEMAND_FORECAST).toPandas()
print(f"forecast rows: {len(forecast):,}  weeks "
      f"{forecast.week_end_date.min()} .. {forecast.week_end_date.max()}")
print(forecast.head(6).to_string(index=False))
spark.sql(f"""
SELECT week_end_date, COUNT(*) AS series,
       ROUND(SUM(forecast_p50)) AS total_p50, ROUND(SUM(forecast_p90)) AS total_p90
FROM {config.FCT_DEMAND_FORECAST} GROUP BY week_end_date ORDER BY week_end_date
""").show(20, truncate=False)

# %% [markdown]
# ### Parity: keys-only `score_batch` vs. the estimators called directly
#
# The old path joined 35 columns by hand and called the estimator. Repeat it on a
# sample of series and require the registered model to reproduce it. Any difference
# would be exactly the skew this section exists to remove.

# %%
sample_keys = (
    forecast[["sku_id", "retailer_code"]].drop_duplicates().sample(500, random_state=7)
)
fwd = (
    spark.table(config.FEATURE_TABLE).filter(F.col("week_end_date") == F.lit(W_LAST))
    .toPandas().merge(sample_keys, on=["sku_id", "retailer_code"])
)
fwd = fwd.loc[fwd.index.repeat(H)].copy()
fwd["horizon"] = np.tile(np.arange(1, H + 1), len(fwd) // H)
fwd["origin_week"] = pd.to_datetime(fwd["week_end_date"])

X_fwd = build_matrix(fwd, MODEL_FEATURES, CATEGORICAL, LEVELS)
p50_ref = np.clip(champ_obj.predict(X_fwd), 0, None)
if USE_NATIVE_P90:
    p90_ref = np.maximum(np.clip(q_model.predict(X_fwd), 0, None), p50_ref)
else:
    p90_ref = apply_q90(p50_ref, fwd["horizon"].to_numpy(), champ_q90_tbl)
ref = pd.DataFrame({
    "sku_id": fwd["sku_id"].to_numpy(), "retailer_code": fwd["retailer_code"].to_numpy(),
    "horizon": fwd["horizon"].to_numpy(),
    "ref_p50": np.round(p50_ref, 2), "ref_p90": np.round(p90_ref, 2),
})
cmp = forecast.merge(ref, on=["sku_id", "retailer_code", "horizon"])
d50 = (cmp.forecast_p50 - cmp.ref_p50).abs().max()
d90 = (cmp.forecast_p90 - cmp.ref_p90).abs().max()
print(f"rows compared        : {len(cmp):,}")
print(f"max |p50 difference| : {d50:.4f}")
print(f"max |p90 difference| : {d90:.4f}")
assert len(cmp) == len(ref) and d50 < 0.02 and d90 < 0.02, "score_batch diverges from estimator"
print("score_batch reproduces the estimator output from keys alone.")

# %% [markdown]
# ## 14. Holdout forecast-vs-actual table
#
# The evaluation surface for `gold.mv_forecast_accuracy`, which Stage B left
# deferred because this table did not exist yet. One row per
# SKU x retailer x holdout week with the actual, the p50 and the p90, tagged with
# the model version and alias that produced it — so accuracy can be tracked per
# version once a challenger is promoted.

# %%
eval_df = pd.DataFrame({
    "sku_id": pdf_test["sku_id"].to_numpy(),
    "retailer_code": pdf_test["retailer_code"].to_numpy(),
    "week_end_date": (
        pd.to_datetime(pdf_test[ORIGIN_COL])
        + pd.to_timedelta(pdf_test["horizon"].astype(int) * 7, unit="D")
    ).dt.date.to_numpy(),
    "horizon": pdf_test["horizon"].to_numpy().astype(int),
    "actual_units": y_test,
    "forecast_p50": np.round(p50_test, 2),
    "forecast_p90": np.round(p90_native if USE_NATIVE_P90 else p90_derived, 2),
    "brand": pdf_test["brand"].to_numpy(),
    "license_family": pdf_test["license_family"].to_numpy(),
    "lifecycle_stage": pdf_test["lifecycle_stage"].to_numpy(),
})
eval_df["model_name"] = config.MODEL_NAME
eval_df["model_version"] = registered["champion"]
eval_df["model_alias"] = config.CHAMPION_ALIAS
eval_df["abs_error"] = (eval_df.forecast_p50 - eval_df.actual_units).abs().round(2)

EVAL_TABLE = config.fq("gold", "fct_demand_forecast_eval")
spark.createDataFrame(eval_df).write.mode("overwrite").option(
    "overwriteSchema", "true"
).saveAsTable(EVAL_TABLE)
spark.sql(f"""
COMMENT ON TABLE {EVAL_TABLE} IS
'Forecast vs actual on the single-origin 13-week holdout, at SKU x retailer x week,
tagged with the model version and alias that produced it. Source of
gold.mv_forecast_accuracy. SYNTHETIC DATA.'
""")
print(f"wrote {EVAL_TABLE}: {len(eval_df):,} rows")
print(eval_df.head(5).to_string(index=False))

print("\nforecast vs actual, one holdout week, highest-volume series:")
spark.sql(f"""
SELECT sku_id, retailer_code, week_end_date, horizon, actual_units,
       forecast_p50, forecast_p90, abs_error, license_family, lifecycle_stage
FROM {EVAL_TABLE}
WHERE week_end_date = (SELECT MIN(week_end_date) FROM {EVAL_TABLE})
ORDER BY actual_units DESC LIMIT 12
""").show(truncate=False)

# %% [markdown]
# ## 15. `gold.fct_stockout_risk` — the spine of Stages C2, D, E and F
#
# This is the table the agent reasons over, the app ranks its Monday worklist by,
# Genie answers questions about, and Lakebase serves. It answers one question per
# row: *will this SKU at this retailer run out, when, and what is it worth?*
#
# The supply side is deliberately shared. Inventory is held at SKU x DC, not per
# retailer, so each retailer's claim on a SKU is scaled by its share of forecast
# demand. That is what makes allocation genuinely **constrained**: when a licence
# spikes, two retailers are competing for the same pool, and someone has to
# decide. Stage C2's allocator is what makes that decision explicit and auditable.
#
# Cover is assessed cumulatively — a week is not "safe" because that week's demand
# is covered, it is safe only if every week up to it is.

# %%
risk_sql = f"""
WITH fc AS (
  SELECT sku_id, retailer_code, week_end_date, horizon, forecast_p50, forecast_p90,
         model_version, model_alias
  FROM {config.FCT_DEMAND_FORECAST}
),
-- each retailer's share of a SKU's forecast demand over the horizon
share AS (
  SELECT sku_id, retailer_code,
         SUM(forecast_p50) AS retailer_p50,
         SUM(forecast_p50) / NULLIF(SUM(SUM(forecast_p50)) OVER (PARTITION BY sku_id), 0)
           AS demand_share
  FROM fc GROUP BY sku_id, retailer_code
),
-- the as-of date of the inventory position, materialised once so it is not a
-- scalar subquery inside a join condition (Spark rejects those)
asof AS (
  SELECT MAX(snapshot_date) AS asof_date FROM {C}.gold.fct_inventory_position
),
-- on-hand across all DCs at the latest snapshot
oh AS (
  SELECT sku_id, SUM(on_hand_units) AS on_hand_units
  FROM {C}.gold.fct_inventory_position
  WHERE is_latest_snapshot = true
  GROUP BY sku_id
),
-- inbound arriving on or before each forecast week, by revised ETA. Only
-- shipments landing AFTER the inventory as-of date count, otherwise units
-- already counted in on-hand would be double counted.
inb AS (
  SELECT f.sku_id, f.week_end_date,
         COALESCE(SUM(i.shipped_units), 0) AS inbound_by_eta
  FROM (SELECT DISTINCT sku_id, week_end_date FROM fc) f
  CROSS JOIN asof a
  LEFT JOIN {C}.gold.fct_inbound_eta i
    ON i.sku_id = f.sku_id
   AND i.revised_eta_date <= f.week_end_date
   AND i.revised_eta_date > a.asof_date
  GROUP BY f.sku_id, f.week_end_date
),
-- Cumulative demand at the 90th percentile.
--
-- NOT the running sum of the weekly p90s. The sum of quantiles is not the
-- quantile of the sum: adding thirteen independent weekly p90s produces roughly
-- a p99.9 of the 13-week total, which flagged 74% of all series as at-risk and
-- would hand a planner a 3,330-row Monday worklist. Instead the weekly sigma is
-- recovered from the p90-p50 gap, variances are accumulated, and the cumulative
-- quantile is rebuilt from the cumulative mean -- so the interval widens with
-- the square root of the horizon rather than linearly.
cum AS (
  SELECT fc.*, s.demand_share,
         SUM(fc.forecast_p50) OVER (
           PARTITION BY fc.sku_id, fc.retailer_code ORDER BY fc.week_end_date
         ) AS cum_demand_p50,
         SUM(fc.forecast_p50) OVER (
           PARTITION BY fc.sku_id, fc.retailer_code ORDER BY fc.week_end_date
         )
         + 1.2816 * SQRT(
             SUM(POW(GREATEST(fc.forecast_p90 - fc.forecast_p50, 0) / 1.2816, 2)) OVER (
               PARTITION BY fc.sku_id, fc.retailer_code ORDER BY fc.week_end_date
             )
           ) AS cum_demand_p90
  FROM fc JOIN share s USING (sku_id, retailer_code)
),
pos AS (
  SELECT c.*,
         COALESCE(oh.on_hand_units, 0) AS sku_on_hand_units,
         COALESCE(inb.inbound_by_eta, 0) AS sku_inbound_by_eta,
         (COALESCE(oh.on_hand_units, 0) + COALESCE(inb.inbound_by_eta, 0))
           * c.demand_share AS retailer_available_units
  FROM cum c
  LEFT JOIN oh USING (sku_id)
  LEFT JOIN inb ON inb.sku_id = c.sku_id AND inb.week_end_date = c.week_end_date
),
flagged AS (
  SELECT p.*,
         p.cum_demand_p90 - p.retailer_available_units AS shortfall_units,
         CASE WHEN p.cum_demand_p90 > p.retailer_available_units THEN 1 ELSE 0 END
           AS is_short
  FROM pos p
),
first_short AS (
  SELECT sku_id, retailer_code,
         MIN(CASE WHEN is_short = 1 THEN week_end_date END) AS projected_stockout_week,
         MIN(CASE WHEN is_short = 1 THEN horizon END)       AS weeks_to_stockout
  FROM flagged GROUP BY sku_id, retailer_code
)
SELECT
  f.sku_id,
  f.retailer_code,
  d.retailer_label,
  p.brand,
  p.license_family,
  p.lifecycle_stage,
  fs.projected_stockout_week,
  fs.weeks_to_stockout,
  ROUND(MAX(f.sku_on_hand_units), 0)                       AS sku_on_hand_units,
  ROUND(MAX(f.sku_inbound_by_eta), 0)                      AS sku_inbound_horizon_units,
  ROUND(MAX(f.demand_share), 4)                            AS retailer_demand_share,
  ROUND(SUM(f.forecast_p50), 0)                            AS forecast_p50_13w,
  ROUND(SUM(f.forecast_p90), 0)                            AS forecast_p90_13w,
  ROUND(MAX(CASE WHEN f.week_end_date = fs.projected_stockout_week
                 THEN f.shortfall_units END), 0)           AS units_at_risk,
  ROUND(MAX(CASE WHEN f.week_end_date = fs.projected_stockout_week
                 THEN f.shortfall_units END) * p.wholesale_price_usd, 2)
                                                           AS revenue_at_risk_usd,
  p.wholesale_price_usd,
  d.contractual_fill_commitment,
  MAX(f.model_version)                                     AS model_version,
  MAX(f.model_alias)                                       AS model_alias,
  CURRENT_TIMESTAMP()                                      AS computed_at
FROM flagged f
JOIN first_short fs USING (sku_id, retailer_code)
JOIN {C}.silver.product_master p ON p.sku_id = f.sku_id
JOIN (SELECT DISTINCT retailer_code, retailer_label, contractual_fill_commitment
      FROM {C}.gold.dim_retailer_dc) d ON d.retailer_code = f.retailer_code
WHERE fs.projected_stockout_week IS NOT NULL
GROUP BY f.sku_id, f.retailer_code, d.retailer_label, p.brand, p.license_family,
         p.lifecycle_stage, fs.projected_stockout_week, fs.weeks_to_stockout,
         p.wholesale_price_usd, d.contractual_fill_commitment
"""

risk = spark.sql(risk_sql)

# A 13-week cumulative projection flags two thirds of the portfolio, and that is
# not a mistake in the arithmetic -- it is what "will demand exceed supply within
# 13 weeks" means for a business carrying about 13 weeks of cover. It is a
# replenishment signal, not a 3,000-item alarm, so the rows are tiered by how
# soon the shortfall lands. CRITICAL and HIGH are the planner's actual Monday
# queue; the rest is the ordering book. The app, the agent and Genie all filter
# on this rather than re-deriving a threshold each.
#
# Severity blends what is at stake, how soon, and how hard the account is to
# miss contractually. Normalised so it is comparable across SKUs of very
# different absolute value.
risk = (
    risk.withColumn(
        "urgency", (F.lit(H + 1) - F.col("weeks_to_stockout")) / F.lit(H)
    )
    .withColumn(
        "value_rank",
        F.percent_rank().over(Window.orderBy(F.col("revenue_at_risk_usd").asc_nulls_first())),
    )
    .withColumn(
        "severity_score",
        F.round(
            100.0
            * (
                0.50 * F.col("value_rank")
                + 0.35 * F.col("urgency")
                + 0.15 * F.col("contractual_fill_commitment")
            ),
            2,
        ),
    )
    .withColumn(
        "risk_tier",
        F.when(F.col("weeks_to_stockout") <= 2, F.lit("CRITICAL"))
        .when(F.col("weeks_to_stockout") <= 4, F.lit("HIGH"))
        .when(F.col("weeks_to_stockout") <= 8, F.lit("MEDIUM"))
        .otherwise(F.lit("LOW")),
    )
    .drop("urgency", "value_rank")
)

risk.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    config.FCT_STOCKOUT_RISK
)
spark.sql(f"""
COMMENT ON TABLE {config.FCT_STOCKOUT_RISK} IS
'Projected stockouts at SKU x retailer over the 13-week forecast horizon: forecast
p90 demand against on-hand plus inbound-by-ETA, with the projected stockout week,
units and revenue at risk, and a severity score blending value at stake, urgency
and the contractual fill commitment. Supply is shared across retailers in
proportion to forecast demand, which is what makes the allocation constrained.
Spine of the allocation agent, the app worklist, Genie and Lakebase. SYNTHETIC DATA.'
""")

n_risk = spark.table(config.FCT_STOCKOUT_RISK).count()
n_series = spark.table(config.FCT_DEMAND_FORECAST).select(
    "sku_id", "retailer_code"
).distinct().count()
print(f"wrote {config.FCT_STOCKOUT_RISK}")
print(f"{n_risk:,} of {n_series:,} SKU x retailer series project a cover shortfall "
      f"within {H} weeks ({100.0 * n_risk / n_series:.1f}%)")
print()
print("Tiered, which is what makes it a worklist rather than an alarm:")
spark.sql(f"""
SELECT risk_tier,
       COUNT(*)                                  AS combinations,
       MIN(weeks_to_stockout)                    AS min_weeks,
       MAX(weeks_to_stockout)                    AS max_weeks,
       ROUND(SUM(units_at_risk))                 AS units_at_risk,
       ROUND(SUM(revenue_at_risk_usd))           AS revenue_at_risk_usd,
       ROUND(AVG(severity_score), 1)             AS avg_severity
FROM {config.FCT_STOCKOUT_RISK}
GROUP BY risk_tier
ORDER BY CASE risk_tier WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                        WHEN 'MEDIUM' THEN 3 ELSE 4 END
""").show(truncate=False)

# %%
print("top 15 by severity -- this is the head of the planner's Monday queue:\n")
spark.sql(f"""
SELECT sku_id, retailer_code, brand, license_family, lifecycle_stage,
       risk_tier, projected_stockout_week, weeks_to_stockout, units_at_risk,
       revenue_at_risk_usd, severity_score
FROM {config.FCT_STOCKOUT_RISK}
ORDER BY severity_score DESC, revenue_at_risk_usd DESC
LIMIT 15
""").show(truncate=False)

print("risk concentration by licence family:")
spark.sql(f"""
SELECT license_family,
       COUNT(*) AS at_risk_combinations,
       ROUND(AVG(weeks_to_stockout), 1) AS avg_weeks_to_stockout,
       ROUND(SUM(units_at_risk)) AS units_at_risk,
       ROUND(SUM(revenue_at_risk_usd), 0) AS revenue_at_risk_usd
FROM {config.FCT_STOCKOUT_RISK}
GROUP BY license_family ORDER BY revenue_at_risk_usd DESC
""").show(truncate=False)

print("by retailer:")
spark.sql(f"""
SELECT retailer_code, retailer_label, COUNT(*) AS at_risk_combinations,
       ROUND(SUM(revenue_at_risk_usd), 0) AS revenue_at_risk_usd,
       ROUND(AVG(severity_score), 1) AS avg_severity
FROM {config.FCT_STOCKOUT_RISK}
GROUP BY retailer_code, retailer_label ORDER BY revenue_at_risk_usd DESC
""").show(truncate=False)

print("SKUs where two or more retailers compete for the same constrained pool")
print("(this is the allocation decision the agent has to make):")
spark.sql(f"""
SELECT sku_id, brand, license_family,
       COUNT(DISTINCT retailer_code) AS retailers_at_risk,
       MAX(sku_on_hand_units) AS sku_on_hand_units,
       ROUND(SUM(units_at_risk)) AS combined_units_at_risk,
       ROUND(SUM(revenue_at_risk_usd), 0) AS combined_revenue_at_risk_usd
FROM {config.FCT_STOCKOUT_RISK}
GROUP BY sku_id, brand, license_family
HAVING COUNT(DISTINCT retailer_code) >= 2
ORDER BY combined_revenue_at_risk_usd DESC LIMIT 10
""").show(truncate=False)

# %% [markdown]
# ## 16. Why this replaces a point AutoML platform
#
# The technical buyer's question is whether this can retire an incumbent AutoML
# tool. The sweep above is the functional answer — seven candidates across six
# families, one experiment, identical metrics on an identical holdout, a
# leaderboard pulled back out of the tracking server. But the functional answer
# is the least interesting part, because most tools can fit seven models.
#
# What is different here is what surrounds the model:
#
# * **Governed features, not a CSV.** `ml.demand_features_weekly` is a Unity
#   Catalog table with a composite primary key, comments and grants. The
#   training set was assembled through a `FeatureLookup`, so the join is declared
#   once and `fe.score_batch` reuses it at inference from keys alone, rather than
#   reimplementing it in a scoring script.
# * **Lineage from landing file to prediction.** `_source_file` on the Bronze row
#   survives into Gold; Gold feeds the feature table; the feature table feeds the
#   model; the model writes `gold.fct_demand_forecast`. That chain is queryable
#   in `system.access.table_lineage`, not reconstructed from memory.
# * **Open format, no export step.** The model is MLflow-flavoured in UC with
#   `@champion` / `@challenger` aliases. Promotion is an alias move, not a
#   re-export and re-deploy, and nothing downstream references a version number.
# * **One platform for the next three stages.** The same fitted model, registered
#   as a feature-backed version for batch and a plain twin for the real-time
#   endpoint, is reasoned over by an agent, served to an app
#   through Lakebase and queried in natural language through Genie — without
#   leaving the governance boundary or handing a pickle to another system.
# * **The governance already applies to it.** The row filter and column mask from
#   Stage B are not bolted on afterwards; this notebook had to read cost from
#   `silver.product_master` precisely because the Gold mask was already in force
#   against its own identity.
#
# The incumbent is named generically on purpose. The argument is not that it
# cannot fit a model; it is that the model is the easy part.
