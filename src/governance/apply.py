"""Stage B — apply Unity Catalog governance to the Gold layer.

Runs against the SQL warehouse rather than the ML cluster, because metric views
require a newer runtime than DBR 16.4 LTS ML.

What it applies, in order:

1. **Comments** on every Gold table and every Gold column. A governed data
   product whose columns are unlabelled is not self-describing, and Genie's
   accuracy in Stage E depends directly on these.
2. **Tags** — `domain`, `data_product`, `pii`, plus a `sensitivity` tag on the
   cost and margin columns specifically.
3. **Grants** for three personas: planner (read Gold + metric views), data
   scientist (read Silver and Gold, write to `ml`), app service principal
   (read Gold, read/write the decision table).
4. **Row filter** on retailer, so a planner scoped to one account sees only
   that account's rows.
5. **Column mask** on `unit_cost_usd` and `gross_margin_pct`, so only finance
   sees raw cost and margin.
6. **Metric views** as the governed KPI layer — the single definition of fill
   rate, OTIF, forecast accuracy and weeks of supply that Genie, the app and
   any external BI tool all read.

    python -m src.governance.apply
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.dbsql import sql  # noqa: E402

CATALOG = "toy_demand_allocation"
GOLD = f"{CATALOG}.gold"

# The three personas plus the app identity are SERVICE PRINCIPALS, not groups.
#
# Unity Catalog resolves only ACCOUNT-level principals, and this FEVM workspace
# has no account-level auth available, so workspace-local groups created with
# `databricks groups create` are invisible to GRANT -- every grant against one
# fails with PRINCIPAL_DOES_NOT_EXIST even though SHOW GROUPS lists it. Service
# principals created through the workspace API *are* account-level entities and
# resolve correctly, and they have a further advantage: they can be
# authenticated, so the masked-vs-unmasked comparison can be produced by
# actually signing in as the persona instead of asserting what it would see.
#
# Grants and policies key off the application ID, which is what
# `current_user()` returns for a service principal.
PLANNER = "5d9eb1c3-888e-4acc-8625-4ecedea0fea5"       # toy-planner-mass-a
SCIENTIST = "7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8"     # toy-data-scientist
FINANCE = "dd70e525-c0aa-4ca5-ade2-29671715871a"       # toy-finance
APP_GROUP = "1c73204c-6b49-4db0-a860-300ba392393e"     # toy-app-service

PERSONA_NAMES: dict[str, str] = {
    PLANNER: "toy-planner-mass-a",
    SCIENTIST: "toy-data-scientist",
    FINANCE: "toy-finance",
    APP_GROUP: "toy-app-service",
}

#: Retailer account the scoped planner persona is restricted to.
PLANNER_RETAILER = "MASS_A"

#: Gold objects are materialized views, so policies attach with
#: `ALTER MATERIALIZED VIEW`, not `ALTER TABLE` -- the latter fails with
#: EXPECT_TABLE_NOT_VIEW. This is a DDL-verb difference, not a limitation:
#: row filters and column masks apply to materialized views perfectly well.
ALTER_MV = "ALTER MATERIALIZED VIEW"

# ---------------------------------------------------------------------------
# 1. table and column comments
# ---------------------------------------------------------------------------

TABLE_COMMENTS: dict[str, str] = {
    "dim_product": "Product dimension, one row per SKU: brand, category, licence "
    "family, lifecycle stage, case pack, cost and margin. SYNTHETIC DATA.",
    "dim_retailer_dc": "Retailer x distribution-centre dimension with readable "
    "labels and the contractual fill-rate commitment that drives allocation "
    "priority when supply is constrained. SYNTHETIC DATA.",
    "fct_demand_weekly": "Weekly demand fact at SKU x retailer x DC x week, in "
    "eaches. Training grain for the demand forecast. SYNTHETIC DATA.",
    "fct_orders_fulfillment": "PO-line fulfilment fact with on-time, in-full and "
    "OTIF flags, invoiced value and compliance chargebacks. Basis for the fill "
    "rate and OTIF metric views. SYNTHETIC DATA.",
    "fct_inbound_eta": "Inbound supply fact: units on the water per SKU x DC with "
    "revised ETA, ETA slip, lead-time variance and ocean-vs-air transport mode. "
    "SYNTHETIC DATA.",
    "fct_inventory_position": "Daily on-hand and in-transit position per SKU x DC. "
    "Denominator of weeks-of-supply. SYNTHETIC DATA.",
    "fct_markdown": "Close-out clearance fact: units liquidated and wholesale "
    "margin forgone on end-of-life residual stock. SYNTHETIC DATA.",
    "agg_fill_rate_summary": "Fill rate, OTIF and chargeback rolled up to retailer "
    "x week, sized to serve the app's KPI header from Lakebase. SYNTHETIC DATA.",
}

COLUMN_COMMENTS: dict[str, dict[str, str]] = {
    "dim_product": {
        "sku_id": "Stock-keeping unit identifier. Primary key.",
        "product_name": "Display name of the item.",
        "brand": "Brand: Snugglewick and Glowberry Pets are plush, Titan Squad is "
        "licensed action figures.",
        "category": "PLUSH or ACTION_FIGURE.",
        "license_family": "EVERGREEN (stable, holiday-seasonal), FILM_TIE_IN "
        "(release-window spike) or SOCIAL_TREND (sharp viral spike then collapse). "
        "The strongest single driver of forecast difficulty.",
        "lifecycle_stage": "LAUNCH, GROWTH, MATURE, DECLINE or EOL.",
        "case_pack": "Eaches per case. Used to normalise retailers that report in "
        "cases, and to round replenishment quantities.",
        "unit_cost_usd": "Landed unit cost in USD. SENSITIVE: masked to non-finance.",
        "msrp_usd": "Manufacturer suggested retail price in USD.",
        "wholesale_price_usd": "Price invoiced to the retailer per each, in USD.",
        "gross_margin_pct": "(wholesale - cost) / wholesale. SENSITIVE: masked to "
        "non-finance.",
        "figure_type": "HUMANOID, NON_HUMANOID, CREATURE or VEHICLE. Relevant to "
        "customs classification.",
        "material_composition": "Declared material composition from the supplier "
        "spec sheet.",
        "country_of_origin": "Manufacturing origin: VN, CN or ID.",
        "planned_lifespan_weeks": "Planned commercial life of the licence in weeks.",
        "launch_week_index": "Week index of launch, relative to the start of history.",
    },
    "dim_retailer_dc": {
        "retailer_code": "Anonymised retailer account code: MASS_A, MASS_B, ETAIL_C.",
        "retailer_label": "Readable retailer label for display and for Genie synonyms.",
        "channel": "MASS or ETAIL.",
        "contractual_fill_commitment": "Contractual fill-rate commitment for the "
        "account, expressed as a fraction. Drives allocation priority when supply "
        "is constrained.",
        "dc_code": "Distribution centre code: DC_EAST, DC_CENTRAL, DC_WEST, DC_SOUTH.",
        "dc_label": "Readable distribution-centre label.",
        "dc_region": "Region the distribution centre serves.",
    },
    "fct_demand_weekly": {
        "sku_id": "Stock-keeping unit identifier. Joins to dim_product.",
        "retailer_code": "Retailer account code. Joins to dim_retailer_dc.",
        "dc_code": "Distribution centre shipping the account.",
        "week_end_date": "Retail week ending Saturday. The weekly grain.",
        "pos_units": "Units actually sold through to consumers, in EACHES. Already "
        "normalised from cases where the retailer reports that way.",
        "true_demand_units": "Unconstrained consumer demand in eaches. Exceeds "
        "pos_units whenever supply could not cover it.",
        "on_hand_units": "Retailer on-hand at week end, in eaches.",
        "lost_units": "true_demand_units minus pos_units, floored at zero. Lost sales.",
        "lost_revenue_usd": "Lost units valued at wholesale price.",
        "is_stockout_week": "True when demand exceeded what was served.",
        "promo_flag": "1 when the item was on promotion that week.",
        "reported_uom": "Unit of measure as the retailer reported it: EACH or CASE.",
        "unit_conversion_applied": "True when Silver converted cases to eaches.",
        "reporting_lag_days": "Days between week end and the retailer's report. "
        "Values above 8 are late-arriving data.",
        "brand": "Denormalised from dim_product.",
        "license_family": "Denormalised from dim_product.",
        "lifecycle_stage": "Denormalised from dim_product.",
        "wholesale_price_usd": "Denormalised from dim_product.",
        "_ingested_at": "Bronze ingest timestamp. Provenance.",
        "_source_file": "Landing file the row arrived in. Provenance.",
    },
    "fct_orders_fulfillment": {
        "po_line_id": "Purchase-order line identifier. Primary key.",
        "sku_id": "Stock-keeping unit identifier.",
        "retailer_code": "Ordering retailer account.",
        "dc_code": "Distribution centre fulfilling the line.",
        "requested_week_end_date": "Retail week the retailer requested delivery in.",
        "requested_units": "Units the retailer asked for. Fill-rate denominator.",
        "confirmed_units": "Units accepted against the order.",
        "shipped_units": "Units actually shipped. Fill-rate numerator.",
        "unfilled_units": "requested minus shipped, floored at zero.",
        "requested_ship_date": "Date the retailer required the shipment by.",
        "actual_ship_date": "Date the shipment actually left.",
        "ship_lag_days": "Actual minus requested ship date, in days.",
        "is_on_time": "True when shipped within the contractual 2-day grace window.",
        "is_in_full": "True when shipped units met or exceeded requested units.",
        "is_otif": "True when both on-time and in-full. OTIF numerator.",
        "wholesale_price_usd": "Invoiced price per each.",
        "invoiced_wholesale_usd": "shipped_units x wholesale_price_usd.",
        "chargeback_usd": "Retailer compliance chargeback raised against the line.",
        "brand": "Denormalised from dim_product.",
        "license_family": "Denormalised from dim_product.",
        "lifecycle_stage": "Denormalised from dim_product.",
        "unit_cost_usd": "Landed unit cost. SENSITIVE: masked to non-finance.",
        "_ingested_at": "Bronze ingest timestamp. Provenance.",
        "_source_file": "Landing file the row arrived in. Provenance.",
    },
    "fct_inbound_eta": {
        "asn_id": "Advance ship notice identifier. Primary key.",
        "sku_id": "Stock-keeping unit on the shipment.",
        "dc_code": "Destination distribution centre.",
        "order_placed_date": "Date the factory order was placed.",
        "ordered_units": "Units ordered from the supplier.",
        "shipped_units": "Units the supplier actually shipped.",
        "short_shipped_units": "ordered minus shipped, floored at zero.",
        "original_eta_date": "ETA the supplier originally committed to.",
        "revised_eta_date": "Current ETA. What allocation decisions must use.",
        "eta_slip_days": "Revised minus original ETA, in days.",
        "lead_time_days_planned": "Planned inbound lead time in days.",
        "lead_time_days_actual": "Realised inbound lead time in days.",
        "lead_time_variance_days": "Actual minus planned lead time.",
        "transport_mode": "OCEAN for scheduled replenishment, AIR for a reactive "
        "expedite placed to chase a demand spike.",
        "freight_cost_usd": "Freight cost for the shipment. Air carries a large "
        "premium, which is the cost of reacting to a spike late.",
        "origin_country": "Manufacturing origin.",
        "_ingested_at": "Bronze ingest timestamp. Provenance.",
        "_source_file": "Landing file the row arrived in. Provenance.",
    },
    "fct_inventory_position": {
        "snapshot_date": "Date of the daily position snapshot.",
        "sku_id": "Stock-keeping unit identifier.",
        "dc_code": "Distribution centre holding the stock.",
        "on_hand_units": "Units physically on hand.",
        "in_transit_units": "Units in transit toward the distribution centre.",
        "total_available_units": "on_hand plus in_transit.",
        "is_latest_snapshot": "True on the most recent snapshot date.",
        "_ingested_at": "Bronze ingest timestamp. Provenance.",
    },
    "fct_markdown": {
        "clearance_id": "Close-out clearance line identifier. Primary key.",
        "sku_id": "Stock-keeping unit cleared.",
        "dc_code": "Distribution centre the stock cleared from.",
        "week_end_date": "Week the clearance was recorded.",
        "cleared_units": "Units liquidated at close-out.",
        "wholesale_price_usd": "Normal wholesale price per each.",
        "clearance_price_usd": "Price actually realised on the close-out.",
        "unit_cost_usd": "Landed unit cost. SENSITIVE: masked to non-finance.",
        "markdown_loss_usd": "Wholesale margin forgone: cleared_units x (wholesale "
        "minus clearance price).",
        "brand": "Denormalised from dim_product.",
        "license_family": "Denormalised from dim_product.",
        "lifecycle_stage": "Denormalised from dim_product.",
    },
    "agg_fill_rate_summary": {
        "retailer_code": "Retailer account code.",
        "week_end_date": "Retail week ending Saturday.",
        "requested_units": "Units requested across all PO lines in the week.",
        "confirmed_units": "Units confirmed across all PO lines in the week.",
        "shipped_units": "Units shipped across all PO lines in the week.",
        "unfilled_units": "Units requested but not shipped.",
        "po_lines": "Count of PO lines in the week.",
        "otif_lines": "PO lines that were both on-time and in-full.",
        "on_time_lines": "PO lines shipped within the grace window.",
        "in_full_lines": "PO lines shipped complete.",
        "invoiced_wholesale_usd": "Invoiced wholesale value for the week.",
        "chargeback_usd": "Compliance chargebacks raised in the week.",
        "retailer_label": "Readable retailer label.",
        "contractual_fill_commitment": "Contractual fill-rate commitment.",
        "fill_rate_pct": "shipped_units / requested_units, as a percentage.",
        "otif_pct": "otif_lines / po_lines, as a percentage.",
        "chargeback_pct_of_invoiced": "Chargebacks as a percentage of invoiced value.",
    },
}

# ---------------------------------------------------------------------------
# 2. tags
# ---------------------------------------------------------------------------

#: Columns carrying commercially sensitive cost/margin information.
SENSITIVE_COLUMNS: dict[str, list[str]] = {
    "dim_product": ["unit_cost_usd", "gross_margin_pct"],
    "fct_orders_fulfillment": ["unit_cost_usd"],
    "fct_markdown": ["unit_cost_usd"],
}


def _run(label: str, statement: str, results: list[tuple[str, str, str]]) -> None:
    try:
        sql(statement)
        results.append((label, "OK", ""))
    except Exception as e:  # noqa: BLE001 - we want the full report, not a halt
        results.append((label, "FAILED", str(e)[:300].replace("\n", " ")))


def apply_comments(results: list[tuple[str, str, str]]) -> None:
    for tbl, comment in TABLE_COMMENTS.items():
        safe = comment.replace("'", "''")
        _run(
            f"COMMENT ON TABLE gold.{tbl}",
            f"COMMENT ON TABLE {GOLD}.{tbl} IS '{safe}'",
            results,
        )
    for tbl, cols in COLUMN_COMMENTS.items():
        for col, comment in cols.items():
            safe = comment.replace("'", "''")
            _run(
                f"COMMENT ON COLUMN gold.{tbl}.{col}",
                f"{ALTER_MV} {GOLD}.{tbl} ALTER COLUMN {col} COMMENT '{safe}'",
                results,
            )


# This metastore enforces UC TAG POLICIES, which restrict the permitted values
# for governed tag keys. `data_product` accepts only
# {analytics, machine_learning, data_engineering} and `sensitivity` only
# {pii, internal, public}; anything else is rejected with
# UC_TAG_POLICY_VALUE_NOT_ALLOWED. So the governed keys carry the governed
# vocabulary, and the project-specific detail goes on free-form keys alongside
# them rather than being forced into a controlled key.
#
# The governed `pii` key is a CLASSIFICATION tag, not a boolean: its policy
# admits only {ssn, address}, i.e. it records *which kind* of PII a column
# holds. Tagging a table `pii = 'false'` is therefore not just rejected, it is
# semantically wrong. Since this data product contains no personal data at all,
# the correct thing is to omit the governed key and assert the absence on a
# free-form key instead.
# `data_classification` is policy-controlled too and rejects 'synthetic', so the
# synthetic-data assertion also moves to a free-form key. Discovered allowed
# vocabularies on this metastore, for the record:
#   data_product  -> {analytics, machine_learning, data_engineering}
#   sensitivity   -> {pii, internal, public}
#   pii           -> {ssn, address}
#   data_classification -> constrained; 'synthetic' not permitted
# There is no tag-policy read API on this workspace version, so these were
# established from the UC_TAG_POLICY_VALUE_NOT_ALLOWED errors themselves.
TABLE_TAGS = (
    "'domain' = 'supply_chain', "
    "'data_product' = 'analytics', "
    "'contains_pii' = 'false', "
    "'data_product_name' = 'demand_allocation', "
    "'synthetic_data' = 'true'"
)

COLUMN_SENSITIVITY_TAGS = (
    "'sensitivity' = 'internal', "
    "'cost_margin_confidential' = 'true', "
    "'masked_by' = 'gold.cm_cost_margin'"
)


def apply_tags(results: list[tuple[str, str, str]]) -> None:
    _run(
        "SET TAGS on catalog",
        f"ALTER CATALOG {CATALOG} SET TAGS ({TABLE_TAGS})",
        results,
    )
    for tbl in TABLE_COMMENTS:
        _run(
            f"SET TAGS on gold.{tbl}",
            f"{ALTER_MV} {GOLD}.{tbl} SET TAGS ({TABLE_TAGS})",
            results,
        )
    for tbl, cols in SENSITIVE_COLUMNS.items():
        for col in cols:
            _run(
                f"SET TAGS sensitivity on gold.{tbl}.{col}",
                f"{ALTER_MV} {GOLD}.{tbl} ALTER COLUMN {col} "
                f"SET TAGS ({COLUMN_SENSITIVITY_TAGS})",
                results,
            )


def apply_grants(results: list[tuple[str, str, str]]) -> None:
    # the app identity group may not exist yet; create-if-missing is handled by
    # the caller, so a failure here is reported rather than fatal
    for sp in (PLANNER, SCIENTIST, FINANCE, APP_GROUP):
        _run(
            f"GRANT USE CATALOG to {PERSONA_NAMES[sp]}",
            f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `{sp}`",
            results,
        )

    # planner: read gold only
    _run(
        "GRANT planner USE SCHEMA gold",
        f"GRANT USE SCHEMA ON SCHEMA {GOLD} TO `{PLANNER}`",
        results,
    )
    _run(
        "GRANT planner SELECT on gold",
        f"GRANT SELECT ON SCHEMA {GOLD} TO `{PLANNER}`",
        results,
    )

    # data scientist: read silver + gold, write ml
    for schema in ("silver", "gold"):
        _run(
            f"GRANT scientist USE SCHEMA {schema}",
            f"GRANT USE SCHEMA ON SCHEMA {CATALOG}.{schema} TO `{SCIENTIST}`",
            results,
        )
        _run(
            f"GRANT scientist SELECT on {schema}",
            f"GRANT SELECT ON SCHEMA {CATALOG}.{schema} TO `{SCIENTIST}`",
            results,
        )
    _run(
        "GRANT scientist ALL on ml",
        f"GRANT ALL PRIVILEGES ON SCHEMA {CATALOG}.ml TO `{SCIENTIST}`",
        results,
    )

    # finance: read gold, and see unmasked cost/margin
    _run(
        "GRANT finance USE SCHEMA gold",
        f"GRANT USE SCHEMA ON SCHEMA {GOLD} TO `{FINANCE}`",
        results,
    )
    _run(
        "GRANT finance SELECT on gold",
        f"GRANT SELECT ON SCHEMA {GOLD} TO `{FINANCE}`",
        results,
    )

    # app service principal: read gold, write the decision round-trip table
    _run(
        "GRANT app USE SCHEMA gold",
        f"GRANT USE SCHEMA ON SCHEMA {GOLD} TO `{APP_GROUP}`",
        results,
    )
    _run(
        "GRANT app SELECT on gold",
        f"GRANT SELECT ON SCHEMA {GOLD} TO `{APP_GROUP}`",
        results,
    )
    _run(
        "GRANT app CREATE TABLE on gold",
        f"GRANT CREATE TABLE ON SCHEMA {GOLD} TO `{APP_GROUP}`",
        results,
    )
    _run(
        "GRANT app MODIFY on gold",
        f"GRANT MODIFY ON SCHEMA {GOLD} TO `{APP_GROUP}`",
        results,
    )


ROW_FILTER_TARGETS = ("fct_demand_weekly", "fct_orders_fulfillment", "agg_fill_rate_summary")


def apply_policies(results: list[tuple[str, str, str]]) -> None:
    """Row filter on retailer and column mask on cost/margin.

    Both predicates test `current_user()` against a service-principal
    application ID. `is_account_group_member()` is not usable here: the personas
    are service principals rather than account groups, because this workspace
    cannot create account groups (see the PLANNER/FINANCE comment above).

    A UDF used as a policy needs its `RETURNS` type and `COMMENT` *before* the
    `RETURN` clause; putting COMMENT last is a parse error.
    """
    _run(
        "CREATE row filter function",
        f"""
