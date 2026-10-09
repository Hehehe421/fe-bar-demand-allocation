"""Stage D — provision the Lakebase OLTP schema and prove it works.

Applies `schema.sql`, then exercises the store the way the app will: an INSERT
that lands, a timed point lookup (operational serving is a latency claim, so it
is measured rather than asserted), and row counts for the synced read path
checked against their Unity Catalog sources.

Connection uses a short-lived OAuth token minted from the endpoint resource
path. No password is stored anywhere, and PGPASSWORD in `.env.example` is left
empty on purpose.

    python -m src.lakebase.provision            # apply schema + verify
    python -m src.lakebase.provision --report   # verify only, no DDL
"""

from __future__ import annotations

import pathlib
import statistics
import sys
import time

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from databricks.sdk import WorkspaceClient  # noqa: E402

import src.config as config  # noqa: E402
from tools.dbsql import sql as uc_sql  # noqa: E402

PROFILE = config.PROFILE
SCHEMA_SQL = pathlib.Path(__file__).with_name("schema.sql")


def connect() -> psycopg.Connection:
    """Open a Postgres connection using a freshly minted OAuth token."""
    w = WorkspaceClient(profile=PROFILE)
    # w.postgres is the Autoscaling service; the credential is minted from the
    # ENDPOINT resource path, not a branch or database path.
    cred = w.postgres.generate_database_credential(endpoint=config.LAKEBASE_ENDPOINT)
    token = cred.token
    if not token:
        raise RuntimeError("no token returned by generate_database_credential")
    user = w.current_user.me().user_name
    return psycopg.connect(
        host=config.LAKEBASE_HOST,
        dbname=config.LAKEBASE_DATABASE,
        user=user,
        password=token,
        sslmode="require",
        connect_timeout=30,
    )


def apply_schema(conn: psycopg.Connection) -> None:
    ddl = SCHEMA_SQL.read_text()
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    print(f"applied {SCHEMA_SQL.name} ({len(ddl)} chars)")


