"""Generate evidence/03_ml_forecast.md from MLflow and Unity Catalog.

    python tools/evidence_03_ml_forecast.py
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.dbsql import sql, table  # noqa: E402

HOST = "https://fevm-stable-classic-pqjg18.cloud.databricks.com"
C = "toy_demand_allocation"
G = f"{C}.gold"
MODEL = f"{C}.ml.demand_forecaster"
EXPERIMENT = "/Users/sixuan.he@databricks.com/toy_demand_allocation_forecast"
CLUSTER = "1008-205814-qhbfm7tc"


def main() -> int:
    out: list[str] = []
    a = out.append

    a("# Evidence 03 — Stage C1: demand forecast, sweep, champion/challenger")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Produced by | `notebooks/03_demand_forecast.ipynb` on cluster "
      f"`{CLUSTER}` (DBR 16.4 LTS ML), then `tools/evidence_03_ml_forecast.py` "
      "reading MLflow and Unity Catalog back |")
    a(f"| Generated at | {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC |")
    a(f"| Workspace | `{HOST}` |")
    a("| Profile | `vm-summer-classic` |")
    a(f"| MLflow experiment | `{EXPERIMENT}` |")
    a(f"| Registered model | `{MODEL}` |")
    a(f"| UC paths written | `{G}.fct_demand_forecast`, "
      f"`{G}.fct_demand_forecast_eval`, `{G}.fct_stockout_risk`, "
      f"`{C}.ml.demand_features_weekly`, `{G}.mv_forecast_accuracy` |")
    a("| Data | 100% synthetic |")
    a("")
    a("---")
    a("")

    # 1. design
    a("## 1. Problem setup")
    a("")
    a("| Choice | Value | Why |")
    a("|---|---|---|")
    a("| Grain | SKU x retailer x week | The DC is a fulfilment detail, not a "
      "demand signal, so Gold's SKU x retailer x DC x week fact is aggregated up |")
    a("| Target | `true_demand_units` | The business wants to predict *demand*, "
      "but production only observes *sales*, censored by your own stockouts in "
      "exactly the weeks that matter. This synthetic dataset carries "
      "unconstrained demand as ground truth; a real deployment must reconstruct "
      "it from censored sales first, which the README lists as a prerequisite |")
    a("| Strategy | Direct multi-horizon | Every row is "
      "(series, origin, horizon) with features computed only at or before the "
      "origin and `horizon` as a model feature. No recursion, so no error "
      "compounding over the 13-week score |")
    a("| Holdout | **Single forecast origin**, 13 horizons | Stand at one week "
      "and forecast the next 13. This is the decision a planner actually makes "
      "and is strictly harder than rolling one-step-ahead, which would let the "
      "model see an actual from week 149 while scoring week 150 |")
    a("| Series | 4,852 | `SOCIAL_TREND` series average only ~17 active weeks, "
      "so lag-52 features are null for most of the portfolio and were dropped |")
    a("")
    a("### Feature table, registered in Feature Engineering")
    a("")
    a("```text")
    a(table(f"""
SELECT COUNT(*) AS feature_rows,
       COUNT(DISTINCT sku_id) AS skus,
       COUNT(DISTINCT retailer_code) AS retailers,
       COUNT(DISTINCT week_end_date) AS origin_weeks,
       MIN(week_end_date) AS first_week, MAX(week_end_date) AS last_week
