"""Run SQL on the project's SQL warehouse and render results as plain text.

Evidence files have to be readable as text, so this returns fixed-width tables
rather than objects. Used by the evidence generators for every stage.

    from tools.dbsql import sql, table
    print(table("SELECT * FROM x LIMIT 5"))
"""

from __future__ import annotations

import os
import sys
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

_PROFILE = os.environ.get("DBX_PROFILE", "vm-summer-classic")
_WAREHOUSE = os.environ.get("DBX_WAREHOUSE_ID", "a6857db5911f7af1")

_w: WorkspaceClient | None = None


def client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient(profile=_PROFILE)
    return _w


def sql(query: str, warehouse_id: str | None = None) -> tuple[list[str], list[list[Any]]]:
    """Execute ``query``; return (column_names, rows). Raises on error."""
    resp = client().statement_execution.execute_statement(
        statement=query,
        warehouse_id=warehouse_id or _WAREHOUSE,
        wait_timeout="50s",
    )
    # poll if the warehouse needed longer than the inline wait
    while resp.status and resp.status.state in (
        StatementState.PENDING,
        StatementState.RUNNING,
    ):
        resp = client().statement_execution.get_statement(resp.statement_id)

    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error
        raise RuntimeError(
            f"SQL {resp.status.state}: {getattr(err, 'message', err)}\n--- query ---\n{query}"
        )

    cols = [c.name for c in (resp.manifest.schema.columns or [])] if resp.manifest else []
    rows = [list(r) for r in (resp.result.data_array or [])] if resp.result else []
    return cols, rows


def table(query: str, max_col: int = 60, warehouse_id: str | None = None) -> str:
    """Execute ``query`` and format the result as a fixed-width text table."""
    cols, rows = sql(query, warehouse_id)
    if not cols:
        return "(no result set)"

    def cell(v: Any) -> str:
        s = "NULL" if v is None else str(v)
        return s if len(s) <= max_col else s[: max_col - 1] + "…"

    body = [[cell(v) for v in r] for r in rows]
    widths = [len(c) for c in cols]
    for r in body:
        for i, v in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(v))

    sep = "  ".join("-" * w for w in widths)
    out = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)), sep]
    out += ["  ".join(v.ljust(widths[i]) for i, v in enumerate(r)) for r in body]
    out.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(out)


def scalar(query: str, warehouse_id: str | None = None) -> Any:
    _, rows = sql(query, warehouse_id)
    return rows[0][0] if rows and rows[0] else None


if __name__ == "__main__":
    print(table(sys.argv[1]))