def list_tables(conn: psycopg.Connection) -> None:
    """The psql \\dt equivalent, for both schemas."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT schemaname, tablename, tableowner
            FROM pg_tables
            WHERE schemaname IN ('public', 'allocation')
            ORDER BY schemaname, tablename
            """
        )
        rows = cur.fetchall()
    print(f"\n{'schema':12} {'table':26} owner")
    print("-" * 70)
    for s, t, o in rows:
        print(f"{s:12} {t:26} {o}")
    print(f"({len(rows)} tables)")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT schemaname, indexname, tablename
            FROM pg_indexes
            WHERE schemaname = 'allocation' ORDER BY tablename, indexname
            """
        )
        idx = cur.fetchall()
    print(f"\nindexes on the OLTP schema ({len(idx)}):")
    for _schema, index_name, tbl in idx:
        print(f"  {tbl:26} {index_name}")


def compare_synced_counts(conn: psycopg.Connection) -> None:
    """Row counts in Postgres against the Unity Catalog sources."""
    print(f"\n{'lakebase table':26} {'pg rows':>10} {'uc rows':>10}  {'match':6} uc source")
    print("-" * 100)
    for pg_table, uc_table in config.SYNCED_TABLES.items():
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {config.LAKEBASE_SYNC_SCHEMA}.{pg_table}")
                pg_n = cur.fetchone()[0]
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            print(f"{pg_table:26} {'ERR':>10} {'':>10}  {type(e).__name__}: {str(e)[:40]}")
            continue
        _, rows = uc_sql(f"SELECT COUNT(*) FROM {uc_table}")
        uc_n = int(rows[0][0]) if rows else -1
        print(f"{pg_table:26} {pg_n:>10,} {uc_n:>10,}  "
              f"{'yes' if pg_n == uc_n else 'NO':6} {uc_table}")


def prove_write(conn: psycopg.Connection) -> int:
    """INSERT a decision and SELECT it back. Returns the new decision_id."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM allocation.allocation_decisions")
        before = cur.fetchone()[0]

    # Take a genuinely at-risk combination so the decision row is not invented.
    # Read it from Unity Catalog rather than the synced table: the synced read
    # path is blocked on this workspace (see sync.py) and the OLTP write path
    # must be demonstrable independently of it.
    _, rows = uc_sql(
        f"""
        SELECT sku_id, retailer_code, CAST(projected_stockout_week AS STRING),
               severity_score, units_at_risk, revenue_at_risk_usd
        FROM {config.FCT_STOCKOUT_RISK}
        WHERE risk_tier = 'CRITICAL' ORDER BY severity_score DESC LIMIT 1
        """
    )
    sku, retailer, week, severity, at_risk, revenue = rows[0]
    # the Statement Execution API returns numerics as strings ("2093.0"), which
    # Postgres rejects for an INTEGER column
    at_risk = int(float(at_risk or 0))
    severity = float(severity or 0)
    revenue = float(revenue or 0)
    print(f"source row from {config.FCT_STOCKOUT_RISK}: {sku} / {retailer} / {week}")

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO allocation.allocation_decisions
                (sku_id, retailer_code, week_end_date, recommended_units,
                 planner_units, action, planner_id, rationale_text,
                 agent_trace_id, model_version, severity_score, units_at_risk,
                 revenue_at_risk_usd)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING decision_id, override_delta_units, decided_at
            """,
            (sku, retailer, week, 24, 30, "override", "planner_demo_01",
             "Held back 6 cases from MASS_B to protect the MASS_A line review; "
             "air freight already committed for the following week.",
             "tr-provision-check", 9, severity, at_risk, revenue),
        )
        decision_id, delta, decided_at = cur.fetchone()
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM allocation.allocation_decisions")
        after = cur.fetchone()[0]
        cur.execute(
            """
            SELECT decision_id, sku_id, retailer_code, week_end_date,
                   recommended_units, planner_units, override_delta_units,
                   action, planner_id, agent_trace_id, decided_at
            FROM allocation.allocation_decisions WHERE decision_id = %s
            """,
            (decision_id,),
        )
        row = cur.fetchone()

    print(f"\nrow count before INSERT : {before}")
    print(f"row count after INSERT  : {after}")
    print(f"INSERT returned         : decision_id={decision_id} "
          f"override_delta_units={delta} decided_at={decided_at}")
    print("\nSELECT of the row just written:")
    cols = ["decision_id", "sku_id", "retailer_code", "week_end_date",
            "recommended_units", "planner_units", "override_delta_units",
            "action", "planner_id", "agent_trace_id", "decided_at"]
    for c, v in zip(cols, row, strict=True):
        print(f"  {c:22} {v}")
    return int(decision_id)


def seed_decisions(conn: psycopg.Connection, n: int = 24) -> None:
    """Write a spread of accept / override / defer decisions.

    One row proves the write works; a spread makes the UC round-trip and the
    "are overrides beating the model" question answerable, which is the whole
    reason the decision is captured.
    """
    _, rows = uc_sql(
        f"""
        SELECT sku_id, retailer_code, CAST(projected_stockout_week AS STRING),
               severity_score, units_at_risk, revenue_at_risk_usd
        FROM {config.FCT_STOCKOUT_RISK}
        WHERE risk_tier IN ('CRITICAL','HIGH')
        ORDER BY severity_score DESC LIMIT {n}
        """
    )
    actions = ["accept", "override", "defer"]
    planners = ["planner_a", "planner_b", "planner_c"]
    inserted = 0
    with conn.cursor() as cur:
        for i, (sku, retailer, week, sev, at_risk, revenue) in enumerate(rows):
            at_risk = int(float(at_risk or 0))
            sev = float(sev or 0)
            revenue = float(revenue or 0)
            action = actions[i % 3]
            rec = max(1, int(float(at_risk or 1) * 0.12))
            if action == "accept":
                planner = rec
            elif action == "override":
                planner = max(0, rec + (6 if i % 2 == 0 else -4))
            else:
                planner = 0
            cur.execute(
                """
                INSERT INTO allocation.allocation_decisions
                    (sku_id, retailer_code, week_end_date, recommended_units,
                     planner_units, action, planner_id, rationale_text,
                     agent_trace_id, model_version, severity_score,
                     units_at_risk, revenue_at_risk_usd)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (sku, retailer, week, rec, planner, action,
                 planners[i % 3],
                 {
                     "accept": "Took the model split unchanged.",
                     "override": "Adjusted to protect the line review at the "
                                 "mass account; air freight already committed.",
                     "defer": "Holding until the revised ETA is confirmed.",
                 }[action],
                 f"tr-seed-{i:03d}", 9, sev, at_risk, revenue),
            )
            inserted += 1
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT action, COUNT(*), SUM(planner_units), SUM(recommended_units),
                   SUM(override_delta_units)
            FROM allocation.allocation_decisions GROUP BY action ORDER BY action
        """)
        print(f"\nseeded {inserted} decisions; by action:")
        print(f"  {'action':10} {'rows':>5} {'planner_u':>10} {'recommended_u':>14} "
              f"{'delta_u':>9}")
        for a, c, pu, ru, dd in cur.fetchall():
            print(f"  {a:10} {c:>5} {pu:>10} {ru:>14} {dd:>9}")


def timed_point_lookup(conn: psycopg.Connection, n: int = 60) -> None:
    """Measure the point lookup the app's decision history panel issues.

    Measured against the native OLTP table rather than a synced table, because
    the synced read path is blocked on this workspace. The latency claim this
    supports is the one that matters anyway: the app reads and writes its own
    operational state in single-digit milliseconds.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT sku_id, retailer_code FROM allocation.allocation_decisions
                ORDER BY decided_at DESC LIMIT {n}"""
        )
        keys = cur.fetchall()
    if not keys:
        print("\nno decisions yet, skipping point-lookup timing")
        return

    q = (
        "SELECT decision_id, sku_id, retailer_code, week_end_date, "
        "recommended_units, planner_units, override_delta_units, action "
        "FROM allocation.allocation_decisions "
        "WHERE sku_id = %s AND retailer_code = %s"
    )
    timings: list[float] = []
    with conn.cursor() as cur:
        cur.execute(q, keys[0])  # warm, so the first-call cost is not reported as typical
        cur.fetchall()
        for sku, retailer in keys:
            t0 = time.perf_counter()
            cur.execute(q, (sku, retailer))
            cur.fetchall()
            timings.append((time.perf_counter() - t0) * 1000.0)

    timings.sort()
    print(f"\npoint lookup on allocation.allocation_decisions, {len(timings)} calls")
    print(f"  mean   {statistics.mean(timings):7.2f} ms")
    print(f"  median {statistics.median(timings):7.2f} ms")
    print(f"  p95    {timings[int(0.95 * len(timings)) - 1]:7.2f} ms")
    print(f"  min    {timings[0]:7.2f} ms")
    print(f"  max    {timings[-1]:7.2f} ms")
    print("  NOTE: these are wall-clock round trips from a laptop to us-east-1, so")
    print("  they are dominated by network latency, not by Postgres. The EXPLAIN")
    print("  ANALYZE below reports the actual server-side execution time, which is")
    print("  the number that matters for an app running inside the workspace.")

    with conn.cursor() as cur:
        cur.execute(
            "EXPLAIN ANALYZE SELECT * FROM allocation.allocation_decisions "
            f"WHERE sku_id = '{keys[0][0]}' AND retailer_code = '{keys[0][1]}'"
        )
        plan = [r[0] for r in cur.fetchall()]
    print("\n  query plan:")
    for line in plan:
        print("   ", line)


def main() -> int:
    report_only = "--report" in sys.argv
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_user, current_database()")
            ver, who, db = cur.fetchone()
        print(f"connected   : {db} as {who}")
        print(f"server      : {ver.split(',')[0]}")
        print(f"host        : {config.LAKEBASE_HOST}")
        print(f"endpoint    : {config.LAKEBASE_ENDPOINT}")

        if not report_only:
            apply_schema(conn)
        list_tables(conn)
        compare_synced_counts(conn)
        if not report_only:
            prove_write(conn)
            seed_decisions(conn)
        timed_point_lookup(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
