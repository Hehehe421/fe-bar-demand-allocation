"""The agent's five tools. All of them hit real Unity Catalog objects.

Every tool reads something a human planner would open a different system to
find, and returns JSON-serialisable structured data with explicit units so the
model can quote numbers rather than estimate them:

1. ``get_stockout_risk``    -> gold.fct_stockout_risk
2. ``get_inbound_eta``      -> gold.fct_inbound_eta
3. ``get_fill_rate_history``-> gold.mv_fill_rate (the governed metric view)
4. ``search_supplier_docs`` -> ml.supplier_doc_chunks, parsed from the PDFs
5. ``recommend_allocation`` -> deterministic Python in ``allocator.py``

SQL runs through the Statement Execution API rather than a Spark session,
because these tools also have to work inside Model Serving where there is no
Spark. Tool 3 deliberately queries the **metric view** instead of recomputing
fill rate, so the agent cannot disagree with the dashboard about what fill rate
means.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config  # noqa: E402
from agent.allocator import RetailerClaim, allocate  # noqa: E402

_WAREHOUSE = os.environ.get("DBX_WAREHOUSE_ID", config.WAREHOUSE_ID)
_PROFILE = os.environ.get("DBX_PROFILE_FOR_TOOLS") or None

_w: WorkspaceClient | None = None


def _client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient(profile=_PROFILE) if _PROFILE else WorkspaceClient()
    return _w


def _rows(query: str, limit: int = 200) -> list[dict[str, Any]]:
    """Execute ``query``; return rows as dicts with native Python types."""
    resp = _client().statement_execution.execute_statement(
        statement=query, warehouse_id=_WAREHOUSE, wait_timeout="50s"
    )
    while resp.status and resp.status.state in (
        StatementState.PENDING,
        StatementState.RUNNING,
    ):
        resp = _client().statement_execution.get_statement(resp.statement_id)
    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        err = getattr(resp.status.error, "message", resp.status.state)
        raise RuntimeError(f"tool query failed: {err}")

    cols = [c.name for c in (resp.manifest.schema.columns or [])] if resp.manifest else []
    types = (
        [str(c.type_name) for c in (resp.manifest.schema.columns or [])]
        if resp.manifest
        else []
    )
    data = (resp.result.data_array or []) if resp.result else []

    def coerce(v: Any, t: str) -> Any:
        if v is None:
            return None
        if any(k in t.upper() for k in ("INT", "LONG", "SHORT", "BYTE")):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v
        if any(k in t.upper() for k in ("DOUBLE", "FLOAT", "DECIMAL")):
            try:
                return float(v)
            except (TypeError, ValueError):
                return v
        if "BOOLEAN" in t.upper():
            return str(v).lower() == "true"
        return v

    return [
        {c: coerce(r[i], types[i] if i < len(types) else "") for i, c in enumerate(cols)}
        for r in data[:limit]
    ]


def _q(s: str) -> str:
    """Escape a value for inline SQL. Inputs come from an LLM, so never trust them."""
    return str(s).replace("'", "''").replace("\\", "")


# ---------------------------------------------------------------------------
# 1. stockout risk
# ---------------------------------------------------------------------------


def get_stockout_risk(
    sku_id: str | None = None,
    retailer_code: str | None = None,
    risk_tier: str | None = None,
    max_weeks_to_stockout: int | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Projected stockouts from `gold.fct_stockout_risk`."""
    where = ["1=1"]
    if sku_id:
        where.append(f"sku_id = '{_q(sku_id)}'")
    if retailer_code:
        where.append(f"retailer_code = '{_q(retailer_code)}'")
    if risk_tier:
        where.append(f"risk_tier = '{_q(risk_tier.upper())}'")
    if max_weeks_to_stockout is not None:
        where.append(f"weeks_to_stockout <= {int(max_weeks_to_stockout)}")

    rows = _rows(
        f"""
        SELECT sku_id, retailer_code, retailer_label, brand, license_family,
               lifecycle_stage, risk_tier, projected_stockout_week,
               weeks_to_stockout, units_at_risk, revenue_at_risk_usd,
               severity_score, sku_on_hand_units, sku_inbound_horizon_units,
               retailer_demand_share, forecast_p50_13w, forecast_p90_13w,
               wholesale_price_usd, contractual_fill_commitment,
               model_version, model_alias
        FROM {config.FCT_STOCKOUT_RISK}
        WHERE {' AND '.join(where)}
        ORDER BY severity_score DESC, revenue_at_risk_usd DESC
        LIMIT {int(limit)}
        """
    )
    return {
        "source_table": config.FCT_STOCKOUT_RISK,
        "units": "eaches",
        "row_count": len(rows),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 2. inbound ETA
# ---------------------------------------------------------------------------


def get_inbound_eta(sku_id: str, limit: int = 25) -> dict[str, Any]:
    """Inbound shipments still on the water for a SKU, from `gold.fct_inbound_eta`."""
    rows = _rows(
        f"""
        WITH asof AS (
          SELECT MAX(snapshot_date) AS d FROM {config.fq('gold', 'fct_inventory_position')}
        )
        SELECT i.asn_id, i.sku_id, i.dc_code, i.revised_eta_date, i.original_eta_date,
               i.eta_slip_days, i.shipped_units, i.ordered_units,
               i.short_shipped_units, i.transport_mode, i.freight_cost_usd,
               i.lead_time_days_planned, i.lead_time_days_actual,
               i.lead_time_variance_days, i.origin_country
        FROM {config.FCT_INBOUND_ETA} i CROSS JOIN asof a
        WHERE i.sku_id = '{_q(sku_id)}' AND i.revised_eta_date > a.d
        ORDER BY i.revised_eta_date
        LIMIT {int(limit)}
        """
    )
    total = sum(int(r.get("shipped_units") or 0) for r in rows)
    return {
        "source_table": config.FCT_INBOUND_ETA,
        "sku_id": sku_id,
        "units": "eaches",
        "shipments_on_water": len(rows),
        "total_inbound_units": total,
        "rows": rows,
        "note": (
            "Only shipments arriving AFTER the inventory snapshot date are listed, "
            "so these units are not already counted in on-hand."
        ),
    }


# ---------------------------------------------------------------------------
# 3. fill-rate history, through the governed metric view
# ---------------------------------------------------------------------------


def get_fill_rate_history(
    retailer_code: str | None = None,
    license_family: str | None = None,
    weeks: int = 8,
) -> dict[str, Any]:
    """Recent fill rate and OTIF from the `gold.mv_fill_rate` metric view."""
    where = []
    if retailer_code:
        where.append(f"`Retailer` = '{_q(retailer_code)}'")
    if license_family:
        where.append(f"`License Family` = '{_q(license_family)}'")
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = _rows(
        f"""
        SELECT `Retailer` AS retailer_code,
               `Week`     AS week_end_date,
               ROUND(MEASURE(`Fill Rate Pct`), 3)              AS fill_rate_pct,
               ROUND(MEASURE(`Order Acceptance Pct`), 3)       AS order_acceptance_pct,
               MEASURE(`Requested Units`)                      AS requested_units,
               MEASURE(`Shipped Units`)                        AS shipped_units,
               MEASURE(`Unfilled Units`)                       AS unfilled_units,
               ROUND(MEASURE(`Chargeback USD`), 2)             AS chargeback_usd
        FROM {config.MV_FILL_RATE}
        {clause}
        GROUP BY ALL
        ORDER BY week_end_date DESC
        LIMIT {int(weeks) * 3}
        """
    )
    agg = _rows(
        f"""
        SELECT `Retailer` AS retailer_code,
               ROUND(MEASURE(`Fill Rate Pct`), 3) AS fill_rate_pct_all_time,
               ROUND(MEASURE(`Chargeback Pct Of Invoiced`), 4) AS chargeback_pct
        FROM {config.MV_FILL_RATE} {clause}
        GROUP BY ALL ORDER BY ALL
        """
    )
    return {
        "source_metric_view": config.MV_FILL_RATE,
        "definition": "fill rate = shipped units / requested units",
        "note": (
            "Queried from the governed metric view, not recomputed, so this "
            "number cannot disagree with the dashboard or Genie."
        ),
        "recent_weeks": rows,
        "account_totals": agg,
    }


# ---------------------------------------------------------------------------
# 4. supplier document retrieval
# ---------------------------------------------------------------------------


def search_supplier_docs(query: str, limit: int = 4) -> dict[str, Any]:
    """Keyword retrieval over the parsed supplier PDFs in `ml.supplier_doc_chunks`."""
    terms = [t for t in "".join(
        ch if ch.isalnum() or ch in "-_ " else " " for ch in query.lower()
    ).split() if len(t) > 2][:10]
    if not terms:
        return {"query": query, "matches": 0, "rows": [], "note": "no usable search terms"}

    score = " + ".join(
        f"(CASE WHEN lower(chunk_text) LIKE '%{_q(t)}%' THEN 1 ELSE 0 END)" for t in terms
    )
    table = config.fq("ml", "supplier_doc_chunks")
    rows = _rows(
        f"""
        SELECT doc_file, doc_kind, supplier_name, page_number,
               {score} AS match_score,
               substr(chunk_text, 1, 900) AS chunk_text
        FROM {table}
        WHERE {score} > 0
        ORDER BY match_score DESC, doc_file
        LIMIT {int(limit)}
        """
    )
    return {
        "source_table": table,
        "query": query,
        "terms_used": terms,
        "matches": len(rows),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 5. deterministic allocation
# ---------------------------------------------------------------------------


def recommend_allocation(sku_id: str, week_end_date: str | None = None) -> dict[str, Any]:
    """Split this SKU's constrained supply across the accounts that are short.

    The arithmetic is entirely in `allocator.py`. This function's job is to
    assemble the inputs from Unity Catalog and hand back both the decision and
    every number that produced it, so the explanation can be checked.
    """
    risk = _rows(
        f"""
        SELECT r.sku_id, r.retailer_code, r.retailer_label, r.units_at_risk,
               r.severity_score, r.weeks_to_stockout, r.wholesale_price_usd,
               r.contractual_fill_commitment, r.sku_on_hand_units,
               r.sku_inbound_horizon_units, r.risk_tier,
               p.case_pack, p.gross_margin_pct, p.brand, p.license_family,
               p.lifecycle_stage
        FROM {config.FCT_STOCKOUT_RISK} r
        LEFT JOIN {config.fq('gold', 'dim_product')} p ON p.sku_id = r.sku_id
        WHERE r.sku_id = '{_q(sku_id)}'
        ORDER BY r.severity_score DESC
        """
    )
    if not risk:
        return {
            "sku_id": sku_id,
            "status": "no_risk",
            "message": (
                f"{sku_id} has no projected stockout in the current 13-week horizon, "
                "so there is nothing to allocate."
            ),
        }

    on_hand = max(int(r.get("sku_on_hand_units") or 0) for r in risk)
    inbound = max(int(r.get("sku_inbound_horizon_units") or 0) for r in risk)
    supply = on_hand + inbound

    # recent fill rate per account, for the contractual-gap term
    fills = {
        r["retailer_code"]: r.get("fill_rate_pct_all_time")
        for r in _rows(
            f"""
            SELECT `Retailer` AS retailer_code,
                   ROUND(MEASURE(`Fill Rate Pct`), 3) AS fill_rate_pct_all_time
            FROM {config.MV_FILL_RATE} GROUP BY ALL
            """
        )
    }

    claims = [
        RetailerClaim(
            retailer_code=r["retailer_code"],
            retailer_label=r.get("retailer_label") or r["retailer_code"],
            units_needed=int(r.get("units_at_risk") or 0),
            severity_score=float(r.get("severity_score") or 0.0),
            weeks_to_stockout=int(r.get("weeks_to_stockout") or 0),
            wholesale_price_usd=float(r.get("wholesale_price_usd") or 0.0),
            contractual_fill_commitment=float(r.get("contractual_fill_commitment") or 0.0),
            recent_fill_rate_pct=(
                float(fills[r["retailer_code"]])
                if fills.get(r["retailer_code"]) is not None
                else None
            ),
            gross_margin_pct=(
                float(r["gross_margin_pct"]) if r.get("gross_margin_pct") is not None else None
            ),
            case_pack=int(r.get("case_pack") or 1),
        )
        for r in risk
        if int(r.get("units_at_risk") or 0) > 0
    ]

    result = allocate(sku_id, supply, claims, week_end_date)
    d = result.to_dict()
    d.update({
        "source_tables": [config.FCT_STOCKOUT_RISK, config.MV_FILL_RATE],
        "supply_breakdown": {
            "on_hand_units": on_hand,
            "inbound_within_horizon_units": inbound,
            "total_available_units": supply,
        },
        "product": {
            "brand": risk[0].get("brand"),
            "license_family": risk[0].get("license_family"),
            "lifecycle_stage": risk[0].get("lifecycle_stage"),
            "case_pack": risk[0].get("case_pack"),
        },
        "units": "eaches",
    })
    return d


# ---------------------------------------------------------------------------
# tool registry: OpenAI-style schemas
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "get_stockout_risk": get_stockout_risk,
    "get_inbound_eta": get_inbound_eta,
    "get_fill_rate_history": get_fill_rate_history,
    "search_supplier_docs": search_supplier_docs,
    "recommend_allocation": recommend_allocation,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_stockout_risk",
            "description": (
                "Projected stockouts at SKU x retailer over the 13-week forecast "
                "horizon, with units and revenue at risk, severity score, risk tier "
                "and the on-hand plus inbound position. Use this first for any "
                "question about what is at risk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku_id": {"type": "string", "description": "e.g. SKU-00135"},
                    "retailer_code": {
                        "type": "string",
                        "enum": ["MASS_A", "MASS_B", "ETAIL_C"],
                    },
                    "risk_tier": {
                        "type": "string",
                        "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    },
                    "max_weeks_to_stockout": {"type": "integer"},
                    "limit": {"type": "integer", "default": 25},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inbound_eta",
            "description": (
                "Inbound shipments still on the water for a SKU: revised ETA, ETA "
                "slip against the original commit, units, transport mode (OCEAN or "
                "AIR) and freight cost. Use this to judge whether a projected "
                "stockout can be covered by supply already en route."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 25},
                },
                "required": ["sku_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fill_rate_history",
            "description": (
                "Recent fill rate, order acceptance and compliance chargebacks per "
                "retailer, read from the governed mv_fill_rate metric view. Use this "
                "to judge how far an account already sits below its contractual "
                "commitment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "retailer_code": {
                        "type": "string",
                        "enum": ["MASS_A", "MASS_B", "ETAIL_C"],
                    },
                    "license_family": {
                        "type": "string",
                        "enum": ["EVERGREEN", "FILM_TIE_IN", "SOCIAL_TREND"],
                    },
                    "weeks": {"type": "integer", "default": 8},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_supplier_docs",
            "description": (
                "Search the parsed supplier invoices and product specification "
                "sheets. Use this for questions about payment or shipping terms, "
                "quoted production lead times, material composition, country of "
                "origin or safety standards."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 4},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_allocation",
            "description": (
                "Compute how to split a SKU's constrained supply across the retailer "
                "accounts that are short. The split is calculated deterministically "
                "in Python from severity, value density and the gap to each "
                "account's contractual fill commitment, and the response includes "
                "every input used. ALWAYS call this before proposing any split, and "
                "quote its numbers rather than computing your own."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku_id": {"type": "string"},
                    "week_end_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["sku_id"],
            },
        },
    },
]


def call_tool(name: str, arguments: str | dict[str, Any]) -> str:
    """Dispatch a tool call and return a JSON string for the model."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"could not parse arguments: {e}"})
    try:
        return json.dumps(fn(**args), default=str)
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
    except Exception as e:  # noqa: BLE001 - surfaced to the model, not swallowed
        return json.dumps({"error": f"{name} failed: {type(e).__name__}: {e}"})
