"""Stage A — Silver: conform, deduplicate, normalise units, enforce quality.

This is where governance becomes visible. Three classes of expectation run here
and each one is *meant* to find violations, because the per-expectation
pass/fail/drop counts in the pipeline event log are the evidence that the rules
are actually running rather than merely declared:

* **drop** — rows that are invalid and must not reach Gold: negative
  quantities, and POS rows referencing a SKU that does not exist in the item
  master (referential integrity).
* **warn** — rows that are usable but incomplete: null on-hand. Keeping them
  and counting them is correct, because dropping a POS row because its on-hand
  reading is missing would silently understate demand.
Expectations are intentionally limited to drop and warn. A hard
`expect_all_or_fail` is the wrong tool here: SDP evaluates every expectation
against the dataset the function returns, so a fail-rule asserting
post-conversion validity trips on exactly the rows the drop-rules exist to
remove, and halts the whole update. Drop and warn both emit per-expectation
counts to the event log, which is the evidence that matters.

The unit normalisation matters more than it looks: ETAIL_C reports POS and
on-hand in CASES while every other retailer reports eaches. Left unconverted,
that single mismatch makes a third of the portfolio look permanently
stocked-out, which skews the forecast, the risk table and every KPI downstream.
"""

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

CATALOG = spark.conf.get("target_catalog")  # noqa: F821

#: Retailers that report in cases rather than eaches.
CASE_REPORTING = ["ETAIL_C"]

# ---------------------------------------------------------------------------
# dimensions first — the item master is the referential authority
# ---------------------------------------------------------------------------


@dp.materialized_view(
    name=f"{CATALOG}.silver.product_master",
    comment="Deduplicated item master, one row per SKU. Referential authority for "
    "every other Silver table. SYNTHETIC DATA.",
)
@dp.expect_all_or_drop(
    {
        "sku_id_present": "sku_id IS NOT NULL",
        "case_pack_positive": "case_pack > 0",
        "cost_non_negative": "unit_cost_usd >= 0",
    }
)
@dp.expect_all(
    {
        "margin_in_range": "gross_margin_pct BETWEEN 0 AND 1",
        "msrp_above_cost": "msrp_usd > unit_cost_usd",
    }
)
def silver_product_master():
    df = spark.read.table(f"{CATALOG}.bronze.product_master")  # noqa: F821
    w = Window.partitionBy("sku_id").orderBy(F.col("_ingested_at").desc())
    return (
        df.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .drop("_rn", "_rescued_data")
    )


@dp.materialized_view(
    name=f"{CATALOG}.silver.pos_weekly",
    comment="Conformed weekly demand at SKU x retailer x DC x week. Deduplicated on "
    "the grain key, unit-normalised so every quantity is in EACHES, and "
    "referentially checked against the item master. SYNTHETIC DATA.",
)
@dp.expect_all_or_drop(
    {
        # invalid quantities must not reach Gold
        "pos_units_non_negative": "pos_units_raw >= 0",
        # referential integrity: the SKU must exist in the item master
        "sku_resolves_to_master": "case_pack IS NOT NULL",
        "grain_key_complete": (
            "sku_id IS NOT NULL AND retailer_code IS NOT NULL "
            "AND dc_code IS NOT NULL AND week_end_date IS NOT NULL"
        ),
    }
)
@dp.expect_all(
    {
        # usable but incomplete -- counted, deliberately not dropped
        "on_hand_present": "on_hand_units_raw IS NOT NULL",
        "reported_within_8_days": "reporting_lag_days <= 8",
        "uom_recognised": "reported_uom IN ('EACH', 'CASE')",
        "units_normalised_to_each": "pos_units IS NOT NULL",
    }
)
def silver_pos_weekly():
    pos = spark.read.table(f"{CATALOG}.bronze.pos_weekly")  # noqa: F821
    master = spark.read.table(f"{CATALOG}.bronze.product_master").select(  # noqa: F821
        "sku_id", "case_pack"
    ).dropDuplicates(["sku_id"])

    # keep the raw readings under _raw names so the expectations above can test
    # the pre-conversion values while Gold consumes the normalised ones
    pos = (
        pos.withColumnRenamed("pos_units", "pos_units_raw")
        .withColumnRenamed("on_hand_units", "on_hand_units_raw")
        .withColumn(
            "reporting_lag_days",
            F.datediff(F.col("reported_at").cast("date"), F.col("week_end_date")),
        )
    )

    # collapse duplicate grain keys, keeping the most recently reported row
    grain = ["sku_id", "retailer_code", "dc_code", "week_end_date"]
    w = Window.partitionBy(*grain).orderBy(
        F.col("reported_at").desc_nulls_last(), F.col("_ingested_at").desc()
    )
    pos = pos.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")

    joined = pos.join(F.broadcast(master), on="sku_id", how="left")

    is_case = F.col("retailer_code").isin(CASE_REPORTING)
    factor = F.when(is_case, F.col("case_pack")).otherwise(F.lit(1))

    return (
        joined.withColumn("pos_units", (F.col("pos_units_raw") * factor).cast("int"))
        .withColumn("on_hand_units", (F.col("on_hand_units_raw") * factor).cast("int"))
        .withColumn("unit_conversion_applied", is_case)
        .withColumn("conversion_factor", factor)
        .drop("_rescued_data")
    )


