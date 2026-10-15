"""Runtime settings for the Allocation Command Center.

In the deployed app every value arrives as an environment variable declared in
``app.yaml`` (resources use ``valueFrom``, so no id is hardcoded). For local
development the same names fall back to ``src/config.py``, the repo's single source
of truth, so there is exactly one place a catalog, endpoint or host is defined.
"""

from __future__ import annotations

import os
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
try:  # present when running from the repo, absent in the deployed bundle
    sys.path.insert(0, str(_REPO))
    import src.config as _c
except Exception:  # noqa: BLE001
    _c = None


def _get(name: str, attr: str | None = None, default: str = "") -> str:
    v = os.environ.get(name)
    if v:
        return v
    if _c is not None and attr is not None:
        return str(getattr(_c, attr))
    return default


WAREHOUSE_ID = _get("DATABRICKS_WAREHOUSE_ID", "WAREHOUSE_ID")
CATALOG = _get("UC_CATALOG", "CATALOG", "toy_demand_allocation")
AGENT_ENDPOINT = _get("AGENT_ENDPOINT", "AGENT_ENDPOINT_NAME")
FORECASTER_ENDPOINT = _get("FORECASTER_ENDPOINT", "FORECASTER_ENDPOINT")
GENIE_SPACE_ID = _get("GENIE_SPACE_ID")
if not GENIE_SPACE_ID and (_REPO / "evidence/metrics/genie_space.json").exists():
    import json

    GENIE_SPACE_ID = json.loads((_REPO / "evidence/metrics/genie_space.json").read_text())[
        "space_id"
    ]

PG_HOST = _get("PG_HOST", "LAKEBASE_HOST")
PG_DATABASE = _get("PG_DATABASE", "LAKEBASE_DATABASE")
PG_ENDPOINT = _get("PG_ENDPOINT", "LAKEBASE_ENDPOINT")

#: Feature table the real-time what-if reads its one input row from.
FEATURE_TABLE = f"{CATALOG}.ml.demand_features_weekly"
HORIZON = 13

#: categorical inputs of the forecaster (everything else is numeric)
CATEGORICAL = ("brand", "category", "license_family", "lifecycle_stage", "channel")

RETAILERS = ("MASS_A", "MASS_B", "ETAIL_C")
TIERS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
