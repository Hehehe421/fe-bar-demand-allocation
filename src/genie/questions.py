"""The evaluation questions for the Genie space, with reference SQL.

Each reference query is the ground truth a human would write against the same
tables. ``run_questions.py`` sends the question to Genie, captures the SQL Genie
generated and its rows, and compares them with the reference result, so "wrong"
is judged by data and not by eye.

``check`` names how the two results are compared:
  * ``rows``    -- the result sets must match exactly (after rounding)
  * ``scalar``  -- first value of the first row must match within 1%
  * ``values``  -- every numeric value in the reference result must appear among Genie's
                   numeric cells (row count and label formats may differ)
  * ``top1``    -- Genie's FIRST row must contain every cell of the reference's first row
  * ``refusal`` -- Genie must NOT return a number; it should say the data is not
                   available (used for the masked-column governance question)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import src.config as config

G = config.GOLD


@dataclass(frozen=True)
class Q:
    key: str
    question: str
    reference_sql: str
    check: str = "rows"
    why: str = ""


QUESTIONS: Final[tuple[Q, ...]] = (
    Q(
        "q01_critical_count",
        "How many SKU and retailer combinations are at CRITICAL risk, "
        "and how much revenue is at risk?",
        f"SELECT COUNT(*) AS n, ROUND(SUM(revenue_at_risk_usd), 0) AS revenue_at_risk "
        f"FROM {G}.fct_stockout_risk WHERE risk_tier = 'CRITICAL'",
        why="basic filter + aggregate on the risk table",
    ),
    Q(
        "q02_top_mass_a",
        "Show the 5 highest severity at-risk SKUs for MASS_A.",
        f"SELECT sku_id, severity_score FROM {G}.fct_stockout_risk "
        f"WHERE retailer_code = 'MASS_A' ORDER BY severity_score DESC, sku_id LIMIT 5",
        why="'highest severity' must map to severity_score, not risk_tier",
    ),
    Q(
        "q03_revenue_by_family",
        "Which licence family has the most revenue at risk?",
        f"SELECT license_family, ROUND(SUM(revenue_at_risk_usd), 0) AS revenue_at_risk "
        f"FROM {G}.fct_stockout_risk GROUP BY license_family "
        f"ORDER BY revenue_at_risk DESC LIMIT 1",
        why="spelling variant: licence vs license_family",
    ),
    Q(
        "q04_soon",
        "How many SKUs are projected to stock out within 4 weeks?",
        f"SELECT COUNT(DISTINCT sku_id) AS skus FROM {G}.fct_stockout_risk "
        f"WHERE weeks_to_stockout <= 4",
        check="scalar",
        why="distinct SKUs, not SKU x retailer rows",
    ),
    Q(
        "q05_wape_family",
        "What is forecast accuracy (WAPE) by licence family?",
        f"SELECT `License Family` AS license_family, ROUND(MEASURE(`WAPE Pct`), 1) AS wape_pct "
        f"FROM {G}.mv_forecast_accuracy GROUP BY ALL ORDER BY license_family",
        why="a ratio metric: must use the governed definition, never an average of row ratios",
    ),
    Q(
        "q06_p90_trend",
        "How often does the P90 forecast cover actual demand for SOCIAL_TREND items?",
        f"SELECT ROUND(MEASURE(`P90 Coverage Pct`), 1) AS p90_coverage_pct "
        f"FROM {G}.mv_forecast_accuracy WHERE `License Family` = 'SOCIAL_TREND'",
        check="scalar",
        why="the build's headline finding; must come from the metric view",
    ),
    Q(
        "q07_fill_rate",
        "What is the overall fill rate for each retailer?",
        f"SELECT Retailer, ROUND(MEASURE(`Fill Rate Pct`), 2) AS fill_rate_pct "
        f"FROM {G}.mv_fill_rate GROUP BY ALL ORDER BY Retailer",
        why="weighted fill rate, not an average of weekly percentages",
    ),
    Q(
        "q08_lowest_otif",
        "Which retailer has the lowest OTIF?",
        f"SELECT Retailer, ROUND(MEASURE(`OTIF Pct`), 2) AS otif_pct "
        f"FROM {G}.mv_otif GROUP BY ALL ORDER BY otif_pct ASC LIMIT 1",
        why="metric view selection",
    ),
    Q(
        "q09_decision_mix",
        "How many allocation decisions did planners accept, override and defer?",
        f"SELECT action, COUNT(*) AS decisions FROM {G}.fct_allocation_decisions "
        f"GROUP BY action ORDER BY action",
        why="the decision-capture table written by the app",
    ),
    Q(
        "q10a_override_gross",
        "How many units did planners move in total when they overrode a recommendation, "
        "counting increases and decreases alike?",
        f"SELECT SUM(ABS(override_delta_units)) AS units_moved "
        f"FROM {G}.fct_allocation_decisions WHERE action = 'override'",
        check="scalar",
        why="gross change. The first wording of this question ('by how many units in "
        "total did planners change...') was ambiguous: Genie read it as gross (46), the "
        "reference as net (14). Genie's reading was defensible; the question was the bug",
    ),
    Q(
        "q10b_override_net",
        "What was the net unit change from planner overrides, netting increases "
        "against decreases?",
        f"SELECT SUM(override_delta_units) AS net_units_changed "
        f"FROM {G}.fct_allocation_decisions WHERE action = 'override'",
        check="scalar",
        why="net change, stated unambiguously",
    ),
    Q(
        "q11_forecast_brand",
        "What is the total 13-week p50 forecast demand by brand?",
        f"SELECT p.brand, ROUND(SUM(f.forecast_p50), 0) AS p50_units "
        f"FROM {G}.fct_demand_forecast f JOIN {G}.dim_product p USING (sku_id) "
        f"GROUP BY p.brand ORDER BY p.brand",
        why="needs a join: the forecast table has no brand column",
    ),
    Q(
        "q12_margin",
        "What is the gross margin on SKU-01723?",
        f"SELECT gross_margin_pct FROM {G}.dim_product WHERE sku_id = 'SKU-01723'",
        check="refusal",
        why="governance: the Stage B column mask hides cost and margin; Genie must "
        "not invent one",
    ),

    Q(
        "q13_critical_by_account",
        "How many critical positions are there for each account?",
        f"SELECT retailer_code, COUNT(*) AS critical_positions FROM {G}.fct_stockout_risk "
        f"WHERE risk_tier = 'CRITICAL' GROUP BY retailer_code ORDER BY retailer_code",
        why="added for the executive tab's suggested questions",
    ),
    Q(
        "q14_revenue_by_tier",
        "What is the revenue at risk by risk tier?",
        f"SELECT risk_tier, ROUND(SUM(revenue_at_risk_usd), 0) AS revenue_at_risk "
        f"FROM {G}.fct_stockout_risk GROUP BY risk_tier ORDER BY risk_tier",
        why="added for the executive tab's suggested questions",
    ),

    # ---- rev3: the four questions the build prompt names, plus the KPIs it requires ----------
    Q(
        "q15_stockout_mass_a_4w",
        "Which SKUs are projected to stock out at MASS_A in the next 4 weeks?",
        f"SELECT DISTINCT sku_id FROM {G}.fct_stockout_risk "
        f"WHERE retailer_code = 'MASS_A' AND weeks_to_stockout <= 4 ORDER BY sku_id",
        why="rubric question 1: a filtered list, one row per SKU",
    ),
    Q(
        "q16_fill_qoq",
        "What's our fill rate by retailer last quarter vs. the same quarter last year?",
        f"SELECT Retailer, `Quarter`, ROUND(MEASURE(`Fill Rate Pct`), 2) AS fill_rate_pct "
        f"FROM {G}.mv_fill_rate "
        f"WHERE `Quarter` IN (TIMESTAMP '2026-07-01', TIMESTAMP '2025-07-01') GROUP BY ALL",
        check="values",
        why="rubric question 2: two quarters x three retailers from the governed metric view; "
        "'last quarter' = Q3 2026 as of 2026-10-15",
    ),
    Q(
        "q17_worst_wape_family",
        "Which license family has the worst forecast accuracy?",
        f"SELECT `License Family` AS license_family, ROUND(MEASURE(`WAPE Pct`), 1) AS wape_pct "
        f"FROM {G}.mv_forecast_accuracy GROUP BY ALL ORDER BY wape_pct DESC LIMIT 1",
        check="top1",
        why="rubric question 3: 'worst' accuracy is the HIGHEST WAPE, a direction trap",
    ),
    Q(
        "q18_overrides_last_week",
        "Show me the allocation overrides planners made last week and the agent's original "
        "recommendation.",
        f"SELECT recommended_units, planner_units FROM {G}.fct_allocation_decisions "
        f"WHERE action = 'override' "
        f"AND decided_at >= date_trunc('week', current_date()) - INTERVAL 7 DAYS "
        f"AND decided_at < date_trunc('week', current_date())",
        check="values",
        why="rubric question 4: the decision-capture loop; 'original recommendation' = "
        "recommended_units. "
        "The window is relative to today, so this reference moves with the calendar",
    ),
    Q(
        "q19_wos_dc",
        "What are weeks of supply by distribution centre?",
        f"SELECT `DC`, ROUND(MEASURE(`Weeks Of Supply`), 2) AS wos "
        f"FROM {G}.mv_weeks_of_supply GROUP BY `DC`",
        check="values",
        why="a required KPI; a ratio of sums, so averaging per-SKU values is wrong",
    ),
    Q(
        "q20_chargebacks",
        "How much compliance chargeback did each retailer incur?",
        f"SELECT Retailer, ROUND(MEASURE(`Chargeback USD`), 0) AS chargeback_usd "
        f"FROM {G}.mv_fill_rate GROUP BY ALL",
        check="values",
        why="a required KPI (compliance chargeback $)",
    ),
    Q(
        "q21_stockout_weeks",
        "How many stockout weeks did MASS_B have in 2026?",
        f"SELECT COUNT(*) AS n FROM {G}.fct_demand_weekly WHERE retailer_code = 'MASS_B' "
        f"AND is_stockout_week AND year(week_end_date) = 2026",
        check="scalar",
        why="a required KPI; counts SKU x DC x week cells where demand exceeded service",
    ),
    Q(
        "q22_inbound_slip",
        "How many inbound shipments are more than 14 days later than first promised?",
        f"SELECT COUNT(*) AS n FROM {G}.fct_inbound_eta WHERE eta_slip_days > 14",
        check="scalar",
        why="inbound ETA table; slip = revised minus original",
    ),
    Q(
        "q23_brand_critical",
        "Which brand has the most critical positions?",
        f"SELECT brand, COUNT(*) AS n FROM {G}.fct_stockout_risk WHERE risk_tier = 'CRITICAL' "
        f"GROUP BY brand ORDER BY n DESC LIMIT 1",
        check="top1",
        why="entity matching on brand; top-1 of a count",
    ),
    Q(
        "q24_forecast_etail",
        "What is the total 13-week p90 forecast demand for ETAIL_C?",
        f"SELECT ROUND(SUM(forecast_p90), 0) AS p90_units FROM {G}.fct_demand_forecast "
        f"WHERE retailer_code = 'ETAIL_C'",
        check="scalar",
        why="portfolio demand must come from fct_demand_forecast, not the at-risk-only table",
    ),
    Q(
        "q25_unit_cost",
        "What is the unit cost of SKU-00042?",
        f"SELECT unit_cost_usd FROM {G}.dim_product WHERE sku_id = 'SKU-00042'",
        check="refusal",
        why="governance: cost is masked for everyone except finance",
    ),
)
