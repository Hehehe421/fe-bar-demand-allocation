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

import csv
import json
import logging
import pathlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import settings as S
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from psycopg.rows import dict_row

log = logging.getLogger("acc")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [acc] %(message)s")

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


def worklist(scope: str | None, tiers: list[str], limit: int = 250,
             family: str | None = None, brand: str | None = None) -> list[dict[str, Any]]:
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
          AND (%(family)s::text IS NULL OR r.license_family = %(family)s::text)
          AND (%(brand)s::text IS NULL OR r.brand = %(brand)s::text)
        ORDER BY r.severity_score DESC, r.sku_id
        LIMIT %(limit)s
        """,
        {"scope": scope, "tiers": tiers or ["__none__"], "limit": limit, "family": family,
         "brand": brand},
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
        SELECT horizon, origin_week, week_end_date, forecast_p50, forecast_p90, model_version, algorithm
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
        log.info("lakebase INSERT allocation.allocation_decisions decision_id=%s %s/%s action=%s "
                 "planner_units=%s recommended=%s trace_id=%s", new_id, sku, retailer, action,
                 planner_units, recommended, trace_id)
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
        log.info("lakebase INSERT allocation.agent_interactions trace_id=%s tools=%s", trace_id, tools)


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
    log.info("serving endpoint %s answered in %d ms (request id %s)", S.AGENT_ENDPOINT, ms,
             (raw.get("databricks_output") or {}).get("databricks_request_id") or raw.get("id"))
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
    log.info("genie space %s answered in %.1fs, %d rows, sql=%s", S.GENIE_SPACE_ID, out["seconds"],
             len(out["rows"]), bool(out["sql"]))
    return out


_SAFE = re.compile(r"[^A-Za-z0-9_\-]")


def clean_id(s: str | None) -> str:
    """SKU / retailer ids go into an agent prompt; keep them to the id alphabet."""
    return _SAFE.sub("", s or "")


# --------------------------------------------------------------------------
# Executive and performance views
# --------------------------------------------------------------------------

_SCOPE = "(%(scope)s::text IS NULL OR r.retailer_code = %(scope)s::text)"


def exec_data(scope: str | None) -> dict[str, Any]:
    """Everything the executive tab needs, from Lakebase. Queries run concurrently."""
    sc = {"scope": scope}
    q = {
        "tiers": (f"""SELECT r.risk_tier, COUNT(*) AS n, COALESCE(SUM(r.revenue_at_risk_usd),0)::float AS rev,
                   COALESCE(SUM(r.units_at_risk),0)::float AS units
            FROM public.stockout_risk_current r WHERE {_SCOPE} GROUP BY 1""", sc),
        "families": (f"""SELECT r.license_family, COUNT(*) AS n, COALESCE(SUM(r.revenue_at_risk_usd),0)::float AS rev,
                   COUNT(*) FILTER (WHERE r.risk_tier = 'CRITICAL') AS critical
            FROM public.stockout_risk_current r WHERE {_SCOPE} GROUP BY 1 ORDER BY rev DESC""", sc),
        "accounts": (f"""SELECT r.retailer_code, COUNT(*) AS n, COALESCE(SUM(r.revenue_at_risk_usd),0)::float AS rev,
                   COUNT(*) FILTER (WHERE r.risk_tier = 'CRITICAL') AS critical
            FROM public.stockout_risk_current r WHERE {_SCOPE} GROUP BY 1 ORDER BY rev DESC""", sc),
        "coverage": (f"""SELECT COUNT(*) FILTER (WHERE r.risk_tier = 'CRITICAL') AS critical,
                   COUNT(*) FILTER (WHERE r.risk_tier = 'CRITICAL' AND r.weeks_to_stockout <= 2) AS soon,
                   COUNT(*) FILTER (WHERE r.risk_tier = 'CRITICAL' AND EXISTS (
                       SELECT 1 FROM allocation.allocation_decisions d
                       WHERE d.sku_id = r.sku_id AND d.retailer_code = r.retailer_code)) AS decided,
                   MIN(r.projected_stockout_week) AS first_stockout
            FROM public.stockout_risk_current r WHERE {_SCOPE}""", sc),
        "top": (f"""SELECT r.sku_id, r.retailer_code, r.brand, r.license_family, r.lifecycle_stage, r.risk_tier,
                   r.revenue_at_risk_usd::float AS rev, r.units_at_risk::float AS units,
                   r.weeks_to_stockout, r.projected_stockout_week, d.action AS last_action
            FROM public.stockout_risk_current r
            LEFT JOIN LATERAL (SELECT x.action FROM allocation.allocation_decisions x
                               WHERE x.sku_id = r.sku_id AND x.retailer_code = r.retailer_code
                               ORDER BY x.decided_at DESC LIMIT 1) d ON TRUE
            WHERE {_SCOPE} ORDER BY r.revenue_at_risk_usd DESC LIMIT 5""", sc),
        "forecast": ("SELECT MIN(origin_week) AS origin, MAX(model_version) AS model_version, "
                     "MAX(algorithm) AS algorithm FROM public.forecast_next_13w", ()),
    }
    with ThreadPoolExecutor(max_workers=len(q)) as ex:
        futs = {k: ex.submit(_pg_rows, sql_, params) for k, (sql_, params) in q.items()}
        out = {k: f.result() for k, f in futs.items()}
    out["coverage"] = out["coverage"][0]
    out["forecast"] = out["forecast"][0]
    return out


def decision_stats_full() -> dict[str, Any]:
    st = _pg_rows(
        """SELECT COUNT(*) AS n,
                  COUNT(*) FILTER (WHERE action = 'accept') AS accepted,
                  COUNT(*) FILTER (WHERE action = 'override') AS overridden,
                  COUNT(*) FILTER (WHERE action = 'defer') AS deferred,
                  COALESCE(SUM(override_delta_units) FILTER (WHERE action = 'override'), 0) AS net_units,
                  COALESCE(SUM(ABS(override_delta_units)) FILTER (WHERE action = 'override'), 0) AS gross_units
           FROM allocation.allocation_decisions""")[0]
    return st


def decision_log(limit: int = 12) -> list[dict[str, Any]]:
    return _pg_rows(
        """SELECT decision_id, sku_id, retailer_code, action, recommended_units, planner_units,
                  override_delta_units, planner_id, left(coalesce(rationale_text, ''), 90) AS why,
                  decided_at
           FROM allocation.allocation_decisions ORDER BY decided_at DESC, decision_id DESC LIMIT %s""",
        (limit,))


_ttl_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _ttl_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _ttl_cache[key] = (time.time(), val)
    return val


def performance(scope: str | None, ttl: float = 900) -> dict[str, Any]:
    """Trend, accuracy by family and fill rate vs commitment, from the metric views."""
    def run() -> dict[str, Any]:
        g = f"{S.CATALOG}.gold"
        flt = "WHERE Retailer = :r" if scope else ""
        p = {"r": scope} if scope else None
        _, fill = _wh(f"SELECT Week, MEASURE(`Fill Rate Pct`) FROM {g}.mv_fill_rate {flt} "
                      f"GROUP BY Week ORDER BY Week", p)
        _, otif = _wh(f"SELECT Week, MEASURE(`OTIF Pct`) FROM {g}.mv_otif {flt} "
                      f"GROUP BY Week ORDER BY Week", p)
        fam_cols, fam = _wh(
            f"SELECT `License Family`, MEASURE(`WAPE Pct`), MEASURE(`Bias Pct`), "
            f"MEASURE(`P90 Coverage Pct`), MEASURE(`Actual Units`) FROM {g}.mv_forecast_accuracy "
            f"{flt} GROUP BY ALL ORDER BY 2", p)
        _, acct = _wh(f"SELECT Retailer, MEASURE(`Fill Rate Pct`) FROM {g}.mv_fill_rate "
                      f"GROUP BY Retailer ORDER BY Retailer")
        _, commit = _wh(f"SELECT DISTINCT retailer_code, contractual_fill_commitment "
                        f"FROM {g}.dim_retailer_dc")
        commit_map = {r[0]: float(r[1]) * 100 for r in commit}
        return {
            "weeks": [r[0] for r in fill][-52:],
            "fill": [float(r[1]) for r in fill][-52:],
            "otif": [float(r[1]) for r in otif][-52:],
            "families": [{"family": r[0], "wape": float(r[1]), "bias": float(r[2]),
                          "p90_cov": float(r[3]), "units": float(r[4])} for r in fam],
            "accounts": [{"account": r[0], "fill": float(r[1]), "commit": commit_map.get(r[0])}
                         for r in acct],
        }
    return _cached(f"perf:{scope}", ttl, run)


def dc_exposure(scope: str | None, ttl: float = 900) -> list[dict[str, Any]]:
    """At-risk exposure attributed to distribution centres.

    Each at-risk SKU x account row is spread over the DCs that served that SKU x account in
    the last 13 weeks, in proportion to unconstrained demand. So the map answers "where is
    the at-risk demand physically served from", using the demand fact's own DC grain.
    """
    def run() -> list[dict[str, Any]]:
        g = f"{S.CATALOG}.gold"
        flt = "WHERE r.retailer_code = :r" if scope else ""
        p = {"r": scope} if scope else None
        _, rows = _wh(f"""
            WITH last AS (SELECT MAX(week_end_date) AS m FROM {g}.fct_demand_weekly),
            share AS (SELECT sku_id, retailer_code, dc_code, SUM(true_demand_units) AS u
                      FROM {g}.fct_demand_weekly, last
                      WHERE week_end_date > date_sub(last.m, 91) GROUP BY 1, 2, 3),
            tot AS (SELECT sku_id, retailer_code, SUM(u) AS t FROM share GROUP BY 1, 2)
            SELECT s.dc_code,
                   SUM(r.revenue_at_risk_usd * s.u / NULLIF(t.t, 0)) AS revenue_at_risk,
                   SUM(r.units_at_risk * s.u / NULLIF(t.t, 0)) AS units_at_risk,
                   SUM(CASE WHEN r.risk_tier = 'CRITICAL' THEN s.u / NULLIF(t.t, 0) ELSE 0 END) AS critical,
                   COUNT(DISTINCT r.sku_id) AS skus
            FROM {g}.fct_stockout_risk r
            JOIN share s ON s.sku_id = r.sku_id AND s.retailer_code = r.retailer_code
            JOIN tot t ON t.sku_id = r.sku_id AND t.retailer_code = r.retailer_code
            {flt} GROUP BY s.dc_code ORDER BY revenue_at_risk DESC""", p)
        out = []
        for dc, rev, units, crit, skus in rows:
            geo = S.DC_GEO.get(dc, {})
            out.append({"dc": dc, "label": geo.get("label", dc), "city": geo.get("city", ""),
                        "lat": geo.get("lat"), "lon": geo.get("lon"),
                        "revenue": float(rev or 0), "units": float(units or 0),
                        "critical": float(crit or 0), "skus": int(skus or 0)})
        return out
    return _cached(f"dc:{scope}", ttl, run)


_info_cache: dict[str, dict[str, Any]] = {}


def sku_info(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Catalog attributes for artwork and captions. Cached for the process lifetime.

    Read from the Lakebase ``product_lookup`` table (a masked-column-free projection of
    ``dim_product``); the warehouse is only the fallback if Lakebase has no such row.
    """
    need = [s for s in dict.fromkeys(skus) if s and s not in _info_cache]
    if need:
        ids = [clean_id(s) for s in need]
        try:
            for r in _pg_rows(
                    "SELECT sku_id, product_name, brand, category, license_family, lifecycle_stage "
                    "FROM public.product_lookup WHERE sku_id = ANY(%s)", (ids,)):
                _info_cache[r["sku_id"]] = r
        except Exception:  # noqa: BLE001
            pass
        missing = [i for i in ids if i not in _info_cache]
        if missing:
            lst = ", ".join("'" + i + "'" for i in missing)
            cols, rows = _wh(
                f"SELECT sku_id, product_name, brand, category, license_family, lifecycle_stage "
                f"FROM {S.CATALOG}.gold.dim_product WHERE sku_id IN ({lst})")
            for r in rows:
                _info_cache[r[0]] = dict(zip(cols, r, strict=True))
    return {s: _info_cache[s] for s in skus if s in _info_cache}