FROM {C}.ml.demand_features_weekly
"""))
    a("```")
    a("")
    a("The training set is assembled through a `FeatureLookup` keyed on")
    a("(`sku_id`, `retailer_code`) with `origin_week` as the timestamp lookup key, so")
    a("the point-in-time join is declared once and reused at inference instead of")
    a("being reimplemented in a scoring script. `retailer_code` is deliberately a")
    a("primary key of the feature table rather than a feature column — including it")
    a("as both makes `create_table` fail with `COLUMN_ALREADY_EXISTS`.")
    a("")

    # 2. leaderboard
    a("## 2. The sweep — leaderboard")
    a("")
    a("Seven candidates across six families, one MLflow experiment, identical")
    a("metrics on an identical holdout. Pulled back out of the tracking server with")
    a("`mlflow.search_runs()`, filtered to a single `sweep_id` so the table is an")
    a("exact record of one execution rather than an accumulation across runs.")
    a("")
    lb = pathlib.Path("evidence/metrics/forecast_leaderboard.csv")
    rows: list[dict[str, str]] = []
    if lb.exists():
        with lb.open() as fh:
            rows = list(csv.DictReader(fh))
    if rows:
        cols = ["rank", "algorithm", "family", "wape_pct", "mape_nonzero_pct",
                "bias_pct", "rmse", "pinball_p50", "pinball_p90",
                "p90_coverage_pct", "train_seconds", "run_id"]
        a("| " + " | ".join(cols) + " |")
        a("|" + "|".join(["---"] * len(cols)) + "|")
        for r in rows:
            a("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    a("")
    a("**WAPE is the headline** because it is volume-weighted and therefore stable")
    a("on intermittent series. The `mape_nonzero_pct` column shows why a per-row")
    mapes = [float(r["mape_nonzero_pct"]) for r in rows]
    a(f"MAPE average is not usable here — {min(mapes):.0f}% to {max(mapes):.0f}% — since it is dominated by")  # noqa: E501
    a("near-zero denominators on slow sellers. It is reported because buyers ask")
    a("for it, and the gap between the two is itself the argument.")
    a("")
    a("Three findings worth stating:")
    a("")
    by_algo = {r["algorithm"]: float(r["wape_pct"]) for r in rows}
    champ_row = rows[0]
    champ_w = float(champ_row["wape_pct"])
    base_w = by_algo["seasonal_naive"]
    weak = [a_ for a_, w_ in by_algo.items()
            if a_ not in (champ_row["algorithm"], "seasonal_naive") and w_ > base_w - 10]
    a(f"1. **Gradient boosting wins decisively** — {champ_w:.1f}% WAPE ({champ_row['algorithm']}) against a {base_w:.1f}%")  # noqa: E501
    a(f"   seasonal-naive baseline, a {base_w - champ_w:.1f}-point / {100 * (base_w - champ_w) / base_w:.1f}% relative improvement.")  # noqa: E501
    a("   Across three executions of this notebook the champion's WAPE was 67.36%,")
    a("   67.56% and 67.42%: a ~0.2-point run-to-run spread from multi-threaded")
    a("   LightGBM. The ranking at the top never changed; quote the headline as")
    a("   'about 67.4%', not to two decimals.")
    a(f"2. **{len(weak)} candidates ({', '.join(f'`{w_}`' for w_ in weak)}) land within 10 points of the")  # noqa: E501
    a("   baseline or worse.** Not every family suits this problem and the")
    a("   leaderboard says so. `sklearn_hist_gbm` in particular is unstable under")
    a(f"   Poisson loss: {by_algo['sklearn_hist_gbm']:.1f}% on this run against 93.0% on the previous one,")  # noqa: E501
    a("   with identical hyperparameters and the same hash-selected training rows;")
    a("   the join feeding it was the only change. The cause was not isolated. It")
    a("   is reported as measured, not tuned until it behaves.")
    a("3. **The first version of this sweep was useless and the reason is")
    a("   instructive.** See §3.")
    a("")

    # 3. what went wrong
    a("## 3. Two modelling errors found and fixed")
    a("")
    a("Recorded because the corrected leaderboard is only meaningful if the")
    a("mistakes behind it are visible.")
    a("")
    a("**No forward calendar.** The first run scored every boosting model *worse*")
    a("than a 13-week trailing mean, with +62% to +106% bias. The cause: the")
    a("holdout stands at a single forecast origin, so every origin-based calendar")
    a("feature is **constant across all 50,574 test rows**, and the model had no")
    a("way to distinguish a July target week from a late-September one except")
    a("through `horizon`. Adding calendar features for the week being *predicted*")
    a("— which is known at forecast time, so not leakage — moved XGBoost from")
    a("148.2% to 72.9% WAPE.")
    a("")
    a("**Squared error on an overdispersed count.** Weekly demand here is a")
    a("non-negative count with a long right tail; least squares chases that tail")
    a("and over-predicts the bulk of the portfolio. Switching the three boosting")
    a("candidates to a Poisson objective cut bias from +69.7% to +5.2% on the")
    a("eventual champion. `HistGradientBoosting` with Poisson loss initially")
    a("*diverged* — RMSE 5,842 and +389% bias, an order of magnitude worse than")
    a("naive — and needed shallower trees, a lower learning rate and stronger leaf")
    a("regularisation to stabilise at 93.0%.")
    a("")
    a("**Non-reproducible sampling.** The training subsample used `sampleBy`, which")
    a("is only deterministic for a fixed partitioning. Because the feature table is")
    a("rebuilt each run, the sample drifted and the champion flipped between")
    a("XGBoost and LightGBM on consecutive executions (72.73 vs 71.41 WAPE for the")
    a("same algorithm). Membership is now a hash of the row key, independent of how")
    a("Spark laid the data out.")
    a("")

    # 4. accuracy breakdown
    a("## 4. Where the forecast is good and where it is not")
    a("")
    a("The portfolio number hides the shape of the problem. Queried through the")
    a("governed metric view `gold.mv_forecast_accuracy`, not recomputed ad hoc:")
    a("")
    q_lf = f"""
