"""Stage D — create the synced tables that form the app's read path.

Four Unity Catalog tables are replicated into Lakebase Postgres so the app can
serve its worklist from a point lookup instead of a warehouse query per page
load.

Two things about this workspace make the naive call fail, and both are worth
knowing before reading the spec below.

**1. The sync pipeline needs an explicit storage catalog.**
The first attempt created all four synced tables successfully and then every
one of them went to ``SYNCED_TABLE_OFFLINE_FAILED``, with the underlying
pipeline reporting::

    [ErrorClass=INVALID_STATE] Metastore storage root URL does not exist.

This metastore uses Default Storage and has no storage root, so the sync
pipeline has nowhere to write its event log and checkpoints. It must be told
explicitly via ``new_pipeline_spec.storage_catalog``, and that has to be a
*regular* UC catalog — it cannot be the Lakebase-backed online catalog, because
a declarative pipeline cannot write its event log into a Postgres-backed schema.
The same missing storage root is what forced an explicit ``--storage-root`` when
this project's catalog was created.

**2. Two of the four sources had to be re-pointed.**
``dim_product`` carries the Stage B column mask and ``agg_fill_rate_summary``
carries the row filter, and Unity Catalog refuses to build an online copy of
either::

    Table ... cannot have both row/column security and online materialized views

That is UC closing a governance hole rather than an obstacle: an online replica
sits outside the policy, so syncing a masked table would leak exactly the
column the mask protects. The resolution is to sync projections that hold no
protected data — ``gold.svc_product_lookup`` omits cost and margin entirely, so
there is nothing left to mask. Retailer scoping for the Lakebase read path
therefore moves into the app's own query; the authoritative governed path stays
Unity Catalog and Genie.

    python -m src.lakebase.sync            # create (or recreate) all four
    python -m src.lakebase.sync --status   # report state only
    python -m src.lakebase.sync --recreate # delete then create
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from databricks.sdk import WorkspaceClient  # noqa: E402

import src.config as config  # noqa: E402

#: Regular UC catalog/schema the sync pipelines write their event log into.
#: Must NOT be the Lakebase online catalog.
STORAGE_CATALOG = config.CATALOG
STORAGE_SCHEMA = "ml"

TERMINAL_OK = "SYNCED_TABLE_ONLINE"
TERMINAL_BAD = ("FAILED", "OFFLINE_FAILED")


def _w() -> WorkspaceClient:
    return WorkspaceClient(profile=config.PROFILE)


def qualified(pg_table: str) -> str:
    return (
        f"{config.LAKEBASE_UC_CATALOG}.{config.LAKEBASE_SYNC_SCHEMA}.{pg_table}"
    )


def spec_for(pg_table: str) -> dict:
    return {
        "branch": config.LAKEBASE_BRANCH,
        "postgres_database": config.LAKEBASE_DATABASE,
        "source_table_full_name": config.SYNCED_TABLES[pg_table],
        "primary_key_columns": list(config.SYNCED_TABLE_KEYS[pg_table]),
        "scheduling_policy": "SNAPSHOT",
        # without this the pipeline falls back to the (absent) metastore
        # storage root and fails with INVALID_STATE
        "new_pipeline_spec": {
            "storage_catalog": STORAGE_CATALOG,
            "storage_schema": STORAGE_SCHEMA,
        },
    }


def delete_all(w: WorkspaceClient) -> None:
    for pg_table in config.SYNCED_TABLES:
        name = f"synced_tables/{qualified(pg_table)}"
        try:
            w.api_client.do("DELETE", f"/api/2.0/{name}")
            print(f"  deleted {pg_table}")
        except Exception as e:  # noqa: BLE001
            print(f"  delete {pg_table}: {type(e).__name__}: {str(e)[:90]}")


def create_all(w: WorkspaceClient) -> None:
    for pg_table in config.SYNCED_TABLES:
        body = {"spec": spec_for(pg_table)}
        try:
            w.api_client.do(
                "POST",
                "/api/2.0/synced_tables",
                query={"synced_table_id": qualified(pg_table)},
                body=body,
            )
            print(f"  created {pg_table} <- {config.SYNCED_TABLES[pg_table]}")
        except Exception as e:  # noqa: BLE001
            print(f"  create {pg_table} FAILED: {type(e).__name__}: {str(e)[:200]}")


def status(w: WorkspaceClient) -> dict[str, str]:
    out: dict[str, str] = {}
    for pg_table in config.SYNCED_TABLES:
        try:
            r = w.api_client.do("GET", f"/api/2.0/synced_tables/{qualified(pg_table)}")
            st = r.get("status") or {}
            out[pg_table] = str(st.get("detailed_state") or "?")
        except Exception as e:  # noqa: BLE001
            out[pg_table] = f"ERR {type(e).__name__}"
    return out


def wait_online(w: WorkspaceClient, timeout_s: int = 2400) -> dict[str, str]:
    deadline = time.time() + timeout_s
    last: dict[str, str] = {}
    while time.time() < deadline:
        last = status(w)
        vals = list(last.values())
        print("  " + "  ".join(f"{k}={v}" for k, v in last.items())[:220])
        if all(v == TERMINAL_OK for v in vals):
            return last
        if any(any(b in v for b in TERMINAL_BAD) for v in vals):
            return last
        time.sleep(30)
    return last


def main() -> int:
    w = _w()
    if "--status" in sys.argv:
        for k, v in status(w).items():
            print(f"  {k:26} {v}")
        return 0

    print(f"storage catalog for sync pipelines: {STORAGE_CATALOG}.{STORAGE_SCHEMA}")
    print(f"target: {config.LAKEBASE_UC_CATALOG}.{config.LAKEBASE_SYNC_SCHEMA}.*")
    print()
    print("deleting any existing synced tables ...")
    delete_all(w)
    print()
    print("creating synced tables ...")
    create_all(w)
    print()
    print("waiting for ONLINE ...")
    final = wait_online(w)
    print()
    ok = sum(1 for v in final.values() if v == TERMINAL_OK)
    for k, v in final.items():
        print(f"  {k:26} {v}")
    print(f"\n{ok} of {len(final)} synced tables ONLINE")
    return 0 if ok == len(final) else 1


if __name__ == "__main__":
    sys.exit(main())
