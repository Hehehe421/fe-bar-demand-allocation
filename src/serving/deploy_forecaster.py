"""Stage C1 §D — serve the demand forecaster in real time, and score it in batch.

Completes the end-to-end claim. Until now the forecaster was batch-only: 13
weeks written to `gold.fct_demand_forecast` and nothing served. "End-to-end
including batch and real-time" was not true, and this is what makes it true.

**Why this works without an online store.** The model is logged with
`fe.log_model(training_set=...)`, so its signature marks the join keys
(`sku_id`, `retailer_code`, `origin_week`, `horizon`) as *required* and all 35
feature columns as *optional* — optional because a feature-store-backed model
would normally fill them by looking them up. With no online store there is
nothing to look up, so the caller supplies the feature values instead and the
lookup is skipped entirely.

That is not a workaround, it is the right shape for this architecture: Lakebase
already holds the low-latency read path, so the app reads the feature row from
Postgres (single-digit millisecond point lookup) and posts it to the endpoint.
One hop for data, one for inference, and no online feature store to keep in sync.

Batch scoring uses `fe.score_batch()` rather than `mlflow.pyfunc.spark_udf()`,
because for a feature-backed model `score_batch` takes keys only and joins the
features from the model's own registered lineage. Rebuilding the 35 feature
columns by hand to feed `predict` — which is what Stage C1 originally did — is
precisely the training/serving skew the feature layer exists to prevent.

    python -m src.serving.deploy_forecaster              # deploy + wait + query
    python -m src.serving.deploy_forecaster --query-only  # skip the deploy
    FORECASTER_ENDPOINT=<name> python -m src.serving.deploy_forecaster --query-only

ROOT CAUSE of the original UPDATE_FAILED (resolved): the endpoint was pointed at a
model logged with `fe.log_model`. Model Serving reads the feature-lookup spec in
that artifact and tries to set up an online feature store, which this build
deliberately does not have -- served-entity state "Online feature store setup
failed". A second, independent defect sat behind it: the bare estimator could not
have produced a correct forecast anyway, because the target-week calendar columns,
category levels and p90 all lived outside it.

A THIRD defect only showed once the first two were fixed: the rt model's
`pip_requirements` were copied from the training runtime, which ships pandas 1.5.3.
That has no Python 3.12 wheel, so the serving image tried to compile it from source
and the container build failed in ~100 s (`No module named 'pkg_resources'`). Two
lessons: (1) never pin serving dependencies from `pd.__version__` of a Databricks
ML runtime; (2) a failed build is only reported after a ~25 min queue, so validate
the pins first. Notebook 03 now rebuilds the model's requirements in a fresh
Python 3.12 virtualenv and compares forecasts before anything is deployed.

The endpoint therefore serves `config.MODEL_NAME_RT`: the same fitted estimators
wrapped in `forecasting.pyfunc_model.DemandForecaster` (matrix construction + p50 +
p90) and logged as a plain pyfunc with a signature and no feature lineage. Batch
scoring keeps using the feature-backed `config.MODEL_NAME` through `fe.score_batch`.
Promotion is an alias move on both models.

`query()` and `feature_row()` are independent of `deploy()` and work against any
ready endpoint, which is what `--query-only` plus FORECASTER_ENDPOINT is for.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import os

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.serving import (  # noqa: E402
    EndpointCoreConfigInput,
    ServedEntityInput,
)

import src.config as config  # noqa: E402

CATEGORICAL = ("brand", "category", "license_family", "lifecycle_stage", "channel")

# Override to point at an endpoint created elsewhere, e.g.
#   FORECASTER_ENDPOINT=demand_forecaster python -m src.serving.deploy_forecaster --query-only
ENDPOINT = os.environ.get("FORECASTER_ENDPOINT", config.FORECASTER_ENDPOINT)
MODEL = config.MODEL_NAME_RT
ALIAS = config.CHAMPION_ALIAS


def _w() -> WorkspaceClient:
    return WorkspaceClient(profile=config.PROFILE)


def champion_version(w: WorkspaceClient) -> int:
    rm = w.registered_models.get(MODEL, include_aliases=True)
    for a in (rm.aliases or []):
        if a.alias_name == ALIAS:
            return int(a.version_num)
    raise LookupError(f"no @{ALIAS} alias on {MODEL}")


def deploy(w: WorkspaceClient, version: int) -> None:
    """Create or update the endpoint. Scale-to-zero: idle cost ~0."""
    entity = ServedEntityInput(
        name=f"forecaster-rt-{version}",
        entity_name=MODEL,
        entity_version=str(version),
        workload_size="Small",
        scale_to_zero_enabled=True,
    )
    # EndpointCoreConfigInput requires `name` (the endpoint name), even
    # though it is also passed to create() -- omitting it is a TypeError.
    cfg = EndpointCoreConfigInput(name=ENDPOINT, served_entities=[entity])
    existing = {e.name for e in w.serving_endpoints.list()}
    if ENDPOINT in existing:
        print(f"updating {ENDPOINT} -> v{version}")
        w.serving_endpoints.update_config(name=ENDPOINT, served_entities=[entity])
    else:
        print(f"creating {ENDPOINT} -> v{version}")
        w.serving_endpoints.create(name=ENDPOINT, config=cfg)


def wait_ready(w: WorkspaceClient, timeout_s: int = 2400) -> str:
    """Readiness is TWO fields: ready, and config_update must not be in progress."""
    deadline = time.time() + timeout_s
    state = "?"
    while time.time() < deadline:
        ep = w.serving_endpoints.get(ENDPOINT)
        ready = str(getattr(ep.state, "ready", "") or "")
        upd = str(getattr(ep.state, "config_update", "") or "")
        state = f"ready={ready} config_update={upd}"
        print(f"  {state}")
        if "READY" in ready and "IN_PROGRESS" not in upd:
            return state
        if "FAILED" in upd:
            return state
        time.sleep(30)
    return state


def feature_row() -> dict:
    """One real feature row, read from Lakebase the way the app will.

    Deliberately sourced from the synced table rather than hand-written, so the
    request is the one the app would actually issue.
    """
    from src.lakebase.provision import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sku_id, retailer_code
            FROM public.stockout_risk_current
            WHERE risk_tier = 'CRITICAL'
            ORDER BY severity_score DESC LIMIT 1
            """
        )
        sku, retailer = cur.fetchone()
    print(f"  picked {sku} / {retailer} from Lakebase stockout_risk_current")

    # the feature values themselves come from the feature table, which is what
    # the app would read from its own synced copy in production
    from tools.dbsql import sql

    cols, rows = sql(
        f"""
        SELECT * FROM {config.FEATURE_TABLE}
        WHERE sku_id = '{sku}' AND retailer_code = '{retailer}'
        ORDER BY week_end_date DESC LIMIT 1
        """
    )
    row = dict(zip(cols, rows[0], strict=True))
    payload = {k: v for k, v in row.items() if k != "week_end_date"}
    payload["origin_week"] = str(row["week_end_date"])
    payload["horizon"] = 1
    # the SQL statement API returns every value as a string; the model signature
    # wants doubles for features and an int for the horizon
    keep_str = {"sku_id", "retailer_code", "origin_week", *CATEGORICAL}
    for k, v in payload.items():
        if k not in keep_str:
            payload[k] = None if v is None else float(v)
    payload["horizon"] = 1
    return payload