CREATE OR REPLACE FUNCTION {GOLD}.rf_retailer_scope(retailer_code STRING)
RETURNS BOOLEAN
COMMENT 'Row filter on retailer. The planner persona scoped to the
{PLANNER_RETAILER} account sees only {PLANNER_RETAILER} rows; every other
identity is unrestricted. Applied to gold.fct_demand_weekly,
gold.fct_orders_fulfillment and gold.agg_fill_rate_summary.'
RETURN
  CASE
    WHEN current_user() = '{PLANNER}' THEN retailer_code = '{PLANNER_RETAILER}'
    ELSE TRUE
  END
""".strip(),
        results,
    )

    _run(
        "CREATE column mask function",
        f"""
CREATE OR REPLACE FUNCTION {GOLD}.cm_cost_margin(v DOUBLE)
RETURNS DOUBLE
COMMENT 'Column mask on landed cost and gross margin. Only the finance persona
reads raw values; every other identity reads NULL. Applied to
dim_product.unit_cost_usd, dim_product.gross_margin_pct,
fct_orders_fulfillment.unit_cost_usd and fct_markdown.unit_cost_usd.'
RETURN
  CASE
    WHEN current_user() = '{FINANCE}' THEN v
    ELSE NULL
  END
""".strip(),
        results,
    )

    for tbl in ROW_FILTER_TARGETS:
        _run(
            f"SET ROW FILTER on gold.{tbl}",
            f"{ALTER_MV} {GOLD}.{tbl} "
            f"SET ROW FILTER {GOLD}.rf_retailer_scope ON (retailer_code)",
            results,
        )

    for tbl, cols in SENSITIVE_COLUMNS.items():
        for col in cols:
            _run(
                f"SET MASK on gold.{tbl}.{col}",
                f"{ALTER_MV} {GOLD}.{tbl} ALTER COLUMN {col} "
                f"SET MASK {GOLD}.cm_cost_margin",
                results,
            )


# ---------------------------------------------------------------------------
# 6. metric views — the governed KPI layer
# ---------------------------------------------------------------------------

MV_FILL_RATE = f"""
CREATE OR REPLACE VIEW {GOLD}.mv_fill_rate
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: {CATALOG}.gold.fct_orders_fulfillment
comment: >-
  Governed definition of retailer fill rate and the order-acceptance rate.
  Fill rate is shipped units over REQUESTED units, which is the service level the
  retailer actually experiences and the basis on which compliance chargebacks are
  raised. The confirmed-basis measure is published alongside it because supply
  chain teams often quote that number instead, and the two differ sharply whenever
  supply is constrained. SYNTHETIC DATA.
