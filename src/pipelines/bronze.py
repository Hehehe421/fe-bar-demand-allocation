"""Stage A — Bronze: Auto Loader streaming tables, one per source system.

Raw and append-only. The only transformations are the two provenance columns
every row carries, `_ingested_at` and `_source_file`, so a row in Gold can
always be traced back to the file it arrived in.

Deliberately NO cleaning here: the negative quantities, duplicate grain keys,
null on-hand values, orphan SKU references and case-vs-each unit mismatch all
land in Bronze exactly as the source produced them. Silver is where the
data-quality expectations act on them, and the event-log drop/fail counts are
the evidence that governance is live.

Schema is inferred with hints rather than declared outright: Spark's CSV reader
maps an explicitly-supplied schema by POSITION when `header=true`, so a column
reordering upstream would silently shift values into the wrong fields. Inference
with `schemaHints` binds by name and still pins the types that matter.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG = spark.conf.get("target_catalog")  # noqa: F821
LANDING = spark.conf.get("landing_path")  # noqa: F821
META = spark.conf.get("schema_location_base")  # noqa: F821

#: Type hints per source. Everything not listed infers as STRING, which is the
#: safe default for identifiers and codes.
HINTS: dict[str, str] = {
    "product_master": (
        "case_pack INT, unit_cost_usd DOUBLE, msrp_usd DOUBLE, "
        "wholesale_price_usd DOUBLE, launch_week_index INT, "
        "planned_lifespan_weeks INT, gross_margin_pct DOUBLE"
    ),
    "pos_weekly": (
        "week_end_date DATE, pos_units INT, true_demand_units INT, "
        "promo_flag INT, on_hand_units DOUBLE, reported_at TIMESTAMP"
    ),
    "orders": (
        "requested_week_end_date DATE, requested_units INT, confirmed_units INT, "
        "shipped_units INT, requested_ship_date DATE, actual_ship_date DATE, "
        "wholesale_price_usd DOUBLE, chargeback_usd DOUBLE"
    ),
    "inventory_snapshots": (
        "snapshot_date DATE, on_hand_units INT, in_transit_units INT"
    ),
    "inbound_shipments": (
        "order_placed_date DATE, ordered_units INT, shipped_units INT, "
        "original_eta_date DATE, revised_eta_date DATE, "
        "lead_time_days_planned INT, lead_time_days_actual INT, "
        "freight_cost_usd DOUBLE"
    ),
    "markdown_clearance": (
        "week_end_date DATE, cleared_units INT, wholesale_price_usd DOUBLE, "
        "clearance_price_usd DOUBLE, unit_cost_usd DOUBLE, markdown_loss_usd DOUBLE"
    ),
}

#: Liquid clustering keys per source, chosen for the access patterns the later
#: stages actually use.
CLUSTER_BY: dict[str, list[str]] = {
    "product_master": ["sku_id"],
    "pos_weekly": ["week_end_date", "retailer_code"],
    "orders": ["requested_week_end_date", "retailer_code"],
    "inventory_snapshots": ["snapshot_date", "dc_code"],
    "inbound_shipments": ["revised_eta_date", "dc_code"],
    "markdown_clearance": ["week_end_date", "dc_code"],
}


def _ingest(source: str):
    """Auto Loader stream for one landing subdirectory."""
    return (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaLocation", f"{META}/bronze_{source}")
        .option("cloudFiles.schemaHints", HINTS[source])
        .option("cloudFiles.inferColumnTypes", "true")
        .option("header", "true")
        # keep malformed values instead of nulling them, so nothing is lost
        # silently before the Silver expectations get a chance to judge it
        .option("rescuedDataColumn", "_rescued_data")
        .load(f"{LANDING}/{source}")
        .withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_source_file", F.col("_metadata.file_path"))
    )


def _register(source: str, comment: str) -> None:
    @dp.table(
        name=f"{CATALOG}.bronze.{source}",
        cluster_by=CLUSTER_BY[source],
        comment=comment,
    )
    def _fn(source: str = source):
        return _ingest(source)


_register(
    "product_master",
    "Raw item master as supplied: brand, licence family, lifecycle stage, case pack, "
    "unit cost, MSRP, wholesale price. One row per SKU. SYNTHETIC DATA.",
)
_register(
    "pos_weekly",
    "Raw retailer point-of-sale and on-hand by SKU x retailer x DC x week. Arrives "
    "late, contains duplicate grain keys and null on-hand, and ETAIL_C reports in "
    "CASES rather than eaches. SYNTHETIC DATA.",
)
_register(
    "orders",
    "Raw purchase-order lines: requested, confirmed and shipped quantity and date, "
    "plus any compliance chargeback. Basis for fill rate and OTIF. SYNTHETIC DATA.",
)
_register(
    "inventory_snapshots",
    "Raw daily on-hand and in-transit units per SKU x distribution centre. "
    "SYNTHETIC DATA.",
)
_register(
    "inbound_shipments",
    "Raw inbound advance ship notices from offshore manufacturing: original vs "
    "revised ETA, lead-time actuals, ocean vs air transport mode and freight cost. "
    "SYNTHETIC DATA.",
)
_register(
    "markdown_clearance",
    "Raw close-out clearance lines for end-of-life residual stock: units cleared, "
    "clearance price and forgone wholesale margin. SYNTHETIC DATA.",
)
