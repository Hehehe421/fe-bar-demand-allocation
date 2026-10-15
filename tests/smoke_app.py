"""Smoke test for the Allocation Command Center backend, against the live workspace.

    python tests/smoke_app.py            # everything, including the slow endpoint calls
    python tests/smoke_app.py --fast     # skip agent / real-time / Genie

What it proves, in the order a demo depends on it:

  1. warm-up: wake the scale-to-zero endpoints first, so nothing cold-starts mid-demo
     (the forecaster took 138 s to answer after idling)
  2. Lakebase reads (worklist, forecast) and the governed KPI metric views
  3. the write path: an INSERT into allocation.allocation_decisions that is ROLLED
     BACK, so the test leaves no rows behind (a stray row would also change the
     answers Genie gives about decisions)
  4. real-time forecast == batch forecast (the two serving paths agree)
  5. the agent's TOOLS returned data. A fluent answer is not evidence: an endpoint
     whose every tool failed with a permission error still produced prose.
  6. Genie answers a question with SQL

Exit code 0 only if every check passed.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
# Pin the profile, do not setdefault: a DATABRICKS_CONFIG_PROFILE already exported in the
# shell (often the broken DEFAULT one) would otherwise win and every check fails with
# "refresh token is invalid" for a reason unrelated to the app.
os.environ["DATABRICKS_CONFIG_PROFILE"] = os.environ.get("DBX_PROFILE", "vm-summer-classic")

import backend as b  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
    dt = time.perf_counter() - t0
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:34} {dt:7.1f}s  {detail}", flush=True)
    return ok


def main() -> int:
    fast = "--fast" in sys.argv
    top: dict = {}

    # 1. warm-up --------------------------------------------------------------
    if not fast:
        def warm():
            row = b.worklist(None, ["CRITICAL"], limit=1)[0]
            top.update(row)
            rt = b.realtime_forecast(row["sku_id"], row["retailer_code"])
            return True, f"forecaster answered in {rt['latency_ms'] / 1000:.1f}s"
        check("warm-up: real-time forecaster", warm)

    # 2. reads ----------------------------------------------------------------
    def wl():
        rows = b.worklist(None, ["CRITICAL", "HIGH"])
        top.update(rows[0])
        ok = len(rows) > 100 and rows[0]["severity_score"] >= rows[-1]["severity_score"]
        return ok, f"{len(rows)} rows, top {rows[0]['sku_id']}/{rows[0]['retailer_code']}"
    check("lakebase: ranked worklist", wl)

    def scoped():
        rows = b.worklist("MASS_A", ["CRITICAL", "HIGH", "MEDIUM", "LOW"], limit=5000)
        return bool(rows) and {r["retailer_code"] for r in rows} == {"MASS_A"}, f"{len(rows)} rows"
    check("lakebase: account scoping", scoped)

    def fc():
        rows = b.sku_forecast(top["sku_id"], top["retailer_code"])
        return len(rows) == 13, f"{len(rows)} weeks, model v{rows[0]['model_version']}"
    check("lakebase: 13-week forecast", fc)

    def totals():
        t = b.risk_totals(None)
        return t["at_risk_rows"] > 1000, f"{t['at_risk_rows']:,} rows, ${t['revenue_at_risk']:,.0f} at risk"  # noqa: E501
    check("lakebase: totals", totals)

    def kp():
        k = b.kpis(None)
        ok = all(k.get(x) is not None for x in ("fill_rate", "otif", "wape", "p90_cov",
                                                "p90_cov_trend"))
        return ok, (f"fill {k['fill_rate']} otif {k['otif']} wape {k['wape']} "
                    f"p90 {k['p90_cov']} (trend {k['p90_cov_trend']})")
    check("warehouse: governed KPIs", kp)

    # 2a. Stage F items: KPI header, brand filter, product lookup, actuals, inbound ETAs ----
    def strip():
        k = b.kpi_strip(None)
        n, pr = k["now"], k["prior"]
        ok = bool(n and pr) and n["week"] > pr["week"] and 80 < n["fill"] <= 100 and k["revenue_at_risk"] > 1e6  # noqa: E501
        return ok, (f"fill {n['fill']:.2f}% ({n['fill'] - pr['fill']:+.2f} pt vs {pr['week']}), "
                    f"otif {n['otif']:.2f}%, ${k['revenue_at_risk']:,.0f} / {k['units_at_risk']:,.0f} units at risk")  # noqa: E501
    check("lakebase: KPI header + WoW", strip)

    def brand_filter():
        bs = b.brands()
        rows = b.worklist(None, ["CRITICAL", "HIGH"], brand=bs[0], limit=5000)
        return bool(rows) and {r["brand"] for r in rows} == {bs[0]}, f"{len(bs)} brands; {bs[0]}: {len(rows)} rows"  # noqa: E501
    check("lakebase: worklist brand filter", brand_filter)

    def lookup():
        i = b._pg_rows("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE product_name IS NOT NULL) AS named "  # noqa: E501
                       "FROM public.product_lookup")[0]
        cols = {c["column_name"] for c in b._pg_rows(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'product_lookup'")}  # noqa: E501
        masked = cols & {"unit_cost_usd", "gross_margin_pct"}
        return i["n"] == 2400 and not masked, f"{i['n']:,} SKUs, masked columns present: {sorted(masked) or 'none'}"  # noqa: E501
    check("lakebase: product_lookup", lookup)

    def hist():
        h = b.sku_history(top["sku_id"], top["retailer_code"])
        inbound = b.sku_inbound(top["sku_id"], "2026-09-26")
        # a recent launch (this top SKU has 14 weeks) has a short history; it must have some
        return len(h["weeks"]) >= 8 and h["backtest_wape"] is not None, (
            f"{len(h['weeks'])} weeks actuals, backtest WAPE {h['backtest_wape']:.0f}%, "
            f"{len(inbound)} open inbound shipments")
    check("SKU detail: actuals + inbound ETAs", hist)

    # 2b. executive / performance / map data ------------------------------------
    def execd():
        e = b.exec_data(None)
        t_rev = sum(float(t["rev"]) for t in e["tiers"])
        ok = e["coverage"]["critical"] == 434 and t_rev > 1e6 and len(e["top"]) == 5
        return ok, f"{e['coverage']['critical']} critical, ${t_rev:,.0f} at risk, top {e['top'][0]['sku_id']}"  # noqa: E501
    check("executive aggregates", execd)

    def perfd():
        p = b.performance(None)
        ok = len(p["weeks"]) == 52 and len(p["families"]) == 3 and len(p["accounts"]) == 3
        return ok, f"{len(p['weeks'])} weeks, fill {p['fill'][-1]:.1f}% otif {p['otif'][-1]:.1f}%"
    check("performance trends", perfd)

    def dcd():
        dc = b.dc_exposure(None)
        t = sum(float(x["rev"]) for x in b.exec_data(None)["tiers"])
        s_ = sum(d["revenue"] for d in dc)
        # attribution must CONSERVE revenue: spreading rows over DCs may not create or lose any
        return abs(s_ - t) / t < 0.001, f"{len(dc)} DCs sum to ${s_:,.0f} vs total ${t:,.0f}"
    check("DC exposure conserves revenue", dcd)

    def info():
        i = b.sku_info([top["sku_id"]])
        return top["sku_id"] in i and i[top["sku_id"]]["category"] in ("PLUSH", "ACTION_FIGURE"), \
            str(i.get(top["sku_id"], {}).get("product_name"))
    check("catalog attributes for art", info)

    def stat():
        s_ = b.service_status()
        return set(s_) == {"Lakebase", "Warehouse", "Agent", "Forecaster"} and s_["Lakebase"]["state"] == "ok", \
            ", ".join(f"{k}={v['state']}" for k, v in s_.items())  # noqa: E501
    check("service status", stat)

    def bl():
        x = b.baseline()
        return float(x["lost_revenue_usd"]["value"]) > 1e6, f"{len(x)} baseline KPIs"
    check("business baseline snapshot", bl)

    # 3. write path (rolled back) ---------------------------------------------
    def write():
        with b.pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM allocation.allocation_decisions")
                before = cur.fetchone()["n"]
                cur.execute(
                    """INSERT INTO allocation.allocation_decisions
                       (sku_id, retailer_code, week_end_date, recommended_units, planner_units,
                        action, planner_id) VALUES (%s,%s,'2026-10-03',10,12,'override','smoke-test')
                       RETURNING decision_id, override_delta_units""",  # noqa: E501
                    (top["sku_id"], top["retailer_code"]))
                row = cur.fetchone()
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM allocation.allocation_decisions")
                after = cur.fetchone()["n"]
        return (after == before and row["override_delta_units"] == 2,
                f"insert ok (delta {row['override_delta_units']}), rolled back, {after} rows remain")  # noqa: E501
    check("lakebase: decision write (rollback)", write)

    if fast:
        return _finish()

    # 4. real-time == batch ---------------------------------------------------
    def parity():
        rt = b.realtime_forecast(top["sku_id"], top["retailer_code"])
        batch = {r["horizon"]: r for r in b.sku_forecast(top["sku_id"], top["retailer_code"])}
        worst = max(abs(float(p["forecast_p50"]) - float(batch[p["horizon"]]["forecast_p50"]))
                    for p in rt["predictions"])
        return worst < 0.05, f"max |p50 diff| = {worst:.3f} units over 13 weeks"
    check("real-time forecast == batch", parity)

    # 5. agent tools ----------------------------------------------------------
    def agent():
        sku, ret = top["sku_id"], top["retailer_code"]
        r = b.ask_agent(f"{sku} is short at {ret}. Recommend how to split the available "
                        f"supply and name which account absorbs the shortfall.")
        if r["tool_errors"]:
            return False, f"{len(r['tool_errors'])} tool call(s) failed: {r['tool_errors'][0][:160]}"  # noqa: E501
        reco = b.recommended_units(r["allocation"], ret)
        return (reco is not None and bool(r["request_id"]),
                f"tools {r['tools']}, recommends {reco} units for {ret}, "
                f"request {str(r['request_id'])[:8]}")
    check("agent: tools return data", agent)

    # 6. genie ----------------------------------------------------------------
    def genie():
        r = b.ask_genie("Which licence family has the most revenue at risk?")
        return bool(r["sql"]) and bool(r["rows"]), f"{r['rows'][:1]} in {r['seconds']}s"
    check("genie: answers with SQL", genie)

    return _finish()


def _finish() -> int:
    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed" + (f"  FAILED: {bad}" if bad else ""))  # noqa: E501
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