dimensions:
  - name: Retailer
    expr: retailer_code
    comment: Retailer account code (MASS_A, MASS_B, ETAIL_C).
  - name: DC
    expr: dc_code
    comment: Distribution centre fulfilling the line.
  - name: Week
    expr: requested_week_end_date
    comment: Retail week ending Saturday.
  - name: Quarter
    expr: DATE_TRUNC('QUARTER', requested_week_end_date)
    comment: Calendar quarter of the requested week.
  - name: Brand
    expr: brand
  - name: License Family
    expr: license_family
    comment: EVERGREEN, FILM_TIE_IN or SOCIAL_TREND.
  - name: Lifecycle Stage
    expr: lifecycle_stage
measures:
  - name: Requested Units
    expr: SUM(requested_units)
    comment: Units the retailer asked for. Fill-rate denominator.
  - name: Confirmed Units
    expr: SUM(confirmed_units)
  - name: Shipped Units
    expr: SUM(shipped_units)
    comment: Units actually shipped. Fill-rate numerator.
  - name: Unfilled Units
    expr: SUM(unfilled_units)
  - name: Fill Rate Pct
    expr: 100.0 * SUM(shipped_units) / NULLIF(SUM(requested_units), 0)
    comment: >-
      Shipped units over requested units, as a percentage. The headline service
      metric.
  - name: Fill Rate Confirmed Basis Pct
    expr: 100.0 * SUM(shipped_units) / NULLIF(SUM(confirmed_units), 0)
    comment: Shipped over CONFIRMED units. Flatters performance when supply is short.
  - name: Order Acceptance Pct
    expr: 100.0 * SUM(confirmed_units) / NULLIF(SUM(requested_units), 0)
    comment: Confirmed over requested units. Falls when supply cannot cover demand.
  - name: PO Lines
    expr: COUNT(1)
  - name: Invoiced Wholesale USD
    expr: SUM(invoiced_wholesale_usd)
  - name: Chargeback USD
    expr: SUM(chargeback_usd)
    comment: Retailer compliance chargebacks raised against non-compliant lines.
  - name: Chargeback Pct Of Invoiced
    expr: 100.0 * SUM(chargeback_usd) / NULLIF(SUM(invoiced_wholesale_usd), 0)
