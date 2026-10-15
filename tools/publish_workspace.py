"""Publish the build's reviewable content to the Databricks workspace.

    python tools/publish_workspace.py            # notebooks, docs, evidence, screenshots, index
    python tools/publish_workspace.py --dry-run  # list what would be uploaded

Why: the notebooks are executed on the cluster and committed with their outputs, but a reviewer
who lives in the workspace should not need the git repo. This puts everything next to the
notebooks that already live there:

    /Workspace/Users/<me>/fe-bar-demand-allocation/
        START_HERE.md          index, generated here
        README.md  docs/  evidence/  evidence/screens/  evidence/metrics/
        notebooks/             the executed .ipynb files, imported as notebooks WITH outputs
        src/                   (uploaded separately: `databricks workspace import-dir src ...`)

Markdown and images upload as workspace files and render in the workspace UI. Idempotent.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.workspace import ImportFormat  # noqa: E402

import src.config as config  # noqa: E402

PUBLISH_DIRS = {"docs": "*.md", "evidence": "*.md", "evidence/screens": "*.png",
                "evidence/metrics": "*.*"}


def base(me: str) -> str:
    return f"/Workspace/Users/{me}/fe-bar-demand-allocation"


def nb_url(path: str) -> str:
    return f"{config.WORKSPACE_HOST}/#workspace{path}"


def start_here(me: str, app_url: str | None, notebooks: list[str]) -> str:
    b = base(me)
    genie = json.loads((ROOT / "evidence/metrics/genie_space.json").read_text())["space_id"]
    rows = "\n".join(f"| `{n}` | {nb_url(f'{b}/notebooks/{n}')} |" for n in notebooks)
    return f"""# Demand forecasting and constrained allocation: start here

All data is synthetic. Everything below is in this folder, so you do not need the git repo.

## The product

| | |
|---|---|
| **Allocation Command Center** (app) | {app_url or '(deploy the app first)'} |
| **Genie space** | {config.WORKSPACE_HOST}/genie/rooms/{genie} |

## Read in this order

| Item | Where |
|---|---|
| What was built and what is honestly still open | `docs/HANDOFF.md` |
| Overview, status, headline numbers | `README.md` |
| Evidence per stage (each one a file) | `evidence/01_lakeflow.md` ... `evidence/07_app.md` |
| Screenshots of the app | `evidence/screens/` |
| Measured metrics (CSV / JSON) | `evidence/metrics/` |
| Where every resource id came from | `docs/PROVENANCE.md` |

## Executed notebooks (outputs included)

| Notebook | Open |
|---|---|
{rows}

`03_demand_forecast` is the centrepiece (feature store, 7-model sweep, champion/challenger,
batch and real-time serving). `04_allocation_agent` is the GenAI agent and its evaluation.

## Honest gaps, up front

* Forecast intervals under-cover on trend-driven items (74% against a 90% target), so the
  exposure shown there is likely understated.
* The agent's numeric grounding is about 74%; a mechanical scorer catches what two LLM judges miss.
* The deployed agent shipped for a week unable to read any data; it is fixed and now verified
  (`evidence/07_app.md`).
* Product images are generated illustrations, not photographs.
"""


def main() -> int:
    dry = "--dry-run" in sys.argv
    w = WorkspaceClient(profile=config.PROFILE)
    me = w.current_user.me().user_name
    b = base(me)
    plan: list[tuple[str, pathlib.Path | None, ImportFormat, str]] = []

    nb_names = []
    for nb in sorted((ROOT / "notebooks").glob("*.ipynb")):
        name = nb.stem
        nb_names.append(name)
        plan.append((f"{b}/notebooks/{name}", nb, ImportFormat.JUPYTER, "notebook (with outputs)"))
    plan.append((f"{b}/README.md", ROOT / "README.md", ImportFormat.AUTO, "file"))
    for rel, pattern in PUBLISH_DIRS.items():
        for f in sorted((ROOT / rel).glob(pattern)):
            if f.is_file() and f.name != ".DS_Store":
                plan.append((f"{b}/{rel}/{f.name}", f, ImportFormat.AUTO, "file"))

    app_url = None
    try:
        app_url = w.apps.get(config.APP_NAME).url
    except Exception:  # noqa: BLE001
        pass
    index = start_here(me, app_url, nb_names)

    if dry:
        for p, _, _fmt, kind in plan:
            print(f"{kind:24} {p}")
        print(f"{'file':24} {b}/START_HERE.md\n{len(plan) + 1} items")
        return 0

    dirs = {str(pathlib.PurePosixPath(p).parent) for p, *_ in plan} | {b}
    for d in sorted(dirs):
        w.workspace.mkdirs(d)
    failed = 0
    for path, local, fmt, _kind in plan:
        try:
            with local.open("rb") as fh:
                w.workspace.upload(path, fh, format=fmt, overwrite=True)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAILED {path}: {type(e).__name__}: {str(e)[:140]}")
    w.workspace.upload(f"{b}/START_HERE.md", io.BytesIO(index.encode()), format=ImportFormat.AUTO,
                       overwrite=True)
    print(f"published {len(plan) + 1 - failed} of {len(plan) + 1} items to {b}")
    print(f"index: {nb_url(b + '/START_HERE.md')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
