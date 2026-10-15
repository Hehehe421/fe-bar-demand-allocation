"""The Genie space, as a ``serialized_space`` document.

Schema: ``references/example-serialized-space.json`` in the ``genie-rooms`` skill
(version 2). The CLI does not document it; the REST API returns it from
``GET /api/2.0/genie/spaces/{id}?include_serialized_space=true``.

Design follows the skill's fix order -- data, then metadata and synonyms, then
joins, then curated SQL, and only last general instructions:

  * a small surface: 4 tables + the 4 governed metric views, not all 20 objects
  * ratio KPIs (fill rate, OTIF, WAPE, weeks of supply) live in metric views, so
    Genie is steered to ``MEASURE()`` by curation, not by prose
  * ``INSTRUCTIONS`` starts minimal on purpose and grows only to patch a failure
    actually observed by ``run_questions.py``; each addition is recorded in
    ``INSTRUCTION_REVISIONS`` with the question that motivated it
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Final

import src.config as config

G = config.GOLD
_NS = uuid.UUID("6f1d0c1e-8a52-4b7e-9c11-0f4e6d2a9b30")


def _id(name: str) -> str:
    """Deterministic 32-hex opaque id, stable across re-creates."""
    return uuid.uuid5(_NS, name).hex


def _lines(sql: str) -> list[str]:
    parts = sql.strip().split("\n")
    return [p + "\n" for p in parts[:-1]] + [parts[-1]]


def _col(name: str, desc: str | None = None, syn: list[str] | None = None,
         entity: bool = False) -> dict[str, Any]:
    c: dict[str, Any] = {"column_name": name, "enable_format_assistance": True}
    if desc:
        c["description"] = [desc]
    if syn:
        c["synonyms"] = syn
    if entity:
        c["enable_entity_matching"] = True
    return c


# --------------------------------------------------------------------------
# Revisions (``rev``): 1 = initial draft. 2 = column descriptions on the two
# misleading ``forecast_*_13w`` columns, added after q11 summed an at-risk-only
# column as if it were total demand. Instruction text starts thin (rev 1) on purpose.
# --------------------------------------------------------------------------
LATEST_REV: Final[int] = 2

INSTRUCTION_REVISIONS: Final[list[dict[str, Any]]] = [
    {
        "rev": 1,
        "motivation": "initial draft: orientation only; curation carries the semantics",
        "text": [
            "This space answers planning questions about demand forecasts, projected "
            "stockouts, retailer service levels and planner allocation decisions for a "
            "licensed toy and collectibles manufacturer. All data is synthetic.",
            "Retailer codes are MASS_A, MASS_B and ETAIL_C. Licence families are "
            "EVERGREEN, FILM_TIE_IN and SOCIAL_TREND.",
        ],
    },
]


def current_instructions(rev: int | None = None) -> list[str]:
    revs = [r for r in INSTRUCTION_REVISIONS if rev is None or r["rev"] <= rev]
    out: list[str] = []
    for r in revs:
        # The server concatenates `content` entries with NO separator (two bare
        # sentences came back as "synthetic.Retailer"), so each entry is one line
        # and must carry its own newline.
        out.extend(t + "\n" for t in r["text"])
    return out


def build(rev: int | None = None) -> dict[str, Any]:
    risk, fc, dec, prod = (f"{G}.{t}" for t in
                           ("fct_stockout_risk", "fct_demand_forecast",
                            "fct_allocation_decisions", "dim_product"))
    mv_acc, mv_fill, mv_otif, mv_wos = (config.MV_FORECAST_ACCURACY, config.MV_FILL_RATE,
                                        config.MV_OTIF, config.MV_WEEKS_OF_SUPPLY)

    tables = [
        {"identifier": dec, "column_configs": sorted([
            _col("action", "What the planner did with the recommendation.",
                 ["decision", "outcome"], entity=True),
            _col("override_delta_units", "planner_units minus recommended_units. Signed.",
                 ["units changed", "adjustment"]),
            _col("planner_units", "Units the planner actually allocated."),
            _col("recommended_units", "Units the agent recommended."),
            _col("retailer_code", entity=True),
            _col("revenue_at_risk_usd", "Revenue at risk on the risk row that triggered it."),
        ], key=lambda c: c["column_name"])},
        {"identifier": fc, "column_configs": sorted([
            _col("forecast_p50", "Median weekly demand forecast, in units.",
                 ["forecast", "expected demand", "p50"]),
            _col("forecast_p90", "Upper (90th percentile) weekly demand forecast, in units.",
                 ["high case", "p90", "upside"]),
            _col("horizon", "Weeks ahead of the forecast origin, 1 to 13."),
            _col("retailer_code", entity=True),
        ], key=lambda c: c["column_name"])},
        {"identifier": risk, "column_configs": sorted([
            _col("license_family", "Licensing programme: EVERGREEN, FILM_TIE_IN or SOCIAL_TREND.",
                 ["licence family", "license", "licence", "franchise"], entity=True),
            _col("brand", entity=True),
            _col("retailer_code", entity=True),
            _col("risk_tier", "Severity band: CRITICAL, HIGH, MEDIUM or LOW.",
                 ["risk level", "risk band", "tier"], entity=True),
            _col("severity_score", "0-100 ranking score; higher is worse.",
                 ["severity", "risk score"]),
            _col("units_at_risk", "Forecast p90 units that cannot be covered by supply.",
                 ["shortfall", "units short"]),
            _col("revenue_at_risk_usd", "Wholesale revenue exposed by units_at_risk.",
                 ["revenue at risk", "exposure", "lost revenue"]),
            _col("projected_stockout_week", "First week cumulative p90 demand exceeds supply.",
                 ["stockout date", "run out date"]),
            _col("weeks_to_stockout", "Weeks from the forecast origin until stockout.",
                 ["weeks until stockout", "time to stockout"]),
            *(([
                _col("forecast_p50_13w",
                     "13-week p50 demand for the at-risk series ONLY. This table holds just "
                     "the SKU x retailer pairs projected to run short, so summing this column "
                     "understates total demand. For total, portfolio or by-brand forecast "
                     "demand use fct_demand_forecast.",
                     ["13 week forecast"]),
                _col("forecast_p90_13w",
                     "13-week p90 demand for the at-risk series ONLY; see forecast_p50_13w.",
                     []),
            ]) if (rev is None or rev >= 2) else []),
        ], key=lambda c: c["column_name"])},
        {"identifier": prod, "column_configs": sorted([
            _col("sku_id", entity=True),
            _col("product_name", entity=True),
            _col("brand", entity=True),
            _col("license_family", entity=True),
            _col("unit_cost_usd", "MASKED by Unity Catalog for every role except finance."),
            _col("gross_margin_pct", "MASKED by Unity Catalog for every role except finance."),
        ], key=lambda c: c["column_name"])},
        {"identifier": mv_acc}, {"identifier": mv_fill},
        {"identifier": mv_otif}, {"identifier": mv_wos},
    ]
    tables.sort(key=lambda t: t["identifier"])

    def rt(kind: str) -> str:
        return f"--rt=FROM_RELATIONSHIP_TYPE_{kind}--"

    join_specs = [
        {"id": _id("join_risk_product"),
         "left": {"identifier": risk, "alias": "fct_stockout_risk"},
         "right": {"identifier": prod, "alias": "dim_product"},
         "sql": ["`fct_stockout_risk`.`sku_id` = `dim_product`.`sku_id`", rt("MANY_TO_ONE")]},
        {"id": _id("join_forecast_product"),
         "left": {"identifier": fc, "alias": "fct_demand_forecast"},
         "right": {"identifier": prod, "alias": "dim_product"},
         "sql": ["`fct_demand_forecast`.`sku_id` = `dim_product`.`sku_id`", rt("MANY_TO_ONE")]},
        {"id": _id("join_decisions_risk"),
         "left": {"identifier": dec, "alias": "fct_allocation_decisions"},
         "right": {"identifier": risk, "alias": "fct_stockout_risk"},
         "sql": ["`fct_allocation_decisions`.`sku_id` = `fct_stockout_risk`.`sku_id` AND "
                 "`fct_allocation_decisions`.`retailer_code` = `fct_stockout_risk`.`retailer_code`",
                 rt("MANY_TO_ONE")]},
    ]

    examples = [
        ("Show the worst at-risk SKUs for ETAIL_C",
         f"SELECT sku_id, brand, license_family, risk_tier, severity_score, revenue_at_risk_usd\n"
         f"FROM {risk}\nWHERE retailer_code = 'ETAIL_C'\n"
         f"ORDER BY severity_score DESC\nLIMIT 10",
         "Ranking at-risk items: order by severity_score, not risk_tier."),
        ("What is the forecast accuracy for FILM_TIE_IN?",
         f"SELECT `License Family`, MEASURE(`WAPE Pct`) AS wape_pct, "
         f"MEASURE(`Bias Pct`) AS bias_pct\nFROM {mv_acc}\n"
         f"WHERE `License Family` = 'FILM_TIE_IN'\nGROUP BY ALL",
         "Accuracy and other ratio KPIs come from the metric view via MEASURE()."),
        ("How many SKUs are at HIGH risk per brand?",
         f"SELECT brand, COUNT(DISTINCT sku_id) AS skus\nFROM {risk}\n"
         f"WHERE risk_tier = 'HIGH'\nGROUP BY brand\nORDER BY skus DESC",
         "Count distinct SKUs when asked about SKUs; rows are SKU x retailer."),
    ]

    from src.genie.questions import QUESTIONS

    space = {
        "version": 2,
        "config": {"sample_questions": [
            {"id": _id(f"sq{i}"), "question": [q]} for i, q in enumerate([
                "Which licence family has the most revenue at risk?",
                "Show the 5 worst at-risk SKUs for MASS_A",
                "How accurate is the forecast for SOCIAL_TREND items?",
                "What is each retailer's fill rate?",
                "How many allocation decisions did planners override?",
            ])]},
        "data_sources": {"tables": tables},
        "instructions": {
            "text_instructions": [{"id": _id("text_instructions"),
                                   "content": current_instructions(rev)}],
            "example_question_sqls": [
                {"id": _id(f"ex{i}"), "question": [q], "sql": _lines(s),
                 "usage_guidance": [g]}
                for i, (q, s, g) in enumerate(examples)],
            "join_specs": join_specs,
            "sql_snippets": {
                "filters": [{
                    "id": _id("f_crit_high"),
                    "sql": [f"`{risk}`.risk_tier IN ('CRITICAL', 'HIGH')"],
                    "display_name": "Critical or high risk",
                    "instruction": ["When the user asks for serious, urgent or top-tier risk"],
                    "synonyms": ["urgent", "serious", "high priority"]}],
                "measures": [{
                    "id": _id("m_rev_risk"),
                    "sql": [f"SUM(`{risk}`.revenue_at_risk_usd) AS total_revenue_at_risk"],
                    "display_name": "Total revenue at risk",
                    "instruction": ["When asked how much revenue is exposed or at risk"],
                    "synonyms": ["exposure", "revenue exposure"]}],
            },
        },
        "benchmarks": {"questions": [
            {"id": _id(f"bm_{q.key}"), "question": [q.question],
             "answer": [{"format": "SQL", "content": _lines(q.reference_sql)}]}
            for q in QUESTIONS if q.check != "refusal"]},
    }
    # opaque-id lists must be sorted by id for the server's validator
    for path in (("config", "sample_questions"), ("instructions", "example_question_sqls"),
                 ("instructions", "join_specs"), ("benchmarks", "questions")):
        node: Any = space
        for k in path[:-1]:
            node = node[k]
        node[path[-1]].sort(key=lambda x: x["id"])
    for k in ("filters", "measures"):
        space["instructions"]["sql_snippets"][k].sort(key=lambda x: x["id"])
    return space


def dumps(rev: int | None = None) -> str:
    return json.dumps(build(rev), indent=2)