$$
""".strip()

MV_OTIF = f"""
CREATE OR REPLACE VIEW {GOLD}.mv_otif
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: {CATALOG}.gold.fct_orders_fulfillment
comment: >-
  Governed definition of OTIF (on-time, in-full). A PO line counts as OTIF only
  when it shipped within the contractual two-day grace window AND shipped complete
  against the requested quantity. On-time and in-full are published separately so a
  miss can be attributed to the right cause. SYNTHETIC DATA.
dimensions:
  - name: Retailer
    expr: retailer_code
  - name: DC
    expr: dc_code
  - name: Week
    expr: requested_week_end_date
  - name: Quarter
    expr: DATE_TRUNC('QUARTER', requested_week_end_date)
  - name: Brand
    expr: brand
  - name: License Family
    expr: license_family
  - name: Lifecycle Stage
    expr: lifecycle_stage
measures:
  - name: PO Lines
    expr: COUNT(1)
  - name: OTIF Lines
    expr: SUM(CASE WHEN is_otif THEN 1 ELSE 0 END)
  - name: OTIF Pct
    expr: 100.0 * SUM(CASE WHEN is_otif THEN 1 ELSE 0 END) / NULLIF(COUNT(1), 0)
    comment: Share of PO lines that were both on-time and in-full.
  - name: On Time Pct
    expr: 100.0 * SUM(CASE WHEN is_on_time THEN 1 ELSE 0 END) / NULLIF(COUNT(1), 0)
  - name: In Full Pct
    expr: 100.0 * SUM(CASE WHEN is_in_full THEN 1 ELSE 0 END) / NULLIF(COUNT(1), 0)
  - name: Avg Ship Lag Days
    expr: AVG(ship_lag_days)
  - name: Chargeback USD
    expr: SUM(chargeback_usd)
