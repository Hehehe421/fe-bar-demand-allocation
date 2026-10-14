"""Stage D — sync `allocation_decisions` from Lakebase back into Unity Catalog.

This closes the loop. The synced tables push governed Delta data *out* to
Postgres for the app to read; this brings the app's *writes* back, so the
decision becomes an auditable Delta table that joins to outcomes and is
answerable in Genie.

Why it is written this way: Lakebase's native Postgres-to-UC change data feed
(`databricks postgres create-cdf-config`, Beta) is the production mechanism and
is named as such in the README. It is not used here because the decision table
is small and low-frequency — a full reconcile is simpler to reason about and
makes the round trip reproducible from a single command — and because a Beta
CDC path would need its own monitoring to be trustworthy evidence. The
trade-off is latency: this is a batch reconcile, not streaming.

The write is a MERGE on `decision_id`, so re-running is idempotent and late
edits to a decision are picked up rather than duplicated.

    python -m src.lakebase.writeback
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import src.config as config  # noqa: E402
from src.lakebase.provision import connect  # noqa: E402
from tools.dbsql import sql, table  # noqa: E402

TARGET = config.FCT_ALLOCATION_DECISIONS

COLUMNS = [
    ("decision_id", "BIGINT"),
    ("sku_id", "STRING"),
    ("retailer_code", "STRING"),
    ("week_end_date", "DATE"),
    ("recommended_units", "INT"),
    ("planner_units", "INT"),
    ("override_delta_units", "INT"),
    ("action", "STRING"),
    ("planner_id", "STRING"),
    ("rationale_text", "STRING"),
    ("agent_trace_id", "STRING"),
    ("model_version", "INT"),
    ("severity_score", "DOUBLE"),
    ("units_at_risk", "INT"),
    ("revenue_at_risk_usd", "DOUBLE"),
    ("decided_at", "TIMESTAMP"),
]


def ensure_target() -> None:
    cols = ",\n  ".join(f"{n} {t}" for n, t in COLUMNS)
    sql(
        f"""
        CREATE TABLE IF NOT EXISTS {TARGET} (
          {cols},
          synced_from_lakebase_at TIMESTAMP
        )
        COMMENT 'Planner allocation decisions, synced back from the Lakebase OLTP
store (allocation.allocation_decisions) into Unity Catalog. This is what makes
the decision auditable, joinable to forecast accuracy and answerable in Genie --
today the decision is made verbally in a Monday meeting and never recorded, so
nobody can measure whether planner overrides beat the model. Reconciled by
src/lakebase/writeback.py with a MERGE on decision_id. SYNTHETIC DATA.'
        """
    )


def fetch_from_lakebase() -> list[tuple]:
    names = ", ".join(n for n, _ in COLUMNS)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {names} FROM allocation.allocation_decisions ORDER BY decision_id")
        return cur.fetchall()


def _lit(v: object, t: str) -> str:
    if v is None:
        return "NULL"
    if t in ("BIGINT", "INT", "DOUBLE"):
        return str(v)
    s = str(v).replace("'", "''")
    if t == "DATE":
        return f"DATE'{s}'"
    if t == "TIMESTAMP":
        return f"TIMESTAMP'{s}'"
    return f"'{s}'"


def merge(rows: list[tuple]) -> None:
    if not rows:
        print("nothing to sync")
        return
    names = [n for n, _ in COLUMNS]
    types = [t for _, t in COLUMNS]
    values = ",\n    ".join(
        "(" + ", ".join(_lit(v, types[i]) for i, v in enumerate(r)) + ")" for r in rows
    )
    collist = ", ".join(names)
    set_clause = ",\n    ".join(f"t.{n} = s.{n}" for n in names if n != "decision_id")
    insert_cols = ", ".join([*names, "synced_from_lakebase_at"])
    insert_vals = ", ".join([f"s.{n}" for n in names] + ["current_timestamp()"])
    sql(
        f"""
        MERGE INTO {TARGET} AS t
        USING (SELECT * FROM VALUES
    {values}
        AS v({collist})) AS s
        ON t.decision_id = s.decision_id
        WHEN MATCHED THEN UPDATE SET
    {set_clause},
    t.synced_from_lakebase_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        """
    )


def main() -> int:
    print(f"target: {TARGET}")
    ensure_target()
    rows = fetch_from_lakebase()
    print(f"read {len(rows)} rows from Lakebase allocation.allocation_decisions")
    merge(rows)

    print("\n--- UC side, after the round trip ---")
    print(table(f"SELECT COUNT(*) AS uc_rows FROM {TARGET}"))
    print()
    print("Do planner overrides beat the model? This is the question the round")
    print("trip exists to make answerable:")
    print()
    print(
        table(
            f"""
SELECT action,
       COUNT(*)                            AS decisions,
       SUM(recommended_units)              AS recommended_units,
       SUM(planner_units)                  AS planner_units,
       SUM(override_delta_units)           AS delta_units,
       ROUND(AVG(severity_score), 1)       AS avg_severity,
       ROUND(SUM(revenue_at_risk_usd), 0)  AS revenue_at_risk_usd
FROM {TARGET} GROUP BY action ORDER BY action
"""
        )
    )
    print()
    print("Joined back to the risk table it came from, proving it is joinable:")
    print()
    print(
        table(
            f"""
SELECT d.action, d.sku_id, d.retailer_code, d.recommended_units, d.planner_units,
       d.override_delta_units, r.risk_tier, r.weeks_to_stockout,
       r.units_at_risk AS risk_units_at_risk
FROM {TARGET} d
JOIN {config.FCT_STOCKOUT_RISK} r
  ON r.sku_id = d.sku_id AND r.retailer_code = d.retailer_code
ORDER BY d.decision_id LIMIT 10
"""
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
