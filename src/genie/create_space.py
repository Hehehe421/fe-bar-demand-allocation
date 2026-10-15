"""Create or update the Genie space from ``space_definition``.

    python -m src.genie.create_space                # create, or PATCH if it exists
    python -m src.genie.create_space --rev 1        # build instruction revision N
    python -m src.genie.create_space --show         # print the serialized_space only

The payload is built in Python and posted through the SDK -- no shell quoting. After
writing, the space is fetched back with ``include_serialized_space=true`` and the
stored tables and instructions are compared with what was sent, because the CLI
accepts unknown fields silently and a write that "succeeded" proves nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from databricks.sdk import WorkspaceClient  # noqa: E402

import src.config as config  # noqa: E402
from src.genie import space_definition as sd  # noqa: E402

PARENT = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
STATE = pathlib.Path("evidence/metrics/genie_space.json")
DESCRIPTION = (
    "Ask planning questions about demand forecasts, projected stockouts, retailer fill "
    "rate and OTIF, and planner allocation decisions. Governed Gold tables and metric "
    "views. All data is synthetic."
)


def find_space(w: WorkspaceClient) -> str | None:
    if STATE.exists():
        sid = json.loads(STATE.read_text()).get("space_id")
        try:
            w.api_client.do("GET", f"/api/2.0/genie/spaces/{sid}")
            return sid
        except Exception:  # noqa: BLE001
            pass
    r = w.api_client.do("GET", "/api/2.0/genie/spaces")
    for s in r.get("spaces", []):
        if s.get("title") == config.GENIE_SPACE_NAME:
            return s["space_id"]
    return None


def main() -> int:
    rev = int(sys.argv[sys.argv.index("--rev") + 1]) if "--rev" in sys.argv else None
    if "--show" in sys.argv:
        print(sd.dumps(rev))
        return 0
    w = WorkspaceClient(profile=config.PROFILE)
    inner = sd.dumps(rev)
    body = {
        "title": config.GENIE_SPACE_NAME,
        "description": DESCRIPTION,
        "warehouse_id": config.WAREHOUSE_ID,
        "serialized_space": inner,
    }
    sid = find_space(w)
    if sid:
        w.api_client.do("PATCH", f"/api/2.0/genie/spaces/{sid}", body=body)
        print(f"updated space {sid}")
    else:
        r = w.api_client.do("POST", "/api/2.0/genie/spaces", body={**body, "parent_path": PARENT})
        sid = r["space_id"]
        print(f"created space {sid}")

    back = w.api_client.do(
        "GET", f"/api/2.0/genie/spaces/{sid}", query={"include_serialized_space": "true"}
    )
    stored = json.loads(back["serialized_space"])
    sent = json.loads(inner)
    t_sent = [t["identifier"] for t in sent["data_sources"]["tables"]]
    t_back = [t["identifier"] for t in stored["data_sources"]["tables"]]
    ok_tables = t_sent == t_back
    txt_sent = sent["instructions"]["text_instructions"][0]["content"]
    txt_back = (stored["instructions"].get("text_instructions") or [{}])[0].get("content")
    ok_text = txt_sent == txt_back
    n = {k: len(stored["instructions"].get(k, [])) for k in
         ("example_question_sqls", "join_specs", "text_instructions")}
    print(f"tables stored      : {len(t_back)}  (match sent: {ok_tables})")
    print(f"instructions match : {ok_text}   counts: {n}")
    print(f"benchmarks stored  : {len(stored.get('benchmarks', {}).get('questions', []))}")
    STATE.write_text(json.dumps(
        {"space_id": sid, "instruction_rev": rev or sd.LATEST_REV}, indent=2))
    print(f"url: {config.WORKSPACE_HOST}/genie/rooms/{sid}")
    return 0 if ok_tables and ok_text else 1


if __name__ == "__main__":
    sys.exit(main())
