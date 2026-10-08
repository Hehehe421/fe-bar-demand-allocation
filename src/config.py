"""Single source of truth for every Unity Catalog name, compute id and endpoint
used by this build.

Nothing in `notebooks/`, `src/`, `app/` or `tests/` may hardcode a catalog,
schema, table, warehouse or model-serving endpoint name. Import it from here.

All data referenced by this module is SYNTHETIC, produced by
``src/datagen/generate.py`` from a fixed seed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

# --------------------------------------------------------------------------
# Workspace / compute
# --------------------------------------------------------------------------

PROFILE: Final[str] = os.environ.get("DBX_PROFILE", "vm-summer-classic")
WORKSPACE_HOST: Final[str] = "https://fevm-stable-classic-pqjg18.cloud.databricks.com"

#: Serverless Starter Warehouse (PRO) — used for all SQL/DDL and by Genie.
WAREHOUSE_ID: Final[str] = os.environ.get("DBX_WAREHOUSE_ID", "a6857db5911f7af1")

#: Single-node DBR 16.4 LTS ML cluster used for the multi-algorithm sweep.
ML_CLUSTER_NAME: Final[str] = "toy-demand-alloc-ml"
ML_CLUSTER_ID: Final[str] = os.environ.get("DBX_ML_CLUSTER_ID", "1008-205814-qhbfm7tc")
ML_RUNTIME: Final[str] = "16.4.x-cpu-ml-scala2.12"

# --------------------------------------------------------------------------
# Unity Catalog namespace
# --------------------------------------------------------------------------

CATALOG: Final[str] = os.environ.get("DBX_CATALOG", "toy_demand_allocation")

BRONZE: Final[str] = f"{CATALOG}.bronze"
SILVER: Final[str] = f"{CATALOG}.silver"
GOLD: Final[str] = f"{CATALOG}.gold"
ML: Final[str] = f"{CATALOG}.ml"

VOLUME: Final[str] = f"{BRONZE}.landing"
LANDING: Final[str] = f"/Volumes/{CATALOG}/bronze/landing"

#: Storage root the catalog was created against (pre-existing external location).
STORAGE_ROOT: Final[str] = (
    "s3://stable-classic-pqjg18-ext-s3-049629455384-e6feh9/toy_demand_allocation"
)


def fq(layer: str, table: str) -> str:
    """Fully-qualified name for ``table`` in ``layer`` ('bronze'|'silver'|'gold'|'ml')."""
    return f"{CATALOG}.{layer}.{table}"


# --------------------------------------------------------------------------
# Landing-zone sources (Stage A) — subdirectory under LANDING, and file format
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    name: str
    fmt: str
    grain: str
    comment: str

    @property
    def landing_path(self) -> str:
        return f"{LANDING}/{self.name}"

    @property
    def bronze_table(self) -> str:
        return fq("bronze", self.name)


SOURCES: Final[tuple[Source, ...]] = (
    Source(
        "product_master",
        "csv",
        "SKU",
        "Item master: brand, license family, lifecycle stage, case pack, unit cost, MSRP.",
    ),
    Source(
        "pos_weekly",
        "csv",
        "SKU x retailer x DC x week",
        "Retailer point-of-sale and on-hand by week. Arrives late, duplicates rows, "
        "and ETAIL_C reports cases rather than eaches.",
    ),
    Source(
        "orders",
        "csv",
        "PO line",
        "Purchase-order lines: requested vs confirmed vs shipped quantity and date. "
        "Basis for fill rate and OTIF.",
    ),
    Source(
        "inventory_snapshots",
        "csv",
        "SKU x DC x day",
        "Daily on-hand and in-transit units by distribution centre.",
    ),
    Source(
        "inbound_shipments",
        "csv",
        "ASN",
        "Inbound advance ship notices: original vs revised ETA, lead-time actuals.",
    ),
    Source(
        "supplier_docs",
        "pdf",
        "document",
        "Supplier invoices and product spec sheets (material composition, figure type).",
    ),
    Source(
        "product_images",
        "png",
        "image",
        "Product silhouettes with varied dominant colour; humanoid vs non-humanoid.",
    ),
)

SOURCES_BY_NAME: Final[dict[str, Source]] = {s.name: s for s in SOURCES}

# --------------------------------------------------------------------------
# Gold tables (Stage A output, consumed by every later stage)
# --------------------------------------------------------------------------

FCT_DEMAND_WEEKLY: Final[str] = fq("gold", "fct_demand_weekly")
FCT_ORDERS_FULFILLMENT: Final[str] = fq("gold", "fct_orders_fulfillment")
FCT_INBOUND_ETA: Final[str] = fq("gold", "fct_inbound_eta")
DIM_PRODUCT: Final[str] = fq("gold", "dim_product")
DIM_RETAILER_DC: Final[str] = fq("gold", "dim_retailer_dc")

#: Stage C1 output.
FCT_DEMAND_FORECAST: Final[str] = fq("gold", "fct_demand_forecast")
FCT_STOCKOUT_RISK: Final[str] = fq("gold", "fct_stockout_risk")

#: Stage D round-trip: Lakebase decisions synced back into UC.
FCT_ALLOCATION_DECISIONS: Final[str] = fq("gold", "fct_allocation_decisions")

#: Stage B quarantine tables (expectation failures land here).
QUARANTINE_SUFFIX: Final[str] = "_quarantine"

# --------------------------------------------------------------------------
# Metric views (Stage B) — the governed KPI layer
# --------------------------------------------------------------------------

MV_FILL_RATE: Final[str] = fq("gold", "mv_fill_rate")
MV_OTIF: Final[str] = fq("gold", "mv_otif")
MV_FORECAST_ACCURACY: Final[str] = fq("gold", "mv_forecast_accuracy")
MV_WEEKS_OF_SUPPLY: Final[str] = fq("gold", "mv_weeks_of_supply")

METRIC_VIEWS: Final[tuple[str, ...]] = (
    MV_FILL_RATE,
    MV_OTIF,
    MV_FORECAST_ACCURACY,
    MV_WEEKS_OF_SUPPLY,
)

# --------------------------------------------------------------------------
# ML (Stage C1)
# --------------------------------------------------------------------------

MODEL_NAME: Final[str] = fq("ml", "demand_forecaster")
MLFLOW_EXPERIMENT: Final[str] = "/Users/sixuan.he@databricks.com/toy_demand_allocation_forecast"
FEATURE_TABLE: Final[str] = fq("ml", "demand_features_weekly")

CHAMPION_ALIAS: Final[str] = "champion"
CHALLENGER_ALIAS: Final[str] = "challenger"

#: Weeks of forward forecast written to FCT_DEMAND_FORECAST.
FORECAST_HORIZON_WEEKS: Final[int] = 13

#: Quantiles produced by the forecaster.
QUANTILES: Final[tuple[float, ...]] = (0.5, 0.9)

# --------------------------------------------------------------------------
# GenAI (Stage C2)
# --------------------------------------------------------------------------

#: Preference order for resolving a Claude FMAPI endpoint AT RUNTIME.
#: Never hardcode a single endpoint — `src/agent/llm.py:resolve_llm_endpoint()`
#: walks this list and returns the first one the workspace reports as READY.
FMAPI_ENDPOINT_PREFERENCE: Final[tuple[str, ...]] = (
    "databricks-claude-sonnet-4-5",
    "databricks-claude-sonnet-4-6",
    "databricks-claude-sonnet-5",
    "databricks-claude-sonnet-5-5",
    "databricks-claude-haiku-4-5",
)

AGENT_MODEL_NAME: Final[str] = fq("ml", "allocation_agent")
AGENT_ENDPOINT_NAME: Final[str] = "toy-allocation-agent"
AGENT_EXPERIMENT: Final[str] = "/Users/sixuan.he@databricks.com/toy_demand_allocation_agent"
PROMPT_REGISTRY_NAME: Final[str] = fq("ml", "allocation_analyst_system_prompt")

# --------------------------------------------------------------------------
# Lakebase (Stage D)
# --------------------------------------------------------------------------

LAKEBASE_INSTANCE: Final[str] = "toy-demand-alloc-db"
LAKEBASE_CAPACITY: Final[str] = "CU_1"
LAKEBASE_DATABASE: Final[str] = "databricks_postgres"
LAKEBASE_APP_SCHEMA: Final[str] = "allocation"

#: UC catalog that fronts the Lakebase instance for synced tables.
LAKEBASE_UC_CATALOG: Final[str] = "toy_demand_alloc_online"

#: Gold tables replicated into Lakebase for the app's low-latency read path.
#: (keys are Lakebase table names, values are the UC gold sources)
SYNCED_TABLES: Final[dict[str, str]] = {
    "stockout_risk_current": FCT_STOCKOUT_RISK,
    "forecast_next_13w": FCT_DEMAND_FORECAST,
    "product_lookup": DIM_PRODUCT,
    "fill_rate_summary": fq("gold", "agg_fill_rate_summary"),
}

#: Native OLTP tables the app writes to (not mirrors of Delta tables).
OLTP_TABLES: Final[tuple[str, ...]] = ("allocation_decisions", "agent_interactions")

# --------------------------------------------------------------------------
# Genie (Stage E)
# --------------------------------------------------------------------------

GENIE_SPACE_NAME: Final[str] = "Demand & Allocation"

# --------------------------------------------------------------------------
# App (Stage F)
# --------------------------------------------------------------------------

APP_NAME: Final[str] = "allocation-command-center"

# --------------------------------------------------------------------------
# Business domain labels — SYNTHETIC, see src/datagen/generate.py
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Retailer:
    code: str
    label: str
    #: Contractual fill-rate commitment, drives allocation priority.
    fill_commitment: float
    #: ETAIL_C reports POS in cases, everyone else in eaches.
    reports_in_cases: bool = False
    dcs: tuple[str, ...] = field(default_factory=tuple)


DCS: Final[tuple[str, ...]] = ("DC_EAST", "DC_CENTRAL", "DC_WEST", "DC_SOUTH")

RETAILERS: Final[tuple[Retailer, ...]] = (
    Retailer("MASS_A", "Mass Retail Account A", 0.97, False, DCS),
    Retailer("MASS_B", "Mass Retail Account B", 0.95, False, DCS),
    Retailer("ETAIL_C", "E-tail Account C", 0.93, True, ("DC_EAST", "DC_WEST")),
)

RETAILERS_BY_CODE: Final[dict[str, Retailer]] = {r.code: r for r in RETAILERS}

BRANDS: Final[tuple[str, ...]] = ("Snugglewick", "Titan Squad", "Glowberry Pets")

#: Brand -> product category.
BRAND_CATEGORY: Final[dict[str, str]] = {
    "Snugglewick": "PLUSH",
    "Titan Squad": "ACTION_FIGURE",
    "Glowberry Pets": "PLUSH",
}

LICENSE_FAMILIES: Final[tuple[str, ...]] = ("EVERGREEN", "FILM_TIE_IN", "SOCIAL_TREND")

LIFECYCLE_STAGES: Final[tuple[str, ...]] = ("LAUNCH", "GROWTH", "MATURE", "DECLINE", "EOL")

# --------------------------------------------------------------------------
# Deck / business-case input assumptions
# --------------------------------------------------------------------------
# These are INPUT ASSUMPTIONS for the sensitivity table, not measured results
# and not claims about any real business. Every *measured* number in the deck
# comes from evidence/metrics/kpi_baseline.csv, which is computed from the
# generated data. See docs/BUSINESS_CASE.md.

#: Wholesale revenue scale used only to express KPI deltas in dollars.
#: Clearly labelled as an assumption wherever it appears.
SENSITIVITY_REVENUE_SCALES_USD: Final[tuple[float, ...]] = (250e6, 500e6, 900e6)
ASSUMED_GROSS_MARGIN_PCT: Final[float] = 0.38
