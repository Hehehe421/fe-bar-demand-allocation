"""Execute a notebook on a Databricks cluster and commit the real outputs.

Why this exists
---------------
The FE Bar evaluator reads text only, so every notebook in this repo must be
committed as ``.ipynb`` *with cell outputs preserved*. A job-run notebook task
does not write its outputs back to the source notebook, and there is no browser
in this build loop to run cells interactively.

So notebooks are authored as ``*.py`` in jupytext "percent" format (readable,
diffable) and executed cell-by-cell against a real cluster through the
Databricks Command Execution API, inside a single execution context so state
carries across cells exactly as in an interactive session. Whatever the cluster
returns is captured verbatim and written into the ``.ipynb`` as cell outputs,
and mirrored to a markdown export.

Nothing here synthesises or edits output text. If a cell raises, the traceback
is captured as the cell's output and the run stops (``--keep-going`` to
continue), so a committed notebook can never show a green cell that did not run.

Usage
-----
    python tools/dbx_notebook.py notebooks/01_generate_data.py \
        --cluster-id 1008-205814-qhbfm7tc

Writes ``notebooks/01_generate_data.ipynb`` and
``notebooks/exports/01_generate_data.md``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
import time

import nbformat
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.compute import Language

CELL_RE = re.compile(r"^#\s*%%\s*(\[markdown\])?\s*(.*)$")


# ---------------------------------------------------------------------------
# percent-format parsing
# ---------------------------------------------------------------------------


class Cell:
    def __init__(self, kind: str, title: str = "") -> None:
        self.kind = kind  # "code" | "markdown"
        self.title = title
        self.lines: list[str] = []

    @property
    def source(self) -> str:
        return "\n".join(self.lines).strip("\n")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Cell({self.kind}, {len(self.lines)} lines)"


def parse_percent(path: pathlib.Path) -> list[Cell]:
    """Split a jupytext percent-format .py into cells."""
    cells: list[Cell] = []
    current: Cell | None = None
    for raw in path.read_text().splitlines():
        m = CELL_RE.match(raw)
        if m:
            kind = "markdown" if m.group(1) else "code"
            current = Cell(kind, (m.group(2) or "").strip())
            cells.append(current)
            continue
        if current is None:
            # preamble before the first marker becomes a code cell
            current = Cell("code")
            cells.append(current)
        current.lines.append(raw)

    out: list[Cell] = []
    for c in cells:
        if c.kind == "markdown":
            # strip the leading comment marker from markdown bodies
            c.lines = [re.sub(r"^#\s?", "", ln) for ln in c.lines]
        if c.source.strip():
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# cluster execution
# ---------------------------------------------------------------------------


class ClusterSession:
    """A single Command Execution API context, reused across all cells."""

    def __init__(self, profile: str, cluster_id: str) -> None:
        self.w = WorkspaceClient(profile=profile)
        self.cluster_id = cluster_id
        self.context_id: str | None = None

    def __enter__(self) -> ClusterSession:
        self._wait_running()
        ctx = self.w.command_execution.create(
            cluster_id=self.cluster_id, language=Language.PYTHON
        ).result()
        self.context_id = ctx.id
        return self

    def __exit__(self, *exc: object) -> None:
        if self.context_id:
            try:
                self.w.command_execution.destroy(
                    cluster_id=self.cluster_id, context_id=self.context_id
                )
            except Exception:  # noqa: BLE001 - teardown must never mask a real error
                pass

    def _wait_running(self, timeout_s: int = 1800) -> None:
        deadline = time.time() + timeout_s
        last = ""
        while time.time() < deadline:
            c = self.w.clusters.get(self.cluster_id)
            state = str(c.state)
            if "RUNNING" in state:
                return
            if any(bad in state for bad in ("TERMINATED", "ERROR", "UNKNOWN")):
                raise RuntimeError(f"cluster {self.cluster_id} is {state}: {c.state_message}")
            if state != last:
                print(f"  [cluster {state}] {c.state_message or ''}", flush=True)
                last = state
            time.sleep(15)
        raise TimeoutError(f"cluster {self.cluster_id} did not reach RUNNING in {timeout_s}s")

    def run(self, code: str, timeout_s: int = 3600) -> tuple[str, bool]:
        """Execute ``code``; return (output_text, ok)."""
        assert self.context_id
        waiter = self.w.command_execution.execute(
            cluster_id=self.cluster_id,
            context_id=self.context_id,
            language=Language.PYTHON,
            command=code,
        )
        res = waiter.result(timeout=dt.timedelta(seconds=timeout_s))
        r = res.results
        if r is None:
            return (f"<no results; status={res.status}>", False)

        rtype = str(getattr(r, "result_type", "") or "")
        if "error" in rtype.lower():
            parts = [p for p in (r.summary, r.cause) if p]
            return ("\n".join(parts) or "<error with no detail>", False)

        data = r.data
        if data is None:
            return ("", True)
        if isinstance(data, str):
            return (data, True)
        return (str(data), True)


# ---------------------------------------------------------------------------
# notebook assembly
# ---------------------------------------------------------------------------


def build_notebook(
    cells: list[Cell], results: dict[int, tuple[str, bool]]
) -> nbformat.NotebookNode:
    nb = nbformat.v4.new_notebook()
    nb.metadata.update(
        {
            "language_info": {"name": "python", "version": "3.12"},
            "kernelspec": {"display_name": "Databricks DBR 16.4 LTS ML", "name": "python3"},
        }
    )
    exec_count = 0
    for i, c in enumerate(cells):
        if c.kind == "markdown":
            nb.cells.append(nbformat.v4.new_markdown_cell(c.source))
            continue
        exec_count += 1
        cell = nbformat.v4.new_code_cell(c.source)
        cell.execution_count = exec_count
        text, ok = results.get(i, ("<not executed>", False))
        if text.strip():
            if ok:
                cell.outputs = [
                    nbformat.v4.new_output("stream", name="stdout", text=text.rstrip() + "\n")
                ]
            else:
                cell.outputs = [
                    nbformat.v4.new_output(
                        "error",
                        ename="DatabricksCommandError",
                        evalue="cell failed on cluster",
                        traceback=text.rstrip().splitlines(),
                    )
                ]
        nb.cells.append(cell)
    return nb


def build_markdown(title: str, cells: list[Cell], results: dict[int, tuple[str, bool]],
                   header: str) -> str:
    out = [f"# {title}", "", header, ""]
    for i, c in enumerate(cells):
        if c.kind == "markdown":
            out += [c.source, ""]
            continue
        out += ["```python", c.source, "```", ""]
        text, ok = results.get(i, ("<not executed>", False))
        if text.strip():
            label = "Output" if ok else "Output (FAILED)"
            out += [f"_{label}:_", "", "```text", text.rstrip(), "```", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook", type=pathlib.Path, help="percent-format .py notebook")
    ap.add_argument("--profile", default="vm-summer-classic")
    ap.add_argument("--cluster-id", required=True)
    ap.add_argument("--cell-timeout", type=int, default=3600)
    ap.add_argument("--keep-going", action="store_true",
                    help="continue after a failing cell instead of stopping")
    args = ap.parse_args()

    src = args.notebook
    cells = parse_percent(src)
    code_cells = [i for i, c in enumerate(cells) if c.kind == "code"]
    print(f"{src.name}: {len(cells)} cells ({len(code_cells)} code)")

    results: dict[int, tuple[str, bool]] = {}
    started = dt.datetime.now(dt.UTC)
    failed = 0

    with ClusterSession(args.profile, args.cluster_id) as sess:
        print(f"  context {sess.context_id} on cluster {args.cluster_id}")
        for n, i in enumerate(code_cells, 1):
            t0 = time.time()
            text, ok = sess.run(cells[i].source, timeout_s=args.cell_timeout)
            results[i] = (text, ok)
            flag = "ok " if ok else "ERR"
            print(f"  [{flag}] cell {n}/{len(code_cells)} ({time.time() - t0:.1f}s)", flush=True)
            if not ok:
                failed += 1
                print("    " + "\n    ".join(text.splitlines()[:12]), flush=True)
                if not args.keep_going:
                    break

    finished = dt.datetime.now(dt.UTC)
    header = (
        f"> Executed by `tools/dbx_notebook.py` on Databricks cluster "
        f"`{args.cluster_id}` ({src.stem}), profile `{args.profile}`.\n"
        f"> Started {started:%Y-%m-%d %H:%M:%S} UTC, finished {finished:%Y-%m-%d %H:%M:%S} UTC. "
        f"Outputs below are captured verbatim from the cluster."
    )

    nb = build_notebook(cells, results)
    ipynb = src.with_suffix(".ipynb")
    nbformat.write(nb, str(ipynb))

    exports = src.parent / "exports"
    exports.mkdir(exist_ok=True)
    md = exports / f"{src.stem}.md"
    title = src.stem.replace("_", " ")
    md.write_text(build_markdown(title, cells, results, header))

    print(f"wrote {ipynb} and {md}")
    if failed:
        print(f"FAILED cells: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
