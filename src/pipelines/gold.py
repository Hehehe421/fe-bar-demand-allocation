"""Stage A — Gold: the business layer every later stage reads from.

These tables are the contract between Stage A and Stages B–F. The forecast
features, the stockout-risk spine, the agent's tools, the metric views, the
Genie space, the Lakebase synced tables and the app all read from here — none
of them re-read Bronze or Silver, and none of them build their own copy of a
fact. That is what keeps the six stages integrated rather than siloed.

Everything is in EACHES. Every quantity has already been unit-normalised and
referentially checked in Silver.
"""

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

CATALOG = spark.conf.get("target_catalog")

#: Readable labels and contractual fill-rate commitments per retailer. The
#: commitment drives allocation priority when supply is constrained, so it
#: belongs in the governed dimension rather than in application code.
RETAILERS = [
    ("MASS_A", "Mass Retail Account A", "MASS", 0.97),
    ("MASS_B", "Mass Retail Account B", "MASS", 0.95),
    ("ETAIL_C", "E-tail Account C", "ETAIL", 0.93),
]

DCS = [
    ("DC_EAST", "East Coast DC", "US-EAST"),
    ("DC_CENTRAL", "Central DC", "US-CENTRAL"),
    ("DC_WEST", "West Coast DC", "US-WEST"),
    ("DC_SOUTH", "South DC", "US-SOUTH"),
]


# ---------------------------------------------------------------------------
# dimensions
# ---------------------------------------------------------------------------


@dp.materialized_view(
    name=f"{CATALOG}.gold.dim_product",
    comment="Product dimension, one row per SKU: brand, category, licence family, "
    "lifecycle stage, case pack, cost and margin. SYNTHETIC DATA.",
)
def dim_product():
    return spark.read.table(f"{CATALOG}.silver.product_master").select(
        "sku_id",
        "product_name",
        "brand",
        "category",
        "license_family",
        "lifecycle_stage",
        "case_pack",
        "unit_cost_usd",
        "msrp_usd",
        "wholesale_price_usd",
        "gross_margin_pct",
        "figure_type",
        "material_composition",
        "country_of_origin",
        "planned_lifespan_weeks",
        "launch_week_index",
    )


@dp.materialized_view(
    name=f"{CATALOG}.gold.dim_retailer_dc",
    comment="Retailer x distribution-centre dimension with readable labels and the "
    "contractual fill-rate commitment that drives allocation priority. "
    "SYNTHETIC DATA.",
)
def dim_retailer_dc():
    retailers = spark.createDataFrame(
        RETAILERS, "retailer_code STRING, retailer_label STRING, channel STRING, "
        "contractual_fill_commitment DOUBLE"
    )
    dcs = spark.createDataFrame(
        DCS, "dc_code STRING, dc_label STRING, dc_region STRING"
    )
    # only emit the combinations that actually ship
    observed = (
        spark.read.table(f"{CATALOG}.silver.pos_weekly")
        .select("retailer_code", "dc_code")
        .distinct()
    )
    return (
        observed.join(retailers, "retailer_code", "inner")
        .join(dcs, "dc_code", "inner")
        .select(
            "retailer_code",
            "retailer_label",
            "channel",
            "contractual_fill_commitment",
            "dc_code",
            "dc_label",
            "dc_region",
        )
    )


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------