$$
""".strip()

#: Mean weekly POS units over the last 13 weeks, per SKU x DC. Weeks of supply needs demand as
#: well as the position snapshot, and this is the demand side. Defined here, once, so the metric
#: view is the only place the denominator lives.
AGG_TRAILING_DEMAND = f"""
CREATE OR REPLACE TABLE {GOLD}.agg_trailing_demand
COMMENT 'Mean weekly POS units over the last 13 weeks at SKU x DC: the demand denominator of
weeks of supply. Read by gold.mv_weeks_of_supply. SYNTHETIC DATA.'
AS SELECT sku_id, dc_code, SUM(pos_units) / 13.0 AS mean_weekly_pos_units_13w
   FROM {GOLD}.fct_demand_weekly
   WHERE week_end_date > date_sub((SELECT MAX(week_end_date) FROM {GOLD}.fct_demand_weekly), 91)
   GROUP BY sku_id, dc_code
""".strip()

MV_WEEKS_OF_SUPPLY = f"""
CREATE OR REPLACE VIEW {GOLD}.mv_weeks_of_supply
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: {CATALOG}.gold.fct_inventory_position
comment: >-
  Governed definition of inventory cover. Weeks of supply is on-hand units divided
  by mean weekly demand, so it needs the demand fact as well as the position
  snapshot; the join is declared here rather than left to each consumer to
  reinvent. Restricted to the latest snapshot. SYNTHETIC DATA.
