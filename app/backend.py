"""Data access for the Allocation Command Center.

Three stores, each used for what it is good at:

* **Lakebase (Postgres)** -- the app's own state and its low-latency read path:
  the at-risk worklist and the 13-week forecast (synced from Unity Catalog), and
  the decisions and agent calls the app writes.
* **SQL warehouse** -- analytical reads that have no Lakebase copy: the governed
  KPI metric views, and the one feature row for the real-time what-if.
* **Model Serving / Genie** -- the agent, the real-time forecaster, and
  natural-language questions.

Everything authenticates through the SDK's default config: the app's service
principal when deployed, the developer's CLI profile locally. No token is stored.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import psycopg
import settings as S
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from psycopg.rows import dict_row

_w: WorkspaceClient | None = None
_lock = threading.Lock()


def w() -> WorkspaceClient:
    global _w
    with _lock:
        if _w is None:
            _w = WorkspaceClient()
        return _w


# --------------------------------------------------------------------------
# Lakebase
# --------------------------------------------------------------------------

_cred: dict[str, Any] = {"token": None, "exp": 0.0, "user": None}


def _pg_identity() -> tuple[str, str]:
    """Short-lived OAuth token + role name. Refreshed well inside the 1 h expiry."""
    now = time.time()
    if now >= _cred["exp"]:
        tok = w().postgres.generate_database_credential(endpoint=S.PG_ENDPOINT).token
        if not tok:
            raise RuntimeError("Lakebase returned no credential")
        _cred.update(token=tok, exp=now + 45 * 60)
    if not _cred["user"]:
        _cred["user"] = w().current_user.me().user_name
    return _cred["user"], _cred["token"]


def pg() -> psycopg.Connection:
    user, token = _pg_identity()
    return psycopg.connect(
        host=S.PG_HOST, dbname=S.PG_DATABASE, user=user, password=token,
        sslmode="require", connect_timeout=15, row_factory=dict_row,
    )


def _pg_rows(query: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with pg() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def worklist(scope: str | None, tiers: list[str], limit: int = 250) -> list[dict[str, Any]]:
    """At-risk rows ranked by severity, each with the planner's latest decision."""
    return _pg_rows(
        """
        SELECT r.sku_id, r.retailer_code, r.brand, r.license_family, r.lifecycle_stage,
               r.risk_tier, r.severity_score, r.projected_stockout_week,
               r.weeks_to_stockout, r.units_at_risk, r.revenue_at_risk_usd,
               d.action AS last_action
        FROM public.stockout_risk_current r
        LEFT JOIN LATERAL (
            SELECT x.action FROM allocation.allocation_decisions x
            WHERE x.sku_id = r.sku_id AND x.retailer_code = r.retailer_code
            ORDER BY x.decided_at DESC LIMIT 1
        ) d ON TRUE
        WHERE (%(scope)s::text IS NULL OR r.retailer_code = %(scope)s::text)
          AND r.risk_tier = ANY(%(tiers)s)
        ORDER BY r.severity_score DESC, r.sku_id
        LIMIT %(limit)s
        """,
        {"scope": scope, "tiers": tiers or ["__none__"], "limit": limit},
    )


def risk_by_family(scope: str | None) -> list[dict[str, Any]]:
    return _pg_rows(
        """
        SELECT license_family, SUM(revenue_at_risk_usd)::float AS revenue_at_risk,
               SUM(units_at_risk)::float AS units_at_risk, COUNT(*) AS rows
        FROM public.stockout_risk_current
        WHERE (%(scope)s::text IS NULL OR retailer_code = %(scope)s::text)
        GROUP BY license_family ORDER BY revenue_at_risk DESC
        """,
        {"scope": scope},
    )