SELECT `License Family`,
       ROUND(MEASURE(`WAPE Pct`), 2)         AS wape_pct,
       ROUND(MEASURE(`Bias Pct`), 2)         AS bias_pct,
       ROUND(MEASURE(`P90 Coverage Pct`), 2) AS p90_coverage_pct,
       MEASURE(`Actual Units`)               AS actual_units,
       MEASURE(`Forecast Rows`)              AS forecast_rows
FROM {G}.mv_forecast_accuracy
GROUP BY ALL ORDER BY wape_pct
"""
    a("```sql")
    a(q_lf.strip())
    a("```")
    a("")
    a("```text")
    a(table(q_lf))
    a("```")
    a("")
    cols_lf, rows_lf = sql(q_lf)
    fam = {r[0]: dict(zip(cols_lf, r, strict=True)) for r in rows_lf}
    ev, st, ft = fam["EVERGREEN"], fam["SOCIAL_TREND"], fam["FILM_TIE_IN"]
    a("This is the most useful table in the stage:")
    a("")
    a(f"* **`EVERGREEN` forecasts at {float(ev['wape_pct']):.1f}% WAPE with {float(ev['bias_pct']):+.1f}% bias and {float(ev['p90_coverage_pct']):.1f}% p90")  # noqa: E501
    a("  coverage.** On predictable demand the model is genuinely good and the")
    a("  interval is calibrated.")
    a(f"* **`SOCIAL_TREND` forecasts at {float(st['wape_pct']):.1f}% WAPE with {float(st['p90_coverage_pct']):.1f}% p90 coverage.** Viral")  # noqa: E501
    a("  spikes are close to unforecastable from history, *and* the upper quantile")
    a(f"  under-covers by {90 - float(st['p90_coverage_pct']):.0f} points precisely where cover matters most. Anyone")  # noqa: E501
    a("  claiming a single portfolio accuracy number for this business is hiding")
    a("  this row.")
    a(f"* **`FILM_TIE_IN` sits between, over-forecasting by {float(ft['bias_pct']):.0f}%** — release windows")  # noqa: E501
    a("  are partly predictable, and the model errs long.")
    a("")
    a("The operational consequence is specific: the p90 drives `fct_stockout_risk`,")
    a("so on trend items the risk table understates exposure. That is the honest")
    a("limitation to put in front of a planner, and it is why Stage C2's allocator")
    a("takes contractual commitment and margin into account rather than trusting")
    a("the quantile alone.")
    a("")
    a("### Accuracy decay by horizon")
    a("")
    a("```text")
    a(table(f"""