filter: is_latest_snapshot = true
joins:
  - name: prod
    source: {CATALOG}.gold.dim_product
    on: source.sku_id = prod.sku_id
  - name: dem
    source: {CATALOG}.gold.agg_trailing_demand
    on: source.sku_id = dem.sku_id AND source.dc_code = dem.dc_code
dimensions:
  - name: DC
    expr: source.dc_code
  - name: SKU
    expr: source.sku_id
  - name: Brand
    expr: prod.brand
  - name: License Family
    expr: prod.license_family
  - name: Lifecycle Stage
    expr: prod.lifecycle_stage
  - name: Category
    expr: prod.category
measures:
  - name: Weeks Of Supply
    expr: SUM(source.on_hand_units) / NULLIF(SUM(dem.mean_weekly_pos_units_13w), 0)
    comment: >-
      Latest on-hand units divided by mean weekly POS units over the last 13 weeks.
      A ratio of sums, so it aggregates correctly to any grouping.
  - name: Weekly Demand Units
    expr: SUM(dem.mean_weekly_pos_units_13w)
    comment: Mean weekly POS units over the last 13 weeks (the denominator).
  - name: On Hand Units
    expr: SUM(source.on_hand_units)
  - name: In Transit Units
    expr: SUM(source.in_transit_units)
  - name: Total Available Units
    expr: SUM(source.total_available_units)
  - name: SKU DC Positions
    expr: COUNT(1)
  - name: On Hand At Cost USD
    expr: SUM(source.on_hand_units * prod.unit_cost_usd)
    comment: Inventory valued at landed cost.