def brands() -> list[str]:
    """Brands for the worklist filter, from the Lakebase product lookup."""
    def run() -> list[str]:
        return [r["brand"] for r in _pg_rows(
            "SELECT DISTINCT brand FROM public.product_lookup WHERE brand IS NOT NULL ORDER BY 1")]
    return _cached("brands", 3600, run)


def kpi_strip(scope: str | None) -> dict[str, Any]:
    """Header KPIs from Lakebase: fill rate, OTIF, chargebacks (latest PO week vs the one before)
    and units / revenue at risk now. Ratios are ratios of sums, never averages of percentages.
    """
    weeks = _pg_rows(
        """SELECT week_end_date, SUM(requested_units)::float AS req, SUM(shipped_units)::float AS shp,
                  SUM(po_lines)::float AS po, SUM(otif_lines)::float AS otif,
                  SUM(chargeback_usd)::float AS cb
           FROM public.fill_rate_summary
           WHERE (%(scope)s::text IS NULL OR retailer_code = %(scope)s::text)
           GROUP BY week_end_date ORDER BY week_end_date DESC LIMIT 2""", {"scope": scope})
    tot = risk_totals(scope)

    def at(i: int) -> dict[str, Any] | None:
        if i >= len(weeks):
            return None
        x = weeks[i]
        return {"week": x["week_end_date"], "fill": 100 * x["shp"] / x["req"] if x["req"] else None,
                "otif": 100 * x["otif"] / x["po"] if x["po"] else None, "chargeback": x["cb"]}
    return {"now": at(0), "prior": at(1), "units_at_risk": float(tot["units_at_risk"]),
            "revenue_at_risk": float(tot["revenue_at_risk"]), "critical": int(tot["critical_rows"])}


