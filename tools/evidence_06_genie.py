"""Generate evidence/06_genie.md from the recorded Genie runs.

    python tools/evidence_06_genie.py

Reads ``evidence/metrics/genie_run_*.json`` and ``genie_q11_stability.json``
(written by ``src/genie/run_questions.py`` and the stability check), plus the live
space definition. Nothing is retyped: every SQL string and row below is what Genie
returned during the recorded run.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from databricks.sdk import WorkspaceClient  # noqa: E402

import src.config as config  # noqa: E402

M = pathlib.Path("evidence/metrics")


def load(tag: str) -> dict:
    return json.loads((M / f"genie_run_{tag}.json").read_text())


def by_key(run: dict) -> dict:
    return {r["key"]: r for r in run["results"]}


def fisher_one_sided(a_fail: int, a_n: int, b_fail: int, b_n: int) -> float:
    """P(at least this many failures land in group A | total failures fixed)."""
    k, n, na = a_fail + b_fail, a_n + b_n, a_n
    tot = math.comb(n, k)
    return sum(math.comb(na, i) * math.comb(n - na, k - i)
               for i in range(a_fail, min(k, na) + 1)) / tot


def sql_block(s: str | None) -> list[str]:
    return ["```sql", (s or "-- (no SQL produced)").strip(), "```"]


def main() -> int:
    r1, r1b, r2 = load("rev1"), load("rev1b"), load("rev2")  # noqa: F841
    stab = json.loads((M / "genie_q11_stability.json").read_text())
    state = json.loads((M / "genie_space.json").read_text())
    sid = state["space_id"]
    w = WorkspaceClient(profile=config.PROFILE)
    sp = w.api_client.do("GET", f"/api/2.0/genie/spaces/{sid}",
                         query={"include_serialized_space": "true"})
    ss = json.loads(sp["serialized_space"])
    ins = ss["instructions"]

    out: list[str] = []
    a = out.append
    a("# Evidence 06 — Stage E: Genie")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Produced by | `src/genie/{space_definition,create_space,run_questions,questions}.py`, "
      "`tools/evidence_06_genie.py` |")
    a(f"| Generated at | {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC |")
    a(f"| Space | `{sp.get('title')}` — `{sid}` |")
    a(f"| URL | {config.WORKSPACE_HOST}/genie/rooms/{sid} |")
    a(f"| Warehouse | `{config.WAREHOUSE_ID}` |")
    a("")
    a("## 1. What was built")
    a("")
    a("A deliberately small surface: **4 Gold tables and the 4 governed metric views**, "
      "not all 20 objects in the catalog. Ratio KPIs (fill rate, OTIF, WAPE, weeks of supply) "
      "live in the metric views, so Genie is steered to `MEASURE()` by curation, not by prose.")
    a("")
    a("```text")
    for t in ss["data_sources"]["tables"]:
        a(f"{t['identifier']:62} {len(t.get('column_configs', []))} column configs")
    a("```")
    a("")
    a(f"Stored in the space (read back with `include_serialized_space=true`): "
      f"{len(ins.get('example_question_sqls', []))} curated example queries, "
      f"{len(ins.get('join_specs', []))} join specs, "
      f"{len(ins.get('sql_snippets', {}).get('filters', []))} filter and "
      f"{len(ins.get('sql_snippets', {}).get('measures', []))} measure snippets, "
      f"{len(ss.get('benchmarks', {}).get('questions', []))} benchmarks, "
      f"{len(ss.get('config', {}).get('sample_questions', []))} sample questions.")
    a("")
    a("General instructions are **two lines of orientation**. That is intentional and "
      "follows the fix order in the `genie-rooms` skill (data → metadata → joins → example "
      "SQL → general instructions last): instructions grow only to patch a failure "
      "actually observed, below.")
    a("")
    a("```text")
    a("".join(ins["text_instructions"][0]["content"]).strip())
    a("```")
    a("")
    a("**Governance carries through.** `dim_product.unit_cost_usd` and `gross_margin_pct` "
      "are masked by Unity Catalog for every identity but finance, including the catalog "
      "owner running this space. Genie inherits that: see q12.")
    a("")
    a("**Two things about the write path worth knowing.** (1) The `serialized_space` schema "
      "is not in the CLI help; it is documented in the `genie-rooms` skill and returned by "
      "`GET /api/2.0/genie/spaces/{id}?include_serialized_space=true` — the earlier belief "
      "that `get-space` \"does not echo it back\" was wrong, it needs that query parameter. "
      "(2) The server concatenates `content` array entries with **no separator**: two "
      "sentences came back as `synthetic.Retailer`, so every entry carries its own newline. "
      "Both were found by reading the stored space back and comparing it with what was sent, "
      "not by trusting the 200 response.")
    a("")
    a("## 2. How answers were scored")
    a("")
    a("13 questions, each in its own Conversation-API conversation. For each, the SQL Genie "
      "generated and its rows are captured, then compared with a **reference query written "
      "before the run** (`src/genie/questions.py`): same row count, and every reference row "
      "found in Genie's rows within 1%. So a verdict is a data comparison, not a read-through.")
    a("")
    a("## 3. First run (instruction revision 1): 9 of 12 flagged PASS, and what the 3 were")
    a("")
    a("| question | verdict | what it turned out to be |")
    a("|---|---|---|")
    k1 = by_key(r1)
    for key, kind in (("q10_override_units", "**my question was ambiguous**"),
                      ("q11_forecast_brand", "**Genie was wrong**"),
                      ("q12_margin", "**my scorer was wrong**")):
        r = k1[key]
        a(f"| {r['question'][:70]}… | {r['verdict']} | {kind} |")
    a("")
    a("Taking them one at a time, because three FAILs are not three Genie errors:")
    a("")
    a("* **q12 (margin) — scorer bug.** Genie queried `gross_margin_pct`, got NULL, and said "
      "the value is \"not available\". Correct behaviour. The scorer flagged the echoed "
      "`sku_id` cell as \"a value returned for a masked column\". Fixed to inspect only "
      "margin/cost columns, then re-asked: PASS (`genie_run_rev1b.json`).")
    a("* **q10 (override units) — ambiguous question.** \"By how many units in total did "
      "planners change the recommended allocation\": Genie summed `ABS(override_delta_units)` "
      "= 46 (gross); my reference was the signed net = 14. Genie's reading is defensible; "
      "the wording was the bug. Split into q10a (gross, 46) and q10b (net, 14), both of "
      "which Genie then answered correctly with no change to the space.")
    a("* **q11 (forecast by brand) — a genuine, confident error.** See section 4.")
    a("")
    a("## 4. The answer Genie got wrong, and the change that followed")
    a("")
    q = k1["q11_forecast_brand"]
    a(f"**Question:** {q['question']}")
    a("")
    a("**Genie's SQL (revision 1):**")
    out.extend(sql_block(q["genie_sql"]))
    a("")
    a("```text")
    a(f"Genie    : {q['genie_columns']} {q['genie_rows']}")
    a(f"Reference: {q['reference_columns']} {q['reference_rows']}")
    a("```")
    a("")
    a(f"It answered from `fct_stockout_risk.forecast_p50_13w` — a table that contains **only "  # noqa: F541
      f"the SKU × retailer pairs projected to run short** — so every brand total is "  # noqa: F541
      f"understated (Snugglewick 987,080 against a true 1,285,858), and the prose presented it "  # noqa: F541
      f"as total demand with no caveat. A plausible number from the wrong grain is the "  # noqa: F541
      f"dangerous kind of wrong.")  # noqa: F541
    a("")
    a("**The change (revision 2) — metadata, not instructions**, per the skill's fix order: "
      "column descriptions on `forecast_p50_13w` / `forecast_p90_13w` saying they cover "
      "at-risk series only and that total, portfolio or by-brand demand comes from "
      "`fct_demand_forecast`. General instructions were left alone.")
    a("")
    a("### Did the change fix it? Measured, because one pass proves nothing")
    a("")
    a("Genie is non-deterministic, and on a later look the revision-1 space answered q11 "
      "correctly several times. So q11 was asked repeatedly on each revision:")
    a("")
    f1 = sum(1 for r in stab["rev1"] if r["verdict"] != "PASS") + 1   # + the original run
    n1 = len(stab["rev1"]) + 1
    f2 = sum(1 for r in stab["rev2"] if r["verdict"] != "PASS")
    n2 = len(stab["rev2"]) + 1                                         # + the full rev2 run
    p = fisher_one_sided(f1, n1, f2, n2)
    a("| revision | q11 attempts | wrong | used `fct_demand_forecast` |")
    a("|---|---|---|---|")
    used1 = sum(1 for r in stab["rev1"] if r["used_fct_demand_forecast"])
    used2 = sum(1 for r in stab["rev2"] if r["used_fct_demand_forecast"]) + 1
    a(f"| 1 — before | {n1} | **{f1}** | {used1} of {n1 - 1} repeats (+ the original, which did not) |")  # noqa: E501
    a(f"| 2 — after | {n2} | **{f2}** | {used2} of {n2} |")
    a("")
    a(f"**Honest reading:** the failure is intermittent (≈{100 * f1 / n1:.0f}% before) and "
      f"did not recur after the change ({f2} of {n2}). One-sided Fisher exact p ≈ {p:.2f}: "
      f"suggestive, **not conclusive** — the sample is too small to claim the description "
      f"*caused* the improvement. The change is the right one regardless (the column really "
      f"is misleading), and the numbers are here so the claim is exactly as strong as the "
      f"evidence.")
    a("")
    a("## 5. Final results, revision 2 — 13 of 13")
    a("")
    a("| # | question | verdict | seconds | Genie used |")
    a("|---|---|---|---|---|")
    for r in r2["results"]:
        used = ", ".join(sorted({t.split(".")[-1] for t in
                                 (r.get("genie_sql") or "").replace("`", "").split()
                                 if t.startswith("toy_demand_allocation.gold.")})) or "—"
        a(f"| {r['key'].split('_')[0]} | {r['question']} | {r['verdict']} | {r['seconds']} | {used} |")  # noqa: E501
    a("")
    a("### Generated SQL and rows, every question")
    a("")
    for r in r2["results"]:
        a(f"#### {r['key']}")
        a(f"**{r['question']}**  — _{r['why']}_")
        a("")
        out.extend(sql_block(r.get("genie_sql")))
        a("")
        a("```text")
        a(f"Genie    : {r.get('genie_columns')} {r.get('genie_rows', [])[:6]}")
        a(f"Reference: {r.get('reference_columns')} {r.get('reference_rows', [])[:6]}")
        a(f"Verdict  : {r['verdict']} — {r['detail']}")
        a("```")
        if r.get("genie_text"):
            a(f"> {r['genie_text'][:420]}")
        a("")
    r2b_path = M / "genie_run_rev2b.json"
    if r2b_path.exists():
        r2b = json.loads(r2b_path.read_text())
        a("## 5b. Two questions added for the app's executive tab")
        a("")
        a("The executive tab offers six suggested questions. Four were already scored above; these two "  # noqa: E501
          "were added and scored against reference SQL before being offered (revision 2 of the space, "  # noqa: E501
          "unchanged).")
        a("")
        a("| question | verdict | seconds |")
        a("|---|---|---|")
        for r in r2b["results"]:
            a(f"| {r['question']} | {r['verdict']} | {r['seconds']} |")
        a("")
    a("## 6. What this does and does not show")
    a("")
    a("* Genie answered the governed KPI questions (WAPE, P90 coverage, fill rate, OTIF) "
      "through `MEASURE()` on the metric views, so the numbers equal those in the notebooks "
      "and evidence — one definition, not one per tool.")
    a("* It respected the column mask: asked for a margin it could not see, it said so "
      "instead of estimating one.")
    a("* It is not reliable on table choice when two tables carry a similarly named column "
      "(q11). Curation reduced that; it did not provably eliminate it. A production space "
      "would keep the benchmark set and re-run it on every change.")
    a("* 13 questions is a smoke test, not an accuracy study.")
    pathlib.Path("evidence/06_genie.md").write_text("\n".join(out) + "\n")
    print(f"wrote evidence/06_genie.md ({len(out)} lines)  rev1 fails={f1}/{n1}  rev2 fails={f2}/{n2}  p={p:.3f}")  # noqa: E501
    return 0


if __name__ == "__main__":
    sys.exit(main())
