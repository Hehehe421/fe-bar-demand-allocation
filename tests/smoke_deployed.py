# ruff: noqa: E501  (report lines carry long, human-readable detail strings)
"""Smoke test of the DEPLOYED Allocation Command Center, over HTTP, as the build prompt asks.

    python tests/smoke_deployed.py            # everything, incl. agent call and a committed write
    python tests/smoke_deployed.py --fast     # no agent call, no write

Drives the running app the way a browser does: GET the page, then POST the same
``/_dash-update-component`` requests the page fires, with an OAuth token for the developer's
profile (the workspace SSO blocks a headless browser). Every line prints the HTTP status and the
latency. The write path is proved with **pre and post row counts** on
``allocation.allocation_decisions`` read straight from Lakebase, then the one decision this test
created is deleted by id (so the seeded rows the KPIs and Genie report on are unchanged).

Exit code 0 only if every check passed.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
os.environ["DATABRICKS_CONFIG_PROFILE"] = os.environ.get("DBX_PROFILE", "vm-summer-classic")

import requests  # noqa: E402

import src.config as config  # noqa: E402

PROFILE = os.environ["DATABRICKS_CONFIG_PROFILE"]
SEL = {"sku": "SKU-01723", "retailer": "MASS_A"}
results: list[tuple[str, bool]] = []


def token() -> str:
    out = subprocess.run(["databricks", "auth", "token", "--profile", PROFILE, "-o", "json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["access_token"]


def app_url() -> str:
    import backend as b
    return b.w().apps.get(config.APP_NAME).url.rstrip("/")


def text_of(node: Any, acc: list[str] | None = None) -> list[str]:
    acc = [] if acc is None else acc
    if isinstance(node, dict):
        for v in ([node["props"].get("children")] if "props" in node else node.values()):
            text_of(v, acc)
    elif isinstance(node, list):
        for n in node:
            text_of(n, acc)
    elif isinstance(node, str):
        acc.append(node)
    return acc


class Client:
    def __init__(self, url: str, tok: str) -> None:
        self.url, self.h = url, {"Authorization": f"Bearer {tok}"}

    def get(self, path: str) -> tuple[int, float, str]:
        t0 = time.time()
        r = requests.get(self.url + path, headers=self.h, timeout=120)
        return r.status_code, time.time() - t0, r.text

    def callback(self, outputs: list[tuple[str, str]], inputs: list[tuple[str, str, Any]],
                 states: list[tuple[str, str, Any]] | None = None,
                 changed: str | None = None,
                 raw_inputs: list[Any] | None = None) -> tuple[int, float, dict[str, Any]]:
        names = [f"{i}.{p}" for i, p in outputs]
        body = {
            "output": names[0] if len(names) == 1 else ".." + "...".join(names) + "..",
            "outputs": [{"id": i, "property": p} for i, p in outputs] if len(outputs) > 1
            else {"id": outputs[0][0], "property": outputs[0][1]},
            "inputs": raw_inputs if raw_inputs is not None
            else [{"id": i, "property": p, "value": v} for i, p, v in inputs],
            "state": [{"id": i, "property": p, "value": v} for i, p, v in (states or [])],
            "changedPropIds": [changed or f"{inputs[0][0]}.{inputs[0][1]}"],
        }
        t0 = time.time()
        r = requests.post(self.url + "/_dash-update-component", json=body, timeout=420,
                          headers={**self.h, "Content-Type": "application/json"})
        return r.status_code, time.time() - t0, (r.json().get("response", {}) if r.ok else {"error": r.text[:300]})


def report(name: str, ok: bool, status: int | str, secs: float, detail: str) -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  HTTP {status}  {secs:6.1f}s  {name:32} {detail}", flush=True)


def main() -> int:
    fast = "--fast" in sys.argv
    import backend as b

    url = app_url()
    c = Client(url, token())
    print(f"app {url}\nprofile {PROFILE}\n")

    st, dt, body = c.get("/healthz")
    # the Apps proxy answers /healthz itself (empty 200), so the in-app route is shadowed there
    report("GET /healthz", st == 200, st, dt, f"{len(body)}-byte body (answered by the platform proxy)")
    st, dt, body = c.get("/")
    report("GET / (dash page)", st == 200 and "_dash-renderer" in body, st, dt, f"{len(body):,} bytes")

    st, dt, r = c.callback([("kpi-strip", "children")], [("scope", "value", "ALL"), ("refresh", "n_clicks", None)])
    txt = " | ".join(text_of(r.get("kpi-strip", {}).get("children")))
    report("header KPIs (Lakebase)", st == 200 and "Fill rate" in txt and "vs prior week" in txt, st, dt, txt[:150])

    st, dt, r = c.callback(
        [("worklist", "data"), ("worklist", "selected_rows"), ("worklist-note", "children")],
        [("scope", "value", "ALL"), ("tiers", "value", ["CRITICAL", "HIGH"]), ("fam", "value", "ALL"),
         ("brand", "value", "Snugglewick"), ("saved", "data", 0)], changed="brand.value")
    rows = r.get("worklist", {}).get("data", [])
    report("worklist, brand filter", st == 200 and bool(rows) and {x["brand"] for x in rows} == {"Snugglewick"},
           st, dt, f"{len(rows)} rows, all Snugglewick")

    st, dt, r = c.callback(
        [("detail-head", "children"), ("forecast-graph", "figure"), ("forecast-note", "children"),
         ("dec-history", "children"), ("inbound-out", "children")],
        [("sel", "data", SEL), ("saved", "data", 0)])
    fig = r.get("forecast-graph", {}).get("figure", {})
    names = [t.get("name") for t in fig.get("data", [])]
    inbound = " | ".join(text_of(r.get("inbound-out", {}).get("children")))
    report("SKU detail: actuals, ETAs", st == 200 and "Actual demand" in names and "Arrives" in inbound, st, dt,
           f"series {[n for n in names if n]}; inbound: {inbound[:70]}")

    if fast:
        return summarize()

    # state BEFORE anything is written (the agent call logs an interaction row too)
    pre = b._pg_rows("SELECT COUNT(*) AS n, COALESCE(MAX(decision_id), 0) AS m "
                     "FROM allocation.allocation_decisions")[0]
    pre_i = b._pg_rows("SELECT COUNT(*) AS n FROM allocation.agent_interactions")[0]["n"]
    print(f"      pre : allocation_decisions={pre['n']} (max id {pre['m']}), agent_interactions={pre_i}")

    # the Genie callback has a pattern-matching input (the question chips) and two outputs
    chips = [{"id": {"type": "qchip", "i": i}, "property": "n_clicks", "value": 1 if i == 2 else 0}
             for i in range(6)]
    st, dt, r = c.callback(
        [("genie-out", "children"), ("genie-q", "value")], [],
        [("genie-q", "value", "")], '{"i":2,"type":"qchip"}.n_clicks',
        raw_inputs=[chips, {"id": "genie-btn", "property": "n_clicks", "value": None},
                    {"id": "genie-q", "property": "n_submit", "value": 0}])
    txt = " | ".join(text_of(r.get("genie-out", {}).get("children")))
    report("Genie question (chip)", st == 200 and "SELECT" in txt.upper(), st, dt, txt[:140])

    st, dt, r = c.callback(
        [("agent-q", "value"), ("agent-out", "children"), ("agent-store", "data")],
        [("sel", "data", SEL), ("agent-btn", "n_clicks", 1)],
        [("agent-q", "value", "SKU-01723 is short at MASS_A. Recommend how to split the available supply.")],
        "agent-btn.n_clicks")
    store = (r.get("agent-store") or {}).get("data") or {}
    atxt = " | ".join(text_of(r.get("agent-out", {}).get("children")))
    report("agent call (serving endpoint)", st == 200 and store.get("reco") is not None
           and "tool call(s) failed" not in atxt, st, dt,
           f"recommends {store.get('reco')} units; request id {store.get('request_id')}")

    if store.get("reco") is None:
        report("decide -> Lakebase INSERT", False, "-", 0.0, "no recommendation to record")
        return summarize()
    st, dt, r = c.callback(
        [("dec-out", "children"), ("saved", "data")], [("dec-save", "n_clicks", 1)],
        [("sel", "data", SEL), ("agent-store", "data", store), ("dec-action", "value", "accept"),
         ("dec-units", "value", store["reco"]), ("dec-why", "value", "deployed-app smoke test"),
         ("saved", "data", 0)], "dec-save.n_clicks")
    msg = " | ".join(text_of(r.get("dec-out", {}).get("children")))
    post = b._pg_rows("SELECT COUNT(*) AS n, COALESCE(MAX(decision_id), 0) AS m "
                      "FROM allocation.allocation_decisions")[0]
    print(f"      post: allocation_decisions={post['n']} (max id {post['m']})")
    row = b._pg_rows("SELECT * FROM allocation.allocation_decisions WHERE decision_id = %s", (post["m"],))
    new = row[0] if row else {}
    ok = (st == 200 and post["n"] == pre["n"] + 1 and new.get("agent_trace_id") == store.get("request_id")
          and new.get("rationale_text") == "deployed-app smoke test")
    report("decide -> Lakebase INSERT", ok, st, dt,
           f"{msg[:60]}; rows {pre['n']} -> {post['n']}; trace id carried: "
           f"{new.get('agent_trace_id') == store.get('request_id')}")

    # clean up exactly what this run created, by id
    if post["n"] == pre["n"] + 1 and new.get("rationale_text") == "deployed-app smoke test":
        with b.pg() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM allocation.allocation_decisions WHERE decision_id = %s", (post["m"],))
            cur.execute("DELETE FROM allocation.agent_interactions WHERE trace_id = %s",
                        (store.get("request_id"),))
            conn.commit()
        after = b._pg_rows("SELECT COUNT(*) AS n FROM allocation.allocation_decisions")[0]["n"]
        after_i = b._pg_rows("SELECT COUNT(*) AS n FROM allocation.agent_interactions")[0]["n"]
        print(f"      cleanup: deleted decision #{post['m']} and its interaction; "
              f"decisions={after}, interactions={after_i}")
        results.append(("cleanup restored counts", after == pre["n"] and after_i == pre_i))
        print(f"{'PASS' if results[-1][1] else 'FAIL'}  cleanup restored the pre-test counts")
    return summarize()


def summarize() -> int:
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed" + (f"  FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