def wape_wow(scope: str | None) -> dict[str, Any]:
    """Latest holdout week's WAPE against the week before (warehouse, cached 15 minutes)."""
    def run() -> dict[str, Any]:
        flt = "WHERE retailer_code = :r" if scope else ""
        _, rows = _wh(
            f"SELECT week_end_date, 100 * SUM(abs_error) / NULLIF(SUM(actual_units), 0) "
            f"FROM {S.CATALOG}.gold.fct_demand_forecast_eval {flt} "
            f"GROUP BY week_end_date ORDER BY week_end_date DESC LIMIT 2", {"r": scope} if scope else None)
        vals = [float(r[1]) for r in rows if r[1] is not None]
        return {"now": vals[0] if vals else None, "prior": vals[1] if len(vals) > 1 else None}
    return _cached(f"wape_wow:{scope}", 900, run)


def sku_history(sku: str, retailer: str, weeks: int = 26) -> dict[str, Any]:
    """Actual weekly units for one series, plus its holdout-backtest WAPE (warehouse, cached)."""
    def run() -> dict[str, Any]:
        g = f"{S.CATALOG}.gold"
        p = {"s": clean_id(sku), "r": clean_id(retailer)}
        _, act = _wh(
            f"SELECT week_end_date, SUM(pos_units), SUM(true_demand_units) FROM {g}.fct_demand_weekly "
            f"WHERE sku_id = :s AND retailer_code = :r GROUP BY week_end_date "
            f"ORDER BY week_end_date DESC LIMIT {int(weeks)}", p)
        _, bt = _wh(
            f"SELECT 100 * SUM(abs_error) / NULLIF(SUM(actual_units), 0), COUNT(*) "
            f"FROM {g}.fct_demand_forecast_eval WHERE sku_id = :s AND retailer_code = :r", p)
        act.reverse()
        return {"weeks": [str(r[0]) for r in act], "pos": [float(r[1]) for r in act],
                "demand": [float(r[2]) for r in act],
                "backtest_wape": float(bt[0][0]) if bt and bt[0][0] is not None else None,
                "backtest_rows": int(bt[0][1]) if bt else 0}
    return _cached(f"hist:{sku}:{retailer}", 900, run)