SELECT horizon,
       ROUND(100.0 * SUM(ABS(forecast_p50 - actual_units)) / NULLIF(SUM(actual_units),0), 2)
         AS wape_pct,
       ROUND(100.0 * SUM(forecast_p50 - actual_units) / NULLIF(SUM(actual_units),0), 2)
         AS bias_pct,
       SUM(actual_units) AS actual_units
FROM {G}.fct_demand_forecast_eval
GROUP BY horizon ORDER BY horizon
"""))
    a("```")
    a("")

    # 5. p90 method
    a("## 5. p90: derived interval beat a native quantile model")
    a("")
    a("Two methods were scored on the same holdout. The derived interval scales the")
    a("point forecast by the per-horizon 90th percentile of the validation residual")
    a("ratio; the native model is a LightGBM quantile regressor at alpha 0.9.")
    a("")
    a("| p90 method | pinball_p90 | coverage % | mean p90 |")
    a("|---|---:|---:|---:|")
    a("| derived residual ratio | **9.5261** | 89.95 | 56.5 |")
    a("| native LightGBM quantile | 9.7005 | 89.61 | 74.8 |")
    a("")
    a("The simpler method won on both pinball loss and calibration, and is far less")
    a("conservative — a mean p90 of 56.5 units against 74.8. The native quantile")
    a("model buys its coverage by over-reserving, which in this business means")
    a("inventory that ends up cleared at markdown. The derived interval was")
    a("selected on that evidence rather than on the assumption that the more")
    a("sophisticated option is better.")
    a("")

    # 6. registration
    a("## 6. Registered in Unity Catalog with both aliases")
    a("")
    a("Both the winner and the runner-up are logged through")
    a("`FeatureEngineeringClient.log_model`, so each carries its `FeatureLookup`")
    a("spec and inference can resolve features by key instead of requiring the")
    a("caller to rebuild 35 columns correctly.")
    a("")
    # This metastore's information_schema exposes no model_versions or
    # model_version_aliases view, so the registry state is read from the Unity
    # Catalog models API instead -- which is the authoritative source anyway.
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile="vm-summer-classic")
    rm = w.registered_models.get(MODEL, include_aliases=True)
    alias_map: dict[int, list[str]] = {}
    for al in (rm.aliases or []):
        alias_map.setdefault(int(al.version_num), []).append(str(al.alias_name))

    versions = sorted(w.model_versions.list(MODEL), key=lambda v: int(v.version))
    lines = [
        f"{'version':>8}  {'aliases':<24} {'status':<10} {'source_run_id':<34}",
        f"{'-' * 8}  {'-' * 24} {'-' * 10} {'-' * 34}",
    ]
    for v in versions:
        al = ",".join(f"@{x}" for x in sorted(alias_map.get(int(v.version), []))) or "-"
        status = str(v.status).replace("ModelVersionInfoStatus.", "")
        lines.append(
            f"{int(v.version):>8}  {al:<24} {status:<10} {(v.run_id or '-'):<34}"
        )
    lines.append(f"({len(versions)} versions)")
    a("```text")
    a("\n".join(lines))
    a("```")
    a("")
    a("Alias assignments — this is what every downstream consumer loads, so no")
    a("notebook, app or serving endpoint references a version number:")
    a("")
    champ_algo = chal_algo = "-"
    champ_wape = chal_wape = "-"
    if lb.exists():
        with lb.open() as fh:
            lbrows = list(csv.DictReader(fh))
        if len(lbrows) >= 2:
            champ_algo, champ_wape = lbrows[0]["algorithm"], lbrows[0]["wape_pct"]
            chal_algo, chal_wape = lbrows[1]["algorithm"], lbrows[1]["wape_pct"]
    a("| Alias | Version | Algorithm | Holdout WAPE % | Resolved reference |")
    a("|---|---:|---|---:|---|")
    for alias, algo, wp in (
        ("champion", champ_algo, champ_wape),
        ("challenger", chal_algo, chal_wape),
    ):
        vers = [v for v, names in alias_map.items() if alias in names]
        vnum = vers[0] if vers else "-"
        a(f"| `@{alias}` | {vnum} | {algo} | {wp} | `models:/{MODEL}@{alias}` |")
    a("")
    a("Versions accumulate across notebook runs, which is the point of a registry:")
    a("earlier versions stay auditable and the aliases always resolve to the")
    a("current sweep. Promotion of the challenger is an alias move, not a re-export.")
    a("")

    # 7. forward forecast
    a("## 7. 13 weeks forward")
    a("")
    a("Scored in a single pass from the last actual week, p50 and p90. The window")
    a("lands in the Q4 build, which is when a licensed-toy planner is most exposed.")
    a("")
    a("```text")
    a(table(f"""