@dp.materialized_view(
    name=f"{CATALOG}.gold.fct_demand_weekly",
    cluster_by=["week_end_date", "retailer_code"],
    comment="Weekly demand fact at SKU x retailer x DC x week, in EACHES. The "
    "forecast's training grain and the spine of the demand story. "
    "`true_demand_units` is the unconstrained consumer demand and "
    "`pos_units` is what was actually served, so their difference is lost "
    "sales. SYNTHETIC DATA.",
)
def fct_demand_weekly():
    pos = spark.read.table(f"{CATALOG}.silver.pos_weekly")
    prod = spark.read.table(f"{CATALOG}.gold.dim_product").select(
        "sku_id", "brand", "license_family", "lifecycle_stage", "wholesale_price_usd"
    )
    return (
        pos.join(F.broadcast(prod), "sku_id", "left")
        .withColumn(
            "lost_units",
            F.greatest(F.col("true_demand_units") - F.col("pos_units"), F.lit(0)),
        )
        .withColumn(
            "lost_revenue_usd",
            F.round(
                F.greatest(F.col("true_demand_units") - F.col("pos_units"), F.lit(0))
                * F.col("wholesale_price_usd"),
                2,
            ),
        )
        .withColumn("is_stockout_week", F.col("true_demand_units") > F.col("pos_units"))
        .select(
            "sku_id",
            "retailer_code",
            "dc_code",
            "week_end_date",
            "pos_units",
            "true_demand_units",
            "on_hand_units",
            "lost_units",
            "lost_revenue_usd",
            "is_stockout_week",
            "promo_flag",
            "reported_uom",
            "unit_conversion_applied",
            "reporting_lag_days",
            "brand",
            "license_family",
            "lifecycle_stage",
            "wholesale_price_usd",
            "_ingested_at",
            "_source_file",
        )
    )


@dp.materialized_view(
    name=f"{CATALOG}.gold.fct_orders_fulfillment",
    cluster_by=["requested_week_end_date", "retailer_code"],
    comment="PO-line fulfilment fact: requested, confirmed and shipped units with "
    "on-time, in-full and OTIF flags, invoiced value and any compliance "
    "chargeback. Basis for the fill-rate and OTIF metric views. SYNTHETIC DATA.",
)
def fct_orders_fulfillment():
    orders = spark.read.table(f"{CATALOG}.silver.orders")
    prod = spark.read.table(f"{CATALOG}.gold.dim_product").select(
        "sku_id", "brand", "license_family", "lifecycle_stage", "unit_cost_usd"
    )
    return (
        orders.join(F.broadcast(prod), "sku_id", "left")
        .select(
            "po_line_id",
            "sku_id",
            "retailer_code",
            "dc_code",
            "requested_week_end_date",
            "requested_units",
            "confirmed_units",
            "shipped_units",
            "unfilled_units",
            "requested_ship_date",
            "actual_ship_date",
            "ship_lag_days",
            "is_on_time",
            "is_in_full",
            "is_otif",
            "wholesale_price_usd",
            "invoiced_wholesale_usd",
            "chargeback_usd",
            "brand",
            "license_family",
            "lifecycle_stage",
            "unit_cost_usd",
            "_ingested_at",
            "_source_file",
        )
    )


@dp.materialized_view(
    name=f"{CATALOG}.gold.fct_inbound_eta",
    cluster_by=["revised_eta_date", "dc_code"],
    comment="Inbound supply fact: units on the water per SKU x DC with revised ETA, "
    "ETA slip, lead-time variance and transport mode. Joined against on-hand "
    "to decide whether a projected stockout can actually be covered. "
    "SYNTHETIC DATA.",
)
def fct_inbound_eta():
    asn = spark.read.table(f"{CATALOG}.silver.inbound_shipments")
    return asn.select(
        "asn_id",
        "sku_id",
        "dc_code",
        "order_placed_date",
        "ordered_units",
        "shipped_units",
        "short_shipped_units",
        "original_eta_date",
        "revised_eta_date",
        "eta_slip_days",
        "lead_time_days_planned",
        "lead_time_days_actual",
        "lead_time_variance_days",
        "transport_mode",
        "freight_cost_usd",
        "origin_country",
        "_ingested_at",
        "_source_file",
    )