$$
""".strip()

MV_FORECAST_ACCURACY = f"""
CREATE OR REPLACE VIEW {GOLD}.mv_forecast_accuracy
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: {CATALOG}.gold.fct_demand_forecast_eval
comment: >-
  Governed definition of forecast accuracy at SKU x retailer x week. WAPE
  (weighted absolute percentage error) is the headline measure because it is
  volume-weighted and therefore stable on the intermittent, spiky series this
  portfolio is full of -- a per-row MAPE average is dominated by near-zero
  denominators on slow sellers and is effectively meaningless here. Bias is
  published alongside it so over- and under-forecasting can be told apart.
  SYNTHETIC DATA.
dimensions:
  - name: Retailer
    expr: retailer_code
  - name: Week
    expr: week_end_date
  - name: SKU
    expr: sku_id
  - name: Brand
    expr: brand
  - name: License Family
    expr: license_family
  - name: Lifecycle Stage
    expr: lifecycle_stage
  - name: Model Version
    expr: model_version
  - name: Model Alias
    expr: model_alias
measures:
  - name: Actual Units
    expr: SUM(actual_units)
  - name: Forecast Units P50
    expr: SUM(forecast_p50)
  - name: Forecast Units P90
    expr: SUM(forecast_p90)
  - name: Abs Error Units
    expr: SUM(ABS(forecast_p50 - actual_units))
  - name: WAPE Pct
    expr: >-
      100.0 * SUM(ABS(forecast_p50 - actual_units)) / NULLIF(SUM(actual_units), 0)
    comment: Volume-weighted absolute percentage error. The headline accuracy measure.
  - name: Bias Pct
    expr: 100.0 * SUM(forecast_p50 - actual_units) / NULLIF(SUM(actual_units), 0)
    comment: Positive means over-forecasting, negative means under-forecasting.
  - name: Forecast Rows
    expr: COUNT(1)
  - name: P90 Coverage Pct
    expr: >-
      100.0 * SUM(CASE WHEN actual_units <= forecast_p90 THEN 1 ELSE 0 END)
      / NULLIF(COUNT(1), 0)
    comment: >-
      Share of actuals at or below the p90 forecast. Should sit near 90 if the
      upper quantile is calibrated.
