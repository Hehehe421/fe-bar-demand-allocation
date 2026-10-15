# ruff: noqa: E501  (instruction and example text is prose and SQL, kept on one line per sentence)
"""Revision 3 of the Genie space: the configuration the build prompt (§E) specifies.

Revisions 1 and 2 (``space_definition.py``) were deliberately thin: curation first, instructions
only to patch an observed failure. That was a defensible experiment and it produced the q11
finding, but it left the space short of what the rubric asks for. Revision 3 adds, on top of the
same curation:

* general instructions that describe the business **and define the KPIs**
* the **metric views marked as the preferred source** for KPI questions
* column synonyms: units / eaches / POS units; fill rate / service level; OTIF / on-time in-full;
  retailer code <-> label
* **8 curated example queries** for the hard parts: weekly grain, the fill-rate denominator, the
  quarter-over-same-quarter pattern, weeks-of-supply math, the decision loop
* the gold tables the questions need (orders, demand, inbound, retailer dimension), not only four

Example queries are deliberately *different* from the evaluation questions in ``questions.py``
(other retailers, other thresholds, other quarters). Teaching Genie the exact test would make
the score meaningless.

    python -m src.genie.space_full --validate     # execute every example and reference query
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import src.config as config  # noqa: E402
from src.genie.space_definition import _col, _id, _lines  # noqa: E402

G = config.GOLD
T = {n: f"{G}.{n}" for n in (
    "dim_product", "dim_retailer_dc", "fct_allocation_decisions", "fct_demand_forecast",
    "fct_demand_forecast_eval", "fct_demand_weekly", "fct_inbound_eta", "fct_orders_fulfillment",
    "fct_stockout_risk", "mv_fill_rate", "mv_otif", "mv_forecast_accuracy", "mv_weeks_of_supply")}

INSTRUCTIONS: list[str] = [
    "BUSINESS",
    "This space covers a licensed toy and collectibles manufacturer (collectible plush and licensed "
    "action figures) selling wholesale to three retail accounts from four distribution centres. "
    "All data is synthetic.",
    "Accounts: MASS_A = Mass Retail Account A, MASS_B = Mass Retail Account B, ETAIL_C = E-tail "
    "Account C. Distribution centres: DC_EAST, DC_CENTRAL, DC_WEST, DC_SOUTH.",
    "Units are eaches. Weeks end on Saturday and the weekly grain is the unit of analysis. The latest "
    "actual week is 2026-09-26; the forecast covers the 13 weeks after it. Licence families are "
    "EVERGREEN, FILM_TIE_IN and SOCIAL_TREND. Brands are Snugglewick, Titan Squad and Glowberry Pets.",
    "KPI DEFINITIONS (use exactly these)",
    "Fill rate (service level) = shipped units divided by REQUESTED units. The confirmed-basis "
    "figure flatters performance; show it only when asked for it.",
    "OTIF (on-time in-full) = PO lines shipped within a 2-day grace AND complete, divided by all PO lines.",
    "Forecast accuracy = WAPE = sum of absolute error divided by sum of actual units, at "
    "SKU x retailer x week. Lower is better, so the 'worst' accuracy is the HIGHEST WAPE.",
    "Weeks of supply = latest on-hand units divided by mean weekly POS units over the last 13 weeks.",
    "Stockout-weeks = SKU x retailer x DC x week cells where demand exceeded the units served "
    "(fct_demand_weekly.is_stockout_week).",
    "Compliance chargeback $ = retailer penalties on non-compliant PO lines. Markdown or excess $ = "
    "margin forgone clearing excess stock. Inventory turns = 52 divided by weeks of supply.",
    "WHERE TO LOOK",
    "For fill rate, OTIF, forecast accuracy and weeks of supply ALWAYS use the metric views "
    "(mv_fill_rate, mv_otif, mv_forecast_accuracy, mv_weeks_of_supply) with MEASURE(). They are the "
    "governed definitions, shared with the app. Never average a percentage column and never "
    "recompute a ratio from a fact table.",
    "fct_stockout_risk holds ONLY the SKU x retailer pairs projected to run short. Risk tiers rank "
    "CRITICAL > HIGH > MEDIUM > LOW; order by severity_score for 'worst'. For total or portfolio "
    "demand use fct_demand_forecast joined to dim_product, not fct_stockout_risk.",
    "fct_allocation_decisions holds planner decisions: action is accept, override or defer; "
    "recommended_units is what the agent proposed; override_delta_units = planner_units minus "
    "recommended_units and is signed.",
    "'Last quarter' means the most recent complete calendar quarter; 'the same quarter last year' is "
    "exactly twelve months earlier. Quarter is a dimension of mv_fill_rate and mv_otif.",
    "Unit cost and gross margin are masked for everyone except finance. If they come back NULL, say "
    "they are not available to this user. Never estimate them.",
]

#: (question, SQL, guidance). Different on purpose from the evaluation questions.
EXAMPLES: list[tuple[str, str, str]] = [
    ("Which SKUs at MASS_B will run out within 6 weeks, worst first?",
     f"SELECT sku_id, brand, license_family, risk_tier, severity_score, weeks_to_stockout,\n"
     f"       projected_stockout_week, units_at_risk, revenue_at_risk_usd\n"
     f"FROM {T['fct_stockout_risk']}\nWHERE retailer_code = 'MASS_B' AND weeks_to_stockout <= 6\n"
     f"ORDER BY severity_score DESC",
     "At-risk lists come from fct_stockout_risk, filtered on weeks_to_stockout and ranked by severity_score."),
    ("What is fill rate and order acceptance by retailer?",
     f"SELECT `Retailer`,\n       MEASURE(`Fill Rate Pct`) AS fill_rate_pct,\n"
     f"       MEASURE(`Fill Rate Confirmed Basis Pct`) AS fill_rate_confirmed_basis_pct,\n"
     f"       MEASURE(`Order Acceptance Pct`) AS order_acceptance_pct\n"
     f"FROM {T['mv_fill_rate']}\nGROUP BY ALL\nORDER BY `Retailer`",
     "Fill rate is shipped over REQUESTED units. The confirmed basis is higher because it ignores "
     "demand the supplier declined; report the requested basis unless asked."),
    ("Compare MASS_B fill rate in Q2 2026 with Q2 2025.",
     f"SELECT `Retailer`, `Quarter`, MEASURE(`Fill Rate Pct`) AS fill_rate_pct\n"
     f"FROM {T['mv_fill_rate']}\n"
     f"WHERE `Retailer` = 'MASS_B' AND `Quarter` IN (TIMESTAMP '2026-04-01', TIMESTAMP '2025-04-01')\n"
     f"GROUP BY ALL\nORDER BY `Quarter`",
     "Quarter-over-same-quarter: filter the metric view's Quarter dimension to the two quarter "
     "start dates and group by it. Do not compute the ratio from the fact table."),
    ("What are weeks of supply by licence family?",
     f"SELECT `License Family`,\n       MEASURE(`Weeks Of Supply`) AS weeks_of_supply,\n"
     f"       MEASURE(`On Hand Units`) AS on_hand_units,\n"
     f"       MEASURE(`Weekly Demand Units`) AS mean_weekly_pos_units\n"
     f"FROM {T['mv_weeks_of_supply']}\nGROUP BY ALL\nORDER BY weeks_of_supply",
     "Weeks of supply is on-hand divided by mean weekly POS over 13 weeks, a ratio of sums, so it "
     "is correct at any grouping. Use MEASURE(`Weeks Of Supply`)."),
    ("How accurate is the forecast for FILM_TIE_IN by brand?",
     f"SELECT `Brand`, MEASURE(`WAPE Pct`) AS wape_pct, MEASURE(`Bias Pct`) AS bias_pct\n"
     f"FROM {T['mv_forecast_accuracy']}\nWHERE `License Family` = 'FILM_TIE_IN'\nGROUP BY ALL\n"
     f"ORDER BY wape_pct DESC",
     "Forecast accuracy is WAPE from mv_forecast_accuracy; higher WAPE is worse."),
    ("What is the total 13-week p90 forecast by licence family?",
     f"SELECT p.license_family, SUM(f.forecast_p50) AS p50_units, SUM(f.forecast_p90) AS p90_units\n"
     f"FROM {T['fct_demand_forecast']} f\nJOIN {T['dim_product']} p ON p.sku_id = f.sku_id\n"
     f"GROUP BY p.license_family\nORDER BY p90_units DESC",
     "Portfolio forecast comes from fct_demand_forecast joined to dim_product for attributes, "
     "never from the at-risk-only fct_stockout_risk."),
    ("How many overrides did each planner make, and what was the net change in units?",
     f"SELECT planner_id, COUNT(*) AS overrides, SUM(recommended_units) AS recommended_units,\n"
     f"       SUM(planner_units) AS planner_units, SUM(override_delta_units) AS net_units_changed\n"
     f"FROM {T['fct_allocation_decisions']}\nWHERE action = 'override'\nGROUP BY planner_id\n"
     f"ORDER BY overrides DESC",
     "The decision loop: override_delta_units is signed (planner minus recommended); sum it for net "
     "change and sum its absolute value for units moved."),
    ("Which inbound shipments are more than 10 days later than first promised?",
     f"SELECT sku_id, dc_code, asn_id, original_eta_date, revised_eta_date, eta_slip_days\n"
     f"FROM {T['fct_inbound_eta']}\nWHERE eta_slip_days > 10\nORDER BY eta_slip_days DESC\nLIMIT 50",
     "ETA slip is revised ETA minus original ETA, in days, on fct_inbound_eta."),
]

SAMPLE_QUESTIONS = [
    "Which SKUs are projected to stock out at MASS_A in the next 4 weeks?",
    "What's our fill rate by retailer last quarter vs. the same quarter last year?",
    "Which license family has the worst forecast accuracy?",
    "What are weeks of supply by distribution centre?",
    "Show me the allocation overrides planners made last week and the agent's original recommendation.",
]

SYN_UNITS = ["units", "eaches", "POS units"]


def _tables() -> list[dict[str, Any]]:
    def cc(*cols: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(cols, key=lambda c: c["column_name"])

    retail = _col("retailer_code", "Retailer account code.", ["retailer", "account", "customer"], entity=True)
    tabs = [
        {"identifier": T["dim_product"], "column_configs": cc(
            _col("sku_id", entity=True), _col("product_name", entity=True), _col("brand", entity=True),
            _col("license_family", "EVERGREEN, FILM_TIE_IN or SOCIAL_TREND.", ["licence family", "license"], True),
            _col("unit_cost_usd", "MASKED by Unity Catalog for every role except finance."),
            _col("gross_margin_pct", "MASKED by Unity Catalog for every role except finance."))},
        {"identifier": T["dim_retailer_dc"], "column_configs": cc(
            retail, _col("retailer_label", "Readable account name; maps to retailer_code.",
                         ["retailer name", "account name"], True),
            _col("dc_code", entity=True), _col("dc_label", "Readable DC name; maps to dc_code.", ["DC name"]),
            _col("contractual_fill_commitment", "Contractual fill-rate commitment (0-1).",
                 ["fill commitment", "service level commitment"]))},
        {"identifier": T["fct_allocation_decisions"], "column_configs": cc(
            _col("action", "accept, override or defer.", ["decision", "outcome"], True),
            _col("recommended_units", "Units the agent proposed.", ["agent recommendation", "recommended"]),
            _col("planner_units", "Units the planner committed.", ["planner decision"]),
            _col("override_delta_units", "planner_units minus recommended_units. Signed.", ["units changed"]),
            _col("planner_id", entity=True), retail)},
        {"identifier": T["fct_demand_forecast"], "column_configs": cc(
            _col("forecast_p50", "Median weekly demand forecast, in eaches.", SYN_UNITS + ["forecast", "p50"]),
            _col("forecast_p90", "High-case (90th percentile) weekly forecast, in eaches.", ["high case", "p90"]),
            _col("horizon", "Weeks ahead of the forecast origin, 1 to 13."), retail)},
        {"identifier": T["fct_demand_forecast_eval"], "column_configs": cc(
            _col("actual_units", "Actual unconstrained demand in the holdout week, in eaches.", SYN_UNITS),
            _col("forecast_p50", "Forecast for the same week.", ["forecast"]),
            _col("abs_error", "Absolute error in eaches."), retail)},
        {"identifier": T["fct_demand_weekly"], "column_configs": cc(
            _col("pos_units", "Units sold through to consumers, in eaches.", SYN_UNITS),
            _col("true_demand_units", "Unconstrained consumer demand in eaches.", ["demand", "demand units"]),
            _col("lost_units", "True demand minus POS units: sales lost to stockouts.", ["lost sales units"]),
            _col("is_stockout_week", "True when demand exceeded what was served.", ["stockout week"]),
            _col("week_end_date", "Retail week ending Saturday.", ["week"]), retail,
            _col("dc_code", entity=True))},
        {"identifier": T["fct_inbound_eta"], "column_configs": cc(
            _col("eta_slip_days", "Revised ETA minus original ETA, in days.", ["delay", "late by", "slip"]),
            _col("revised_eta_date", "Current expected arrival.", ["ETA"]),
            _col("original_eta_date", "First promised arrival."),
            _col("shipped_units", "Units on the water.", SYN_UNITS), _col("dc_code", entity=True))},
        {"identifier": T["fct_orders_fulfillment"], "column_configs": cc(
            _col("requested_units", "Units the retailer asked for: the fill-rate DENOMINATOR.", SYN_UNITS),
            _col("shipped_units", "Units shipped: the fill-rate numerator.", SYN_UNITS),
            _col("is_otif", "True when shipped on time and in full.", ["on-time in-full", "OTIF"]),
            _col("chargeback_usd", "Retailer compliance chargeback on the line.", ["chargeback", "penalty"]),
            retail, _col("dc_code", entity=True))},
        {"identifier": T["fct_stockout_risk"], "column_configs": cc(
            _col("risk_tier", "CRITICAL, HIGH, MEDIUM or LOW.", ["risk level", "tier"], True),
            _col("severity_score", "0-100 ranking score; higher is worse.", ["severity", "risk score"]),
            _col("units_at_risk", "Forecast p90 units not covered by supply, in eaches.",
                 ["shortfall"] + SYN_UNITS),
            _col("revenue_at_risk_usd", "Wholesale revenue exposed.", ["revenue at risk", "exposure"]),
            _col("weeks_to_stockout", "Weeks until the projected stockout.", ["time to stockout"]),
            _col("projected_stockout_week", "First week p90 demand exceeds supply.", ["stockout date"]),
            _col("forecast_p50_13w", "13-week p50 demand for the AT-RISK series ONLY; summing it "
                 "understates total demand. For portfolio demand use fct_demand_forecast.", []),
            _col("forecast_p90_13w", "13-week p90 demand for the at-risk series ONLY.", []),
            _col("license_family", entity=True), _col("brand", entity=True), retail,
            _col("retailer_label", "Readable account name.", ["retailer name"], True))},
        {"identifier": T["mv_fill_rate"], "column_configs": cc(
            _col("Fill Rate Pct", "Shipped / REQUESTED units. The governed fill rate.",
                 ["fill rate", "service level", "fill %"]),
            _col("Order Acceptance Pct", "Confirmed / requested units.", ["acceptance rate"]),
            _col("Chargeback USD", "Retailer compliance chargebacks.", ["chargebacks", "chargeback $"]),
            _col("Retailer", "Account code.", ["retailer", "account"], True),
            _col("Requested Units", "Fill-rate denominator, in eaches.", SYN_UNITS),
            _col("Shipped Units", "Fill-rate numerator, in eaches.", SYN_UNITS))},
        {"identifier": T["mv_otif"], "column_configs": cc(
            _col("OTIF Pct", "On-time AND in-full lines / all lines.",
                 ["OTIF", "on-time in-full", "on time in full"]),
            _col("On Time Pct", "Lines shipped within the grace.", ["on-time"]),
            _col("In Full Pct", "Lines shipped complete.", ["in-full"]))},
        {"identifier": T["mv_forecast_accuracy"], "column_configs": cc(
            _col("WAPE Pct", "Weighted absolute % error. Lower is better.",
                 ["forecast accuracy", "forecast error", "WAPE"]),
            _col("Bias Pct", "Net over- or under-forecast.", ["bias"]),
            _col("P90 Coverage Pct", "Share of actuals under the p90 forecast; target 90.",
                 ["interval coverage"]))},
        {"identifier": T["mv_weeks_of_supply"], "column_configs": cc(
            _col("Weeks Of Supply", "Latest on-hand / mean weekly POS over 13 weeks.",
                 ["weeks of supply", "inventory cover", "WOS", "cover"]),
            _col("On Hand Units", "Latest on-hand, in eaches.", SYN_UNITS),
            _col("Weekly Demand Units", "Mean weekly POS units over 13 weeks.", SYN_UNITS))},
    ]
    tabs.sort(key=lambda t: t["identifier"])
    return tabs


def _joins() -> list[dict[str, Any]]:
    def j(name: str, left: str, right: str, on: str, kind: str) -> dict[str, Any]:
        return {"id": _id(name), "left": {"identifier": T[left], "alias": left},
                "right": {"identifier": T[right], "alias": right},
                "sql": [on, f"--rt=FROM_RELATIONSHIP_TYPE_{kind}--"]}
    return sorted([
        j("risk_product", "fct_stockout_risk", "dim_product",
          "`fct_stockout_risk`.`sku_id` = `dim_product`.`sku_id`", "MANY_TO_ONE"),
        j("forecast_product", "fct_demand_forecast", "dim_product",
          "`fct_demand_forecast`.`sku_id` = `dim_product`.`sku_id`", "MANY_TO_ONE"),
        j("orders_product", "fct_orders_fulfillment", "dim_product",
          "`fct_orders_fulfillment`.`sku_id` = `dim_product`.`sku_id`", "MANY_TO_ONE"),
        j("demand_product", "fct_demand_weekly", "dim_product",
          "`fct_demand_weekly`.`sku_id` = `dim_product`.`sku_id`", "MANY_TO_ONE"),
        j("inbound_product", "fct_inbound_eta", "dim_product",
          "`fct_inbound_eta`.`sku_id` = `dim_product`.`sku_id`", "MANY_TO_ONE"),
        j("orders_retailer_dc", "fct_orders_fulfillment", "dim_retailer_dc",
          "`fct_orders_fulfillment`.`retailer_code` = `dim_retailer_dc`.`retailer_code` AND "
          "`fct_orders_fulfillment`.`dc_code` = `dim_retailer_dc`.`dc_code`", "MANY_TO_ONE"),
        j("decisions_risk", "fct_allocation_decisions", "fct_stockout_risk",
          "`fct_allocation_decisions`.`sku_id` = `fct_stockout_risk`.`sku_id` AND "
          "`fct_allocation_decisions`.`retailer_code` = `fct_stockout_risk`.`retailer_code`", "MANY_TO_ONE"),
    ], key=lambda x: x["id"])


def build() -> dict[str, Any]:
    from src.genie.questions import QUESTIONS

    risk = T["fct_stockout_risk"]
    space = {
        "version": 2,
        "config": {"sample_questions": sorted(
            [{"id": _id(f"sq3_{i}"), "question": [q]} for i, q in enumerate(SAMPLE_QUESTIONS)],
            key=lambda x: x["id"])},
        "data_sources": {"tables": _tables()},
        "instructions": {
            "text_instructions": [{"id": _id("text3"), "content": [t + "\n" for t in INSTRUCTIONS]}],
            "example_question_sqls": sorted(
                [{"id": _id(f"ex3_{i}"), "question": [q], "sql": _lines(s), "usage_guidance": [g]}
                 for i, (q, s, g) in enumerate(EXAMPLES)], key=lambda x: x["id"]),
            "join_specs": _joins(),
            "sql_snippets": {
                "filters": [{"id": _id("f3_crit"), "sql": [f"`{risk}`.risk_tier IN ('CRITICAL', 'HIGH')"],
                             "display_name": "Critical or high risk",
                             "instruction": ["When the user asks for serious, urgent or top-tier risk"],
                             "synonyms": ["urgent", "serious", "high priority"]}],
                "measures": [{"id": _id("m3_rev"), "sql": [f"SUM(`{risk}`.revenue_at_risk_usd) AS total_revenue_at_risk"],
                              "display_name": "Total revenue at risk",
                              "instruction": ["When asked how much revenue is exposed or at risk"],
                              "synonyms": ["exposure", "revenue exposure"]}],
            },
        },
        "benchmarks": {"questions": sorted(
            [{"id": _id(f"bm3_{q.key}"), "question": [q.question],
              "answer": [{"format": "SQL", "content": _lines(q.reference_sql)}]}
             for q in QUESTIONS if q.check != "refusal"], key=lambda x: x["id"])},
    }
    return space


def validate() -> int:
    """Execute every example query and every reference query; fail on the first error."""
    from src.genie.questions import QUESTIONS
    from tools.dbsql import sql

    bad = 0
    for label, q in [(f"example {i + 1}", s) for i, (_, s, _) in enumerate(EXAMPLES)] + [
            (q.key, q.reference_sql) for q in QUESTIONS]:
        try:
            _, rows = sql(q)
            print(f"  OK    {label:28} {len(rows)} rows")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  FAIL  {label:28} {str(e)[:160]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(validate() if "--validate" in sys.argv else 0)