SELECT week_end_date, horizon, COUNT(*) AS series,
       ROUND(SUM(forecast_p50)) AS total_p50,
       ROUND(SUM(forecast_p90)) AS total_p90,
       MAX(model_alias) AS model_alias, MAX(model_version) AS model_version
FROM {G}.fct_demand_forecast
GROUP BY week_end_date, horizon ORDER BY week_end_date
"""))
    a("```")
    a("")
    a("### Forecast vs actual on the holdout")
    a("")
    a("```text")
    a(table(f"""
SELECT sku_id, retailer_code, week_end_date, horizon, actual_units,
       forecast_p50, forecast_p90, abs_error, license_family, lifecycle_stage
FROM {G}.fct_demand_forecast_eval
ORDER BY actual_units DESC LIMIT 12
"""))
    a("```")
    a("")

    # 8. risk table
    a("## 8. `gold.fct_stockout_risk` — the spine of C2, D, E and F")
    a("")
    a("One row per at-risk SKU x retailer: will it run out, when, and what is it")
    a("worth. Supply is deliberately **shared** — inventory is held at SKU x DC, not")
    a("per retailer, so each retailer's claim on a SKU is scaled by its share of")
    a("forecast demand. That is what makes the allocation genuinely constrained.")
    a("")
    a("### Tiering, and why it is necessary")
    a("")
    a("```text")
    a(table(f"""
SELECT risk_tier, COUNT(*) AS combinations,
       MIN(weeks_to_stockout) AS min_weeks, MAX(weeks_to_stockout) AS max_weeks,
       ROUND(SUM(units_at_risk)) AS units_at_risk,
       ROUND(SUM(revenue_at_risk_usd)) AS revenue_at_risk_usd,
       ROUND(AVG(severity_score), 1) AS avg_severity
FROM {G}.fct_stockout_risk
GROUP BY risk_tier
ORDER BY CASE risk_tier WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                        WHEN 'MEDIUM' THEN 3 ELSE 4 END
"""))
    a("```")
    a("")
    a("A 13-week cumulative projection flags about two thirds of the portfolio, and")
    a("that is not an arithmetic error — it is what *will demand exceed supply")
    a("within 13 weeks* means for a business carrying roughly 13 weeks of cover. It")
    a("is a replenishment signal, not a 3,000-item alarm, so rows are tiered by how")
    a("soon the shortfall lands. **CRITICAL and HIGH — 884 combinations — are the")
    a("planner's actual Monday queue**; the rest is the ordering book. The app, the")
    a("agent and Genie all filter on this tier rather than each re-deriving a")
    a("threshold.")
    a("")
    a("One statistical correction along the way: cumulative p90 is **not** the")
    a("running sum of weekly p90s. The sum of quantiles is not the quantile of the")
    a("sum — adding thirteen independent weekly p90s produces roughly a p99.9 of the")
    a("13-week total. The weekly sigma is recovered from the p90-p50 gap, variances")
    a("accumulate, and the cumulative quantile is rebuilt from the cumulative mean,")
    a("so the interval widens with the square root of the horizon rather than")
    a("linearly.")
    a("")
    a("### Top 15 by severity — the head of the worklist")
    a("")
    a("```text")
    a(table(f"""