@dp.materialized_view(
    name=f"{CATALOG}.gold.fct_inventory_position",
    cluster_by=["snapshot_date", "dc_code"],
    comment="Latest-available on-hand and in-transit position per SKU x DC, plus the "
    "full daily history. The denominator of weeks-of-supply. SYNTHETIC DATA.",
)
def fct_inventory_position():
    inv = spark.read.table(f"{CATALOG}.silver.inventory_snapshots")
    # Flag the newest snapshot with a window rather than collecting the max
    # back to the driver: SDP rejects DataFrame.collect inside a decorated
    # function because it breaks the declarative execution model.
    latest = F.max("snapshot_date").over(Window.partitionBy())
    return (
        inv.withColumn("is_latest_snapshot", F.col("snapshot_date") == latest)
        .withColumn(
            "total_available_units", F.col("on_hand_units") + F.col("in_transit_units")
        )
        .select(
            "snapshot_date",
            "sku_id",
            "dc_code",
            "on_hand_units",
            "in_transit_units",
            "total_available_units",
            "is_latest_snapshot",
            "_ingested_at",
        )
    )


@dp.materialized_view(
    name=f"{CATALOG}.gold.fct_markdown",
    cluster_by=["week_end_date"],
    comment="Close-out clearance fact: units liquidated and wholesale margin forgone "
    "on end-of-life residual stock. Makes the markdown KPI traceable to "
    "committed data rather than inferred from a snapshot. SYNTHETIC DATA.",
)
def fct_markdown():
    md = spark.read.table(f"{CATALOG}.silver.markdown_clearance")
    prod = spark.read.table(f"{CATALOG}.gold.dim_product").select(
        "sku_id", "brand", "license_family", "lifecycle_stage"
    )
    return md.join(F.broadcast(prod), "sku_id", "left").select(
        "clearance_id",
        "sku_id",
        "dc_code",
        "week_end_date",
        "cleared_units",
        "wholesale_price_usd",
        "clearance_price_usd",
        "unit_cost_usd",
        "markdown_loss_usd",
        "brand",
        "license_family",
        "lifecycle_stage",
    )


# ---------------------------------------------------------------------------
# aggregate for the app's low-latency read path (synced to Lakebase in Stage D)
# ---------------------------------------------------------------------------


@dp.materialized_view(
    name=f"{CATALOG}.gold.agg_fill_rate_summary",
    comment="Fill rate, OTIF and chargeback rolled up to retailer x week. Small "
    "enough to serve the app's KPI header from Lakebase without a warehouse "
    "query per page load. SYNTHETIC DATA.",
)
def agg_fill_rate_summary():
    f = spark.read.table(f"{CATALOG}.gold.fct_orders_fulfillment")
    dim = spark.read.table(f"{CATALOG}.gold.dim_retailer_dc").select(
        "retailer_code", "retailer_label", "contractual_fill_commitment"
    ).distinct()
    agg = f.groupBy("retailer_code", "requested_week_end_date").agg(
        F.sum("requested_units").alias("requested_units"),
        F.sum("confirmed_units").alias("confirmed_units"),
        F.sum("shipped_units").alias("shipped_units"),
        F.sum("unfilled_units").alias("unfilled_units"),
        F.count("*").alias("po_lines"),
        F.sum(F.col("is_otif").cast("int")).alias("otif_lines"),
        F.sum(F.col("is_on_time").cast("int")).alias("on_time_lines"),
        F.sum(F.col("is_in_full").cast("int")).alias("in_full_lines"),
        F.round(F.sum("invoiced_wholesale_usd"), 2).alias("invoiced_wholesale_usd"),
        F.round(F.sum("chargeback_usd"), 2).alias("chargeback_usd"),
    )
    return (
        agg.join(F.broadcast(dim), "retailer_code", "left")
        .withColumn(
            "fill_rate_pct",
            F.round(F.col("shipped_units") / F.col("requested_units") * 100, 3),
        )
        .withColumn("otif_pct", F.round(F.col("otif_lines") / F.col("po_lines") * 100, 3))
        .withColumn(
            "chargeback_pct_of_invoiced",
            F.round(
                F.col("chargeback_usd")
                / F.when(F.col("invoiced_wholesale_usd") > 0, F.col("invoiced_wholesale_usd")),
                5,
            )
            * 100,
        )
        .withColumnRenamed("requested_week_end_date", "week_end_date")
    )