@dp.materialized_view(
    name=f"{CATALOG}.silver.orders",
    comment="Conformed PO lines with fill-rate and OTIF flags derived per line. "
    "SYNTHETIC DATA.",
)
@dp.expect_all_or_drop(
    {
        "requested_units_positive": "requested_units > 0",
        "confirmed_not_negative": "confirmed_units >= 0",
        "shipped_not_negative": "shipped_units >= 0",
        "po_line_id_present": "po_line_id IS NOT NULL",
    }
)
@dp.expect_all(
    {
        "confirmed_not_above_requested": "confirmed_units <= requested_units",
        "shipped_not_above_confirmed": "shipped_units <= confirmed_units",
    }
)
def silver_orders():
    df = spark.read.table(f"{CATALOG}.bronze.orders")  # noqa: F821
    w = Window.partitionBy("po_line_id").orderBy(F.col("_ingested_at").desc())
    df = df.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")

    ship_lag = F.datediff(F.col("actual_ship_date"), F.col("requested_ship_date"))
    # a 2-day grace window is the contractual definition of on-time here
    on_time = ship_lag <= F.lit(2)
    in_full = F.col("shipped_units") >= F.col("requested_units")

    return (
        df.withColumn("ship_lag_days", ship_lag)
        .withColumn("is_on_time", on_time)
        .withColumn("is_in_full", in_full)
        .withColumn("is_otif", on_time & in_full)
        .withColumn(
            "invoiced_wholesale_usd",
            F.round(F.col("shipped_units") * F.col("wholesale_price_usd"), 2),
        )
        .withColumn(
            "unfilled_units",
            F.greatest(F.col("requested_units") - F.col("shipped_units"), F.lit(0)),
        )
        .drop("_rescued_data")
    )


@dp.materialized_view(
    name=f"{CATALOG}.silver.inventory_snapshots",
    comment="Conformed daily on-hand and in-transit per SKU x DC. SYNTHETIC DATA.",
)
@dp.expect_all_or_drop(
    {
        "on_hand_non_negative": "on_hand_units >= 0",
        "in_transit_non_negative": "in_transit_units >= 0",
        "snapshot_date_present": "snapshot_date IS NOT NULL",
    }
)
def silver_inventory_snapshots():
    df = spark.read.table(f"{CATALOG}.bronze.inventory_snapshots")  # noqa: F821
    w = Window.partitionBy("sku_id", "dc_code", "snapshot_date").orderBy(
        F.col("_ingested_at").desc()
    )
    return (
        df.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .drop("_rn", "_rescued_data")
    )


@dp.materialized_view(
    name=f"{CATALOG}.silver.inbound_shipments",
    comment="Conformed inbound ASNs with ETA slip and lead-time variance derived per "
    "shipment. Ocean vs air transport mode is retained because the air premium "
    "is a cost of chasing demand spikes late. SYNTHETIC DATA.",
)
@dp.expect_all_or_drop(
    {
        "ordered_units_positive": "ordered_units > 0",
        "shipped_units_non_negative": "shipped_units >= 0",
        "asn_id_present": "asn_id IS NOT NULL",
    }
)
@dp.expect_all(
    {
        "eta_not_before_order": "revised_eta_date >= order_placed_date",
        "transport_mode_known": "transport_mode IN ('OCEAN', 'AIR')",
    }
)
def silver_inbound_shipments():
    df = spark.read.table(f"{CATALOG}.bronze.inbound_shipments")  # noqa: F821
    w = Window.partitionBy("asn_id").orderBy(F.col("_ingested_at").desc())
    df = df.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")
    return (
        df.withColumn(
            "eta_slip_days", F.datediff(F.col("revised_eta_date"), F.col("original_eta_date"))
        )
        .withColumn(
            "lead_time_variance_days",
            F.col("lead_time_days_actual") - F.col("lead_time_days_planned"),
        )
        .withColumn("short_shipped_units", F.greatest(
            F.col("ordered_units") - F.col("shipped_units"), F.lit(0)
        ))
        .drop("_rescued_data")
    )


@dp.materialized_view(
    name=f"{CATALOG}.silver.markdown_clearance",
    comment="Conformed close-out clearance lines for end-of-life residual stock. "
    "SYNTHETIC DATA.",
)
@dp.expect_all_or_drop(
    {
        "cleared_units_positive": "cleared_units > 0",
        "clearance_price_non_negative": "clearance_price_usd >= 0",
    }
)
@dp.expect_all(
    {"clearance_below_wholesale": "clearance_price_usd <= wholesale_price_usd"}
)
def silver_markdown_clearance():
    df = spark.read.table(f"{CATALOG}.bronze.markdown_clearance")  # noqa: F821
    w = Window.partitionBy("clearance_id").orderBy(F.col("_ingested_at").desc())
    return (
        df.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .drop("_rn", "_rescued_data")
    )
