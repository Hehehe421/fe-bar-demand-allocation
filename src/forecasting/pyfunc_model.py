"""Servable demand forecaster: matrix construction + p50 + p90 in one pyfunc.

Why this exists
---------------
Stage C1 originally logged the *bare estimator* (a LightGBM / XGBoost object).
That cannot be served or batch-scored correctly, for a reason independent of the
online-store failure on the first endpoints:

* The estimator was trained on a matrix built by ``prep()``, which derives four
  target-week calendar columns from ``origin_week + horizon`` and casts the
  categoricals to a fixed set of levels. None of that lives inside the estimator,
  so a caller (or ``fe.score_batch``) supplying keys + features would hit a
  missing-column error, or worse, mis-encoded categories.
* p90 is produced *outside* the estimator (a native quantile model, or a derived
  per-horizon residual ratio), so the registered model only ever produced p50.

This wrapper moves all three into the model artifact, so the model's contract is
the one the business uses: **keys + features in, p50 and p90 out.**

``build_matrix`` is the single implementation of the matrix construction. The
training notebook imports it too, so training and serving cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence

import mlflow.pyfunc
import numpy as np
import pandas as pd

TARGET_CAL = ["tgt_week_of_year", "tgt_sin_woy", "tgt_cos_woy", "tgt_weeks_to_q4_peak"]
ORIGIN_COL = "origin_week"


def build_matrix(
    df: pd.DataFrame,
    model_features: Sequence[str],
    categorical: Sequence[str],
    levels: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """Feature matrix the estimators were trained on.

    Calendar features describe the week being PREDICTED (origin + horizon), which
    is known at forecast time and is therefore not leakage. Columns a caller
    omits become NaN: the model signature marks features optional.

    ``levels`` pins each categorical to the training-time level set. Pass it at
    inference; without it, level codes depend on whichever values the request
    happens to contain and LightGBM would silently mis-split.
    """
    d = df.copy()
    origin = pd.to_datetime(d[ORIGIN_COL])
    target_week = origin + pd.to_timedelta(d["horizon"].astype(int) * 7, unit="D")
    woy = target_week.dt.isocalendar().week.astype("float32").to_numpy()
    d["tgt_week_of_year"] = woy
    d["tgt_sin_woy"] = np.sin(2 * np.pi * woy / 52.0)
    d["tgt_cos_woy"] = np.cos(2 * np.pi * woy / 52.0)
    d["tgt_weeks_to_q4_peak"] = np.minimum(np.abs(woy - 47.0), 52.0 - np.abs(woy - 47.0))

    X = d.reindex(columns=list(model_features)).copy()
    for c in categorical:
        if levels is not None:
            X[c] = pd.Categorical(X[c], categories=levels[c])
        else:
            X[c] = X[c].astype("category")
    for c in X.columns:
        if c not in categorical:
            X[c] = pd.to_numeric(X[c], errors="coerce").astype("float32")
    return X


class DemandForecaster(mlflow.pyfunc.PythonModel):
    """keys + features -> ``forecast_p50`` / ``forecast_p90``.

    ``p90_model`` is the native quantile regressor when it won the calibration
    comparison; otherwise ``q90_table`` (per-horizon residual ratio) is applied
    to the p50. Exactly one of the two is used, recorded in ``p90_method``.
    """

    def __init__(
        self,
        p50_model: object,
        model_features: Sequence[str],
        categorical: Sequence[str],
        levels: dict[str, list[str]],
        p90_model: object | None = None,
        q90_table: dict[int, float] | None = None,
    ) -> None:
        self.p50_model = p50_model
        self.p90_model = p90_model
        self.q90_table = q90_table or {}
        self.model_features = list(model_features)
        self.categorical = list(categorical)
        self.levels = {k: list(v) for k, v in levels.items()}

    @property
    def p90_method(self) -> str:
        return "native_quantile" if self.p90_model is not None else "derived_residual_ratio"

    def predict(
        self, context: object, model_input: pd.DataFrame, params: dict | None = None
    ) -> pd.DataFrame:
        X = build_matrix(model_input, self.model_features, self.categorical, self.levels)
        p50 = np.clip(np.asarray(self.p50_model.predict(X), dtype="float64"), 0, None)
        if self.p90_model is not None:
            p90 = np.clip(np.asarray(self.p90_model.predict(X), dtype="float64"), 0, None)
        else:
            horizons = model_input["horizon"].astype(int).to_numpy()
            mult = np.array([self.q90_table.get(int(h), 1.3) for h in horizons])
            p90 = p50 * mult
        # an upper quantile can never sit below the median
        p90 = np.maximum(p90, p50)
        return pd.DataFrame(
            {"forecast_p50": np.round(p50, 2), "forecast_p90": np.round(p90, 2)},
            index=model_input.index,
        )