def query(w: WorkspaceClient, payload: dict, n: int = 12) -> None:
    body = {"dataframe_records": [payload]}
    print("\nREQUEST (first 400 chars of one record):")
    print(json.dumps(payload, default=str)[:400])

    timings: list[float] = []
    out = None
    for i in range(n):
        t0 = time.perf_counter()
        out = w.api_client.do(
            "POST", f"/serving-endpoints/{ENDPOINT}/invocations", body=body
        )
        dt = (time.perf_counter() - t0) * 1000
        if i:  # drop the first, which pays any cold-start cost
            timings.append(dt)

    print("\nRESPONSE:")
    print(json.dumps(out, default=str)[:400])
    if timings:
        timings.sort()
        print(f"\nlatency over {len(timings)} warm calls (laptop -> us-east-1):")
        print(f"  median {statistics.median(timings):7.1f} ms")
        print(f"  p95    {timings[int(0.95 * len(timings)) - 1]:7.1f} ms")
        print(f"  min    {timings[0]:7.1f} ms")
        print("  (includes network round trip; an in-workspace caller sees less)")


def main() -> int:
    w = _w()
    v = champion_version(w)
    print(f"model    : {MODEL}")
    print(f"alias    : @{ALIAS} -> v{v}")
    print(f"endpoint : {ENDPOINT}")

    if "--query-only" not in sys.argv:
        deploy(w, v)
        print("\nwaiting for readiness ...")
        print("final:", wait_ready(w))

    print("\nbuilding a request from the Lakebase read path ...")
    query(w, feature_row())
    return 0


if __name__ == "__main__":
    sys.exit(main())