def sku_inbound(sku: str, after: str, limit: int = 8) -> list[dict[str, Any]]:
    """Open inbound shipments for the SKU (revised ETA after the forecast origin), soonest first."""
    def run() -> list[dict[str, Any]]:
        cols, rows = _wh(
            f"SELECT asn_id, dc_code, shipped_units, original_eta_date, revised_eta_date, eta_slip_days, "
            f"transport_mode FROM {S.CATALOG}.gold.fct_inbound_eta "
            f"WHERE sku_id = :s AND revised_eta_date > CAST(:a AS DATE) "
            f"ORDER BY revised_eta_date, asn_id LIMIT {int(limit)}", {"s": clean_id(sku), "a": after})
        return [dict(zip(cols, r, strict=True)) for r in rows]
    return _cached(f"inb:{sku}:{after}", 900, run)


def baseline() -> dict[str, dict[str, Any]]:
    """The Stage B business baseline (a dated snapshot, shipped with the app)."""
    here = pathlib.Path(__file__).resolve().parent
    for path in (here / "data" / "kpi_baseline.csv", here.parent / "evidence/metrics/kpi_baseline.csv"):
        if path.exists():
            with path.open() as fh:
                return {r["kpi"]: r for r in csv.DictReader(fh)}
    return {}


def service_status() -> dict[str, dict[str, str]]:
    """Health of the pieces the app depends on, cached briefly. Never raises."""
    def run() -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        t0 = time.perf_counter()
        try:
            _pg_rows("SELECT 1 AS ok")
            out["Lakebase"] = {"state": "ok", "note": f"{(time.perf_counter() - t0) * 1000:.0f} ms"}
        except Exception as e:  # noqa: BLE001
            out["Lakebase"] = {"state": "down", "note": str(e)[:80]}
        try:
            wh = w().warehouses.get(S.WAREHOUSE_ID)
            st = str(wh.state.value if wh.state else "?")
            out["Warehouse"] = {"state": "ok" if st == "RUNNING" else "idle", "note": st.lower()}
        except Exception as e:  # noqa: BLE001
            out["Warehouse"] = {"state": "down", "note": str(e)[:80]}
        for label, name in (("Agent", S.AGENT_ENDPOINT), ("Forecaster", S.FORECASTER_ENDPOINT)):
            try:
                ep = w().serving_endpoints.get(name)
                ready = str(ep.state.ready.value if ep.state and ep.state.ready else "?")
                msg = ""
                ents = (ep.config.served_entities if ep.config else None) or []
                if ents and ents[0].state and ents[0].state.deployment_state_message:
                    msg = ents[0].state.deployment_state_message
                scaled = "scaled to zero" in msg.lower()
                out[label] = {"state": "idle" if scaled else ("ok" if ready == "READY" else "down"),
                              "note": "scaled to zero" if scaled else ready.lower()}
            except Exception as e:  # noqa: BLE001
                out[label] = {"state": "down", "note": str(e)[:80]}
        return out
    return _cached("status", 45, run)


def clear_caches() -> None:
    """Drop cached warehouse results (the Refresh button)."""
    _ttl_cache.clear()
    _kpi_cache.clear()