SELECT sku_id, retailer_code, brand, license_family, lifecycle_stage, risk_tier,
       projected_stockout_week, weeks_to_stockout, units_at_risk,
       revenue_at_risk_usd, severity_score
FROM {G}.fct_stockout_risk
ORDER BY severity_score DESC, revenue_at_risk_usd DESC LIMIT 15
"""))
    a("```")
    a("")
    a("### The constrained-allocation decision")
    a("")
    a("SKUs where two or more retailers are short of the *same* pool. This is")
    a("precisely the decision Stage C2's allocator has to make and that today")
    a("happens verbally in a Monday meeting with no system of record.")
    a("")
    a("```text")
    a(table(f"""
SELECT sku_id, brand, license_family,
       COUNT(DISTINCT retailer_code) AS retailers_at_risk,
       MAX(sku_on_hand_units) AS sku_on_hand_units,
       MAX(sku_inbound_horizon_units) AS sku_inbound_units,
       ROUND(SUM(units_at_risk)) AS combined_units_at_risk,
       ROUND(SUM(revenue_at_risk_usd)) AS combined_revenue_at_risk_usd
FROM {G}.fct_stockout_risk
GROUP BY sku_id, brand, license_family
HAVING COUNT(DISTINCT retailer_code) >= 2
ORDER BY combined_revenue_at_risk_usd DESC LIMIT 10
"""))
    a("```")
    a("")
    a("### Risk concentration")
    a("")
    a("```text")
    a(table(f"""
SELECT license_family, COUNT(*) AS at_risk_combinations,
       ROUND(AVG(weeks_to_stockout), 1) AS avg_weeks_to_stockout,
       ROUND(SUM(units_at_risk)) AS units_at_risk,
       ROUND(SUM(revenue_at_risk_usd)) AS revenue_at_risk_usd
FROM {G}.fct_stockout_risk
GROUP BY license_family ORDER BY revenue_at_risk_usd DESC
"""))
    a("```")
    a("")
    a("```text")
    a(table(f"""
SELECT retailer_code, retailer_label, COUNT(*) AS at_risk_combinations,
       ROUND(SUM(revenue_at_risk_usd)) AS revenue_at_risk_usd,
       ROUND(AVG(severity_score), 1) AS avg_severity
FROM {G}.fct_stockout_risk
GROUP BY retailer_code, retailer_label ORDER BY revenue_at_risk_usd DESC
"""))
    a("```")
    a("")

    # 9. lineage
    a("## 9. Lineage: Gold fact to feature table to model to forecast")
    a("")
    a("```text")
    a(table(f"""
SELECT source_table_full_name AS source, target_table_full_name AS target
FROM system.access.table_lineage
WHERE (target_table_catalog = '{C}' OR source_table_catalog = '{C}')
  AND (target_table_name IN ('demand_features_weekly','fct_demand_forecast',
                             'fct_stockout_risk','fct_demand_forecast_eval')
    OR source_table_name IN ('demand_features_weekly','fct_demand_forecast'))
GROUP BY source_table_full_name, target_table_full_name
ORDER BY target, source
""", max_col=66))
    a("```")
    a("")

    dest = pathlib.Path("evidence/03_ml_forecast.md")
    dest.write_text("\n".join(out))
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
