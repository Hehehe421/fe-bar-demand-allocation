"""Create, grant and deploy the Allocation Command Center. Idempotent.

    python app/deploy.py            # create/update the app, grant, upload, deploy
    python app/deploy.py --grants   # re-apply grants only (needed after a re-sync)
    python app/deploy.py --render   # write app/app.yaml from src/config.py and stop

Resources are declared on the app so nothing is hardcoded in code: the platform
injects the warehouse id, endpoint names and Genie space id through ``valueFrom``.
The app runs as its own service principal, which this script grants the minimum:

* Unity Catalog: USE on the catalog and two schemas, SELECT on ``gold`` and on the
  one feature table. The row filter leaves this principal unrestricted and the column
  mask returns NULL for cost and margin, which is the behaviour wanted.
* Postgres: SELECT on the two synced tables, SELECT/INSERT on the two OLTP tables.
  **Synced tables are dropped and recreated by ``src/lakebase/sync.py``, which discards
  their grants, so re-run ``--grants`` after any re-sync.**
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.apps import (  # noqa: E402
    App,
    AppDeployment,
    AppResource,
    AppResourceGenieSpace,
    AppResourceGenieSpaceGenieSpacePermission,
    AppResourcePostgres,
    AppResourcePostgresPostgresPermission,
    AppResourceServingEndpoint,
    AppResourceServingEndpointServingEndpointPermission,
    AppResourceSqlWarehouse,
    AppResourceSqlWarehouseSqlWarehousePermission,
)

import src.config as config  # noqa: E402
from src.lakebase.provision import connect  # noqa: E402
from tools.dbsql import sql  # noqa: E402

APP_DIR = ROOT / "app"
APP_NAME = config.APP_NAME
FILES = ("app.py", "backend.py", "settings.py", "requirements.txt")


def genie_space_id() -> str:
    import json

    return json.loads((ROOT / "evidence/metrics/genie_space.json").read_text())["space_id"]


def render_app_yaml() -> str:
    return f"""# Rendered by app/deploy.py from src/config.py. Edit the config, not this file.
command: ["python", "app.py"]

env:
  # resources: the platform injects these, nothing is hardcoded
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse
  - name: AGENT_ENDPOINT
    valueFrom: agent-endpoint
  - name: FORECASTER_ENDPOINT
    valueFrom: forecaster-endpoint
  - name: GENIE_SPACE_ID
    valueFrom: genie-space
  # Lakebase coordinates (not secrets; the credential is minted at runtime)
  - name: PG_HOST
    value: "{config.LAKEBASE_HOST}"
  - name: PG_DATABASE
    value: "{config.LAKEBASE_DATABASE}"
  - name: PG_ENDPOINT
    value: "{config.LAKEBASE_ENDPOINT}"
  - name: UC_CATALOG
    value: "{config.CATALOG}"
  # wake the scale-to-zero forecaster at start-up (it takes minutes to come up)
  - name: WARMUP
    value: "1"