def risk_totals(scope: str | None) -> dict[str, Any]:
    rows = _pg_rows(
        """
        SELECT COUNT(*) FILTER (WHERE risk_tier = 'CRITICAL') AS critical_rows,
               COUNT(*) AS at_risk_rows,
               COALESCE(SUM(revenue_at_risk_usd), 0)::float AS revenue_at_risk,
               COALESCE(SUM(units_at_risk), 0)::float AS units_at_risk
        FROM public.stockout_risk_current
        WHERE (%(scope)s::text IS NULL OR retailer_code = %(scope)s::text)
        """,
        {"scope": scope},
    )
    return rows[0]


def sku_risk(sku: str, retailer: str) -> dict[str, Any] | None:
    rows = _pg_rows(
        "SELECT * FROM public.stockout_risk_current WHERE sku_id = %s AND retailer_code = %s",
        (sku, retailer),
    )
    return rows[0] if rows else None


def sku_forecast(sku: str, retailer: str) -> list[dict[str, Any]]:
    return _pg_rows(
        """
        SELECT horizon, week_end_date, forecast_p50, forecast_p90, model_version, algorithm
        FROM public.forecast_next_13w WHERE sku_id = %s AND retailer_code = %s
        ORDER BY horizon
        """,
        (sku, retailer),
    )


def sku_decisions(sku: str, retailer: str, limit: int = 8) -> list[dict[str, Any]]:
    return _pg_rows(
        """
        SELECT decision_id, action, recommended_units, planner_units, override_delta_units,
               planner_id, rationale_text, agent_trace_id, decided_at
        FROM allocation.allocation_decisions
        WHERE sku_id = %s AND retailer_code = %s ORDER BY decided_at DESC LIMIT %s
        """,
        (sku, retailer, limit),
    )


def decision_stats() -> dict[str, Any]:
    rows = _pg_rows(
        """
        SELECT COUNT(*) AS n,
               COUNT(*) FILTER (WHERE action = 'accept') AS accepted,
               COUNT(*) FILTER (WHERE action = 'override') AS overridden,
               COUNT(*) FILTER (WHERE action = 'defer') AS deferred
        FROM allocation.allocation_decisions
        """
    )
    return rows[0]