$$
""".strip()


def apply_metric_views(results: list[tuple[str, str, str]], include_forecast: bool) -> None:
    _run("CREATE mv_fill_rate", MV_FILL_RATE, results)
    _run("CREATE mv_otif", MV_OTIF, results)
    _run("CREATE agg_trailing_demand", AGG_TRAILING_DEMAND, results)
    _run("CREATE mv_weeks_of_supply", MV_WEEKS_OF_SUPPLY, results)
    if include_forecast:
        _run("CREATE mv_forecast_accuracy", MV_FORECAST_ACCURACY, results)
    else:
        results.append(
            (
                "CREATE mv_forecast_accuracy",
                "DEFERRED",
                "source gold.fct_demand_forecast_eval does not exist yet; created in "
                "Stage C1 and this view is applied by notebook 03",
            )
        )


def main() -> int:
    include_forecast = "--with-forecast" in sys.argv
    results: list[tuple[str, str, str]] = []

    if "--weeks-of-supply-only" in sys.argv:       # re-apply just the demand table + metric view
        _run("CREATE agg_trailing_demand", AGG_TRAILING_DEMAND, results)
        _run("CREATE mv_weeks_of_supply", MV_WEEKS_OF_SUPPLY, results)
        for label, status, err in results:
            print(f"  {status:8} {label} {err[:200] if status != 'OK' else ''}")
        return 1 if any(r[1] == "FAILED" for r in results) else 0

    print("applying comments ...")
    apply_comments(results)
    print("applying tags ...")
    apply_tags(results)
    print("applying grants ...")
    apply_grants(results)
    print("applying row filter and column mask ...")
    apply_policies(results)
    print("applying metric views ...")
    apply_metric_views(results, include_forecast)

    ok = sum(1 for _, s, _ in results if s == "OK")
    failed = [r for r in results if r[1] == "FAILED"]
    deferred = [r for r in results if r[1] == "DEFERRED"]

    print()
    print(f"{ok} statements OK, {len(failed)} failed, {len(deferred)} deferred")
    for label, _, err in failed:
        print(f"  FAILED  {label}: {err}")
    for label, _, err in deferred:
        print(f"  DEFER   {label}: {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