"""


def resources() -> list[AppResource]:
    wh, ep, gen = (AppResourceSqlWarehouseSqlWarehousePermission,
                   AppResourceServingEndpointServingEndpointPermission,
                   AppResourceGenieSpaceGenieSpacePermission)
    return [
        AppResource(name="sql-warehouse", description="KPIs and the what-if feature row",
                    sql_warehouse=AppResourceSqlWarehouse(id=config.WAREHOUSE_ID,
                                                          permission=wh.CAN_USE)),
        AppResource(name="agent-endpoint", description="Allocation agent",
                    serving_endpoint=AppResourceServingEndpoint(name=config.AGENT_ENDPOINT_NAME,
                                                                permission=ep.CAN_QUERY)),
        AppResource(name="forecaster-endpoint", description="Real-time demand forecaster",
                    serving_endpoint=AppResourceServingEndpoint(name=config.FORECASTER_ENDPOINT,
                                                                permission=ep.CAN_QUERY)),
        AppResource(name="genie-space", description="Ask the data",
                    genie_space=AppResourceGenieSpace(name=config.GENIE_SPACE_NAME,
                                                      space_id=genie_space_id(),
                                                      permission=gen.CAN_RUN)),
        AppResource(name="lakebase", description="Worklist, forecast and decision capture",
                    postgres=AppResourcePostgres(
                        branch=config.LAKEBASE_BRANCH, database=config.LAKEBASE_DB_RESOURCE,
                        permission=AppResourcePostgresPostgresPermission.CAN_CONNECT_AND_CREATE)),
    ]


def ensure_app(w: WorkspaceClient) -> App:
    existing = {a.name for a in w.apps.list()}
    spec = App(name=APP_NAME, resources=resources(),
               description="Planner worklist, agent, and decision capture. Synthetic data.")
    if APP_NAME in existing:
        print(f"updating app {APP_NAME}")
        return w.apps.update(APP_NAME, spec)
    print(f"creating app {APP_NAME} (compute start can take a few minutes)")
    return w.apps.create_and_wait(spec)


def grant_uc(sp: str) -> None:
    c = config.CATALOG
    stmts = [
        f"GRANT USE CATALOG ON CATALOG {c} TO `{sp}`",
        f"GRANT USE SCHEMA ON SCHEMA {c}.gold TO `{sp}`",
        f"GRANT SELECT ON SCHEMA {c}.gold TO `{sp}`",
        f"GRANT USE SCHEMA ON SCHEMA {c}.ml TO `{sp}`",
        f"GRANT SELECT ON TABLE {config.FEATURE_TABLE} TO `{sp}`",
    ]
    for s in stmts:
        sql(s)
        print("  ok:", s)


def grant_pg(sp: str) -> None:
    role = f'"{sp}"'
    stmts = [
        f"GRANT USAGE ON SCHEMA public, allocation TO {role}",
        *[f"GRANT SELECT ON public.{t} TO {role}" for t in config.SYNCED_TABLES],
        *[f"GRANT SELECT, INSERT ON allocation.{t} TO {role}" for t in config.OLTP_TABLES],
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA allocation TO {role}",
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (sp,))
            if cur.fetchone() is None:
                raise RuntimeError(
                    f"no Postgres role {sp!r}: the app's Lakebase resource has not created it yet")
        for s in stmts:
            with conn.cursor() as cur:
                cur.execute(s)
            print("  ok:", s)
        conn.commit()


def build_dir() -> pathlib.Path:
    out = pathlib.Path(tempfile.mkdtemp(prefix="allocation-app-"))
    for f in FILES:
        shutil.copy(APP_DIR / f, out / f)
    shutil.copytree(APP_DIR / "assets", out / "assets")
    (out / "app.yaml").write_text(render_app_yaml())
    return out


def main() -> int:
    if "--render" in sys.argv:
        (APP_DIR / "app.yaml").write_text(render_app_yaml())
        print("wrote app/app.yaml")
        return 0
    w = WorkspaceClient(profile=config.PROFILE)
    app = ensure_app(w)
    app = w.apps.get(APP_NAME)
    sp = app.service_principal_client_id
    print(f"app service principal: {sp}")

    print("\nUnity Catalog grants:")
    grant_uc(sp)
    print("\nPostgres grants:")
    try:
        grant_pg(sp)
    except (RuntimeError, psycopg.Error) as e:
        print(f"  FAILED: {e}")
        return 1
    if "--grants" in sys.argv:
        return 0

    me = w.current_user.me().user_name
    target = f"/Workspace/Users/{me}/apps/{APP_NAME}"
    src = build_dir()
    print(f"\nuploading {src} -> {target}")
    subprocess.run(["databricks", "workspace", "delete", target, "--recursive",
                    "--profile", config.PROFILE], capture_output=True)
    subprocess.run(["databricks", "workspace", "mkdirs", target, "--profile", config.PROFILE],
                   check=True)
    subprocess.run(["databricks", "workspace", "import-dir", str(src), target, "--overwrite",
                    "--profile", config.PROFILE], check=True, capture_output=True)
    print("deploying ...")
    d = w.apps.deploy_and_wait(APP_NAME, AppDeployment(source_code_path=target))
    print("deployment:", d.status.state if d.status else "?", "-", d.status.message if d.status else "")
    print("url:", w.apps.get(APP_NAME).url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