def save_decision(*, sku: str, retailer: str, week_end: str, recommended: int, planner_units: int,
                  action: str, planner_id: str, rationale: str | None, trace_id: str | None,
                  model_version: int | None, severity: float | None, units_at_risk: int | None,
                  revenue_at_risk: float | None) -> int:
    with pg() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO allocation.allocation_decisions
              (sku_id, retailer_code, week_end_date, recommended_units, planner_units, action,
               planner_id, rationale_text, agent_trace_id, model_version, severity_score,
               units_at_risk, revenue_at_risk_usd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING decision_id
            """,
            (sku, retailer, week_end, recommended, planner_units, action, planner_id,
             rationale or None, trace_id, model_version, severity, units_at_risk, revenue_at_risk),
        )
        new_id = cur.fetchone()["decision_id"]
        conn.commit()
        return int(new_id)


def save_interaction(*, sku: str | None, retailer: str | None, planner_id: str, question: str,
                     answer: str, tools: list[str], trace_id: str | None, llm_endpoint: str | None,
                     prompt: str | None, in_tokens: int | None, out_tokens: int | None,
                     latency_ms: int) -> None:
    with pg() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO allocation.agent_interactions
              (sku_id, retailer_code, planner_id, question, answer, tools_called, trace_id,
               llm_endpoint, prompt_provenance, input_tokens, output_tokens, latency_ms)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (sku, retailer, planner_id, question, answer, tools, trace_id, llm_endpoint,
             (prompt or "")[:200], in_tokens, out_tokens, latency_ms),
        )
        conn.commit()


# --------------------------------------------------------------------------
# SQL warehouse
# --------------------------------------------------------------------------

def _wh(query: str, params: dict[str, str] | None = None) -> tuple[list[str], list[list[Any]]]:
    plist = [StatementParameterListItem(name=k, value=v, type="STRING")
             for k, v in (params or {}).items()] or None
    r = w().statement_execution.execute_statement(
        statement=query, warehouse_id=S.WAREHOUSE_ID, wait_timeout="50s", parameters=plist)
    while r.status and r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1.5)
        r = w().statement_execution.get_statement(r.statement_id)
    if not r.status or r.status.state != StatementState.SUCCEEDED:
        err = r.status.error.message if r.status and r.status.error else "unknown error"
        raise RuntimeError(f"warehouse query failed: {err[:300]}")
    cols = [c.name for c in (r.manifest.schema.columns or [])]
    return cols, [list(x) for x in (r.result.data_array or [])] if r.result else []


_kpi_cache: dict[str | None, tuple[float, dict[str, Any]]] = {}


def kpis(scope: str | None, ttl: float = 600) -> dict[str, Any]:
    """Governed KPIs straight from the metric views, cached 10 min.

    The warehouse may be cold; the cache means a page load pays that at most once.
    """
    hit = _kpi_cache.get(scope)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    g = f"{S.CATALOG}.gold"
    flt = "WHERE Retailer = :r" if scope else ""
    flt_acc = "WHERE Retailer = :r" if scope else ""
    p = {"r": scope} if scope else None
    out: dict[str, Any] = {}
    _, a = _wh(f"SELECT ROUND(MEASURE(`Fill Rate Pct`), 2) FROM {g}.mv_fill_rate {flt}", p)
    out["fill_rate"] = float(a[0][0]) if a and a[0][0] is not None else None
    _, a = _wh(f"SELECT ROUND(MEASURE(`OTIF Pct`), 2) FROM {g}.mv_otif {flt}", p)
    out["otif"] = float(a[0][0]) if a and a[0][0] is not None else None
    _, a = _wh(
        f"SELECT ROUND(MEASURE(`WAPE Pct`), 1), ROUND(MEASURE(`P90 Coverage Pct`), 1) "
        f"FROM {g}.mv_forecast_accuracy {flt_acc}", p)
    out["wape"] = float(a[0][0]) if a and a[0][0] is not None else None
    out["p90_cov"] = float(a[0][1]) if a and a[0][1] is not None else None
    # the honest number: the same two metrics on the licence family that matters most
    flt_t = "WHERE `License Family` = 'SOCIAL_TREND'" + (" AND Retailer = :r" if scope else "")
    _, a = _wh(
        f"SELECT ROUND(MEASURE(`WAPE Pct`), 1), ROUND(MEASURE(`P90 Coverage Pct`), 1) "
        f"FROM {g}.mv_forecast_accuracy {flt_t}", p)
    out["wape_trend"] = float(a[0][0]) if a and a[0][0] is not None else None
    out["p90_cov_trend"] = float(a[0][1]) if a and a[0][1] is not None else None
    _kpi_cache[scope] = (time.time(), out)
    return out


def feature_row(sku: str, retailer: str) -> dict[str, Any] | None:
    """The one input row the real-time forecaster needs, from the governed feature table."""
    cols, rows = _wh(
        f"SELECT * FROM {S.FEATURE_TABLE} WHERE sku_id = :s AND retailer_code = :r "
        f"ORDER BY week_end_date DESC LIMIT 1", {"s": sku, "r": retailer})
    if not rows:
        return None
    row = dict(zip(cols, rows[0], strict=True))
    keep_str = {"sku_id", "retailer_code", "week_end_date", *S.CATEGORICAL}
    return {k: (v if k in keep_str or v is None else float(v)) for k, v in row.items()}


# --------------------------------------------------------------------------
# Model Serving
# --------------------------------------------------------------------------

def realtime_forecast(sku: str, retailer: str) -> dict[str, Any]:
    """Re-forecast the 13 weeks live on the real-time endpoint.

    One input row is read from the feature table and expanded to horizons 1-13 in a
    single request, so the call is one hop for data and one for inference.
    """
    row = feature_row(sku, retailer)
    if row is None:
        raise LookupError(f"no feature row for {sku} / {retailer}")
    origin = str(row.pop("week_end_date"))
    records = [{**row, "origin_week": origin, "horizon": h} for h in range(1, S.HORIZON + 1)]
    t0 = time.perf_counter()
    out = w().api_client.do(
        "POST", f"/serving-endpoints/{S.FORECASTER_ENDPOINT}/invocations",
        body={"dataframe_records": records})
    ms = (time.perf_counter() - t0) * 1000
    preds = out["predictions"]
    return {"origin": origin, "latency_ms": round(ms), "endpoint": S.FORECASTER_ENDPOINT,
            "predictions": [{"horizon": h + 1, **p} for h, p in enumerate(preds)]}


def ask_agent(question: str) -> dict[str, Any]:
    """Call the live allocation agent; return answer, tool trail and cost.

    The Responses contract wants ``{"input": [...]}`` on the raw invocations route;
    ``WorkspaceClient.query`` would wrap it into the legacy ``inputs`` format.
    """
    t0 = time.perf_counter()
    raw = w().api_client.do(
        "POST", f"/serving-endpoints/{S.AGENT_ENDPOINT}/invocations",
        body={"input": [{"role": "user", "content": question}]})
    ms = round((time.perf_counter() - t0) * 1000)
    answer, calls, outputs, tool_errors = "", {}, [], []
    for item in raw.get("output", []):
        t = item.get("type")
        if t == "function_call":
            calls[item["call_id"]] = item["name"]
        elif t == "function_call_output":
            outputs.append((calls.get(item["call_id"]), item.get("output")))
            # A tool can fail while the model still writes a fluent answer around the
            # failure. Surface it: the prose is not evidence that data was read.
            body = item.get("output") or ""
            if body.lstrip().startswith("{") and '"error"' in body[:200]:
                tool_errors.append(f"{calls.get(item['call_id'], '?')}: {body[:240]}")
        elif t == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    answer = c.get("text") or answer
    co = raw.get("custom_outputs") or {}
    usage = co.get("token_usage") or {}
    alloc = None
    for name, out in outputs:
        if name == "recommend_allocation":
            try:
                alloc = json.loads(out)
            except (TypeError, ValueError):
                alloc = None
    return {
        "answer": answer,
        "tools": co.get("tools_called") or [n for n, _ in outputs],
        "request_id": (raw.get("databricks_output") or {}).get("databricks_request_id")
        or raw.get("id"),
        "llm_endpoint": co.get("llm_endpoint"),
        "prompt": co.get("prompt"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "latency_ms": ms,
        "allocation": alloc,
        "tool_errors": tool_errors,
    }


def recommended_units(allocation: dict | None, retailer: str) -> int | None:
    """This retailer's units from the allocator's own tool output."""
    if not allocation:
        return None
    for c in allocation.get("claims") or []:
        if c.get("retailer_code") == retailer:
            return int(c.get("units_allocated") or 0)
    return None


# --------------------------------------------------------------------------
# Genie
# --------------------------------------------------------------------------

def ask_genie(question: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    msg = w().genie.start_conversation_and_wait(S.GENIE_SPACE_ID, question)
    out: dict[str, Any] = {"sql": None, "text": "", "columns": [], "rows": [], "description": None,
                           "seconds": 0.0}
    for a in msg.attachments or []:
        if a.query is not None:
            out["sql"], out["description"] = a.query.query, a.query.description
            res = w().genie.get_message_attachment_query_result(
                S.GENIE_SPACE_ID, msg.conversation_id, msg.message_id, a.attachment_id)
            sr = res.statement_response
            out["columns"] = [c.name for c in (sr.manifest.schema.columns or [])]
            out["rows"] = [list(r) for r in (sr.result.data_array or [])][:50] if sr.result else []
        if a.text is not None and a.text.content:
            out["text"] = a.text.content
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


_SAFE = re.compile(r"[^A-Za-z0-9_\-]")


def clean_id(s: str | None) -> str:
    """SKU / retailer ids go into an agent prompt; keep them to the id alphabet."""
    return _SAFE.sub("", s or "")
