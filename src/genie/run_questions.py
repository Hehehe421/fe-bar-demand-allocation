"""Ask the Genie space the evaluation questions and score its answers against SQL.

    python -m src.genie.run_questions --tag rev1
    python -m src.genie.run_questions --tag rev2 --only q05_wape_family

Every question goes through the Conversation API in its own conversation. For each
we keep the SQL Genie generated, the rows it returned, its prose, and a verdict from
comparing those rows with the reference query in ``questions.py`` -- so "right" and
"wrong" are decided by data, not by reading the answer and nodding.

Writes ``evidence/metrics/genie_run_<tag>.json``.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from databricks.sdk import WorkspaceClient  # noqa: E402

import src.config as config  # noqa: E402
from src.genie.questions import QUESTIONS, Q  # noqa: E402
from tools.dbsql import sql  # noqa: E402

STATE = pathlib.Path("evidence/metrics/genie_space.json")
TOL = 0.01


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _close(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= max(TOL * abs(fb), 0.06)
    return str(a).strip().lower() == str(b).strip().lower()


def verdict(
    q: Q, ref_rows: list[list[Any]], got_rows: list[list[Any]], had_sql: bool,
    cols: list[str] | None = None,
) -> tuple[str, str]:
    cols = cols or []
    if q.check == "refusal":
        # Only the masked columns matter. (An earlier version looked at every cell
        # and flagged the echoed sku_id as "a value returned for a masked column".)
        masked = [i for i, c in enumerate(cols) if any(k in c.lower() for k in ("margin", "cost"))]
        vals = [r[i] for r in got_rows for i in masked if r[i] not in (None, "", "None")]
        if not had_sql or not vals:
            return "PASS", "masked columns came back NULL / no value stated"
        return "FAIL", f"returned a value for a masked column: {vals[:3]}"
    if not had_sql:
        return "FAIL", "Genie produced no SQL"
    if q.check == "scalar":
        if not got_rows:
            return "FAIL", "no rows"
        ok = _close(got_rows[0][0], ref_rows[0][0]) if len(got_rows[0]) == 1 else any(
            _close(c, ref_rows[0][0]) for c in got_rows[0])
        return ("PASS", "scalar within 1%") if ok else (
            "FAIL", f"expected {ref_rows[0][0]}, got {got_rows[0]}")
    if q.check == "values":
        got_nums = [n for r in got_rows for c in r if (n := _num(c)) is not None]
        want = [n for r in ref_rows for c in r if (n := _num(c)) is not None]
        if not want:
            return "FAIL", "reference has no numeric values (broken question)"
        missing = [w for w in want
                   if not any(abs(g - w) <= max(TOL * abs(w), 0.06) for g in got_nums)]
        return ("PASS", f"all {len(want)} reference values found") if not missing else (
            "FAIL", f"{len(missing)} of {len(want)} reference values missing, e.g. {missing[:3]}")
    if q.check == "top1":
        if not got_rows:
            return "FAIL", "no rows"
        miss = [v for v in ref_rows[0] if not any(_close(c, v) for c in got_rows[0])]
        return ("PASS", "first row matches the reference's top row") if not miss else (
            "FAIL", f"first row {got_rows[0]} lacks {miss}")
    if len(got_rows) != len(ref_rows):
        return "FAIL", f"{len(got_rows)} rows returned, reference has {len(ref_rows)}"
    pool = [list(r) for r in got_rows]
    for rr in ref_rows:
        hit = next((g for g in pool if all(any(_close(c, v) for c in g) for v in rr)), None)
        if hit is None:
            return "FAIL", f"reference row {rr} not found in Genie's rows"
        pool.remove(hit)
    return "PASS", "every reference row found, same row count"


def ask(w: WorkspaceClient, space_id: str, q: Q) -> dict[str, Any]:
    t0 = time.time()
    rec: dict[str, Any] = {"key": q.key, "question": q.question, "why": q.why}
    try:
        msg = w.genie.start_conversation_and_wait(space_id, q.question)
    except Exception as e:  # noqa: BLE001
        rec.update(error=f"{type(e).__name__}: {str(e)[:300]}", verdict="FAIL",
                   detail="API error", seconds=round(time.time() - t0, 1))
        return rec
    rec.update(conversation_id=msg.conversation_id, message_id=msg.message_id,
               status=str(msg.status), seconds=round(time.time() - t0, 1))
    gen_sql, text, rows, cols = None, [], [], []
    for a in msg.attachments or []:
        if a.query is not None:
            gen_sql = a.query.query
            rec["genie_description"] = a.query.description
            try:
                res = w.genie.get_message_attachment_query_result(
                    space_id, msg.conversation_id, msg.message_id, a.attachment_id)
                sr = res.statement_response
                cols = [c.name for c in (sr.manifest.schema.columns or [])]
                rows = [list(r) for r in (sr.result.data_array or [])] if sr.result else []
            except Exception as e:  # noqa: BLE001
                rec["result_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if a.text is not None:
            text.append(a.text.content)
    rec.update(genie_sql=gen_sql, genie_text=" ".join(t for t in text if t),
               genie_columns=cols, genie_rows=rows[:25], genie_row_count=len(rows))
    ref_cols, ref_rows = sql(q.reference_sql)
    rec.update(reference_sql=q.reference_sql, reference_columns=ref_cols,
               reference_rows=ref_rows[:25])
    rec["verdict"], rec["detail"] = verdict(q, ref_rows, rows, gen_sql is not None, cols)
    return rec


def main() -> int:
    tag = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "run"
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    w = WorkspaceClient(profile=config.PROFILE)
    state = json.loads(STATE.read_text())
    sid = state["space_id"]
    print(f"space {sid}  instruction rev {state.get('instruction_rev')}  tag {tag}\n")
    out = []
    for q in QUESTIONS:
        if only and q.key not in only:
            continue
        rec = ask(w, sid, q)
        out.append(rec)
        print(f"{rec['verdict']:5} {q.key:22} {rec.get('seconds', 0):5.1f}s  {rec['detail']}")
    passed = sum(1 for r in out if r["verdict"] == "PASS")
    print(f"\n{passed}/{len(out)} passed")
    path = pathlib.Path(f"evidence/metrics/genie_run_{tag}.json")
    path.write_text(json.dumps({"space_id": sid, "instruction_rev": state.get("instruction_rev"),
                                "tag": tag, "results": out}, indent=2, default=str))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
