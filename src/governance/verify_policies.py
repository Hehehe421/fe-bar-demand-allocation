"""Prove the row filter and column mask bind, by querying as each persona.

Asserting what a restricted identity "would" see is not evidence. This signs in
as each persona service principal over OAuth M2M and runs the same query, so
the masked and unmasked results are both real and each is labelled with the
identity that produced it.

The OAuth client secret is minted at runtime, held only in memory, and deleted
before the process exits. It is never written to disk or printed.

    python -m src.governance.verify_policies
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass
from typing import Any

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from databricks.sdk import WorkspaceClient  # noqa: E402

from tools.dbsql import sql, table  # noqa: E402

PROFILE = "vm-summer-classic"
HOST = "https://fevm-stable-classic-pqjg18.cloud.databricks.com"
WAREHOUSE = "a6857db5911f7af1"
CATALOG = "toy_demand_allocation"

PERSONAS = {
    "toy-planner-mass-a": "5d9eb1c3-888e-4acc-8625-4ecedea0fea5",
    "toy-data-scientist": "7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8",
    "toy-finance": "dd70e525-c0aa-4ca5-ade2-29671715871a",
    "toy-app-service": "1c73204c-6b49-4db0-a860-300ba392393e",
}


@dataclass
class PersonaResult:
    name: str
    application_id: str
    current_user: str
    rows: list[list[Any]]
    columns: list[str]
    error: str = ""


def _sp_internal_id(w: WorkspaceClient, app_id: str) -> str | None:
    for sp in w.service_principals.list():
        if sp.application_id == app_id:
            return sp.id
    return None


def run_as_persona(
    w: WorkspaceClient, name: str, app_id: str, queries: dict[str, str]
) -> dict[str, PersonaResult]:
    """Mint a short-lived OAuth secret, run ``queries``, then revoke it."""
    internal = _sp_internal_id(w, app_id)
    if internal is None:
        return {
            k: PersonaResult(name, app_id, "?", [], [], "service principal not found")
            for k in queries
        }

    secret_id: str | None = None
    out: dict[str, PersonaResult] = {}
    try:
        # workspace-level SP OAuth secrets live on the *_proxy service in the SDK
        created = w.service_principal_secrets_proxy.create(
            service_principal_id=internal, lifetime='600s'
        )
        secret_id = created.id
        client_secret = created.secret  # in memory only, never persisted

        tok = requests.post(
            f"{HOST}/oidc/v1/token",
            data={"grant_type": "client_credentials", "scope": "all-apis"},
            auth=(app_id, client_secret),
            timeout=60,
        )
        tok.raise_for_status()
        access_token = tok.json()["access_token"]

        hdr = {"Authorization": f"Bearer {access_token}"}
        for label, q in queries.items():
            try:
                r = requests.post(
                    f"{HOST}/api/2.0/sql/statements",
                    headers=hdr,
                    json={"statement": q, "warehouse_id": WAREHOUSE, "wait_timeout": "50s"},
                    timeout=120,
                )
                r.raise_for_status()
                body = r.json()
                state = body.get("status", {}).get("state")
                if state != "SUCCEEDED":
                    msg = body.get("status", {}).get("error", {}).get("message", state)
                    out[label] = PersonaResult(name, app_id, "?", [], [], str(msg)[:300])
                    continue
                cols = [c["name"] for c in body["manifest"]["schema"]["columns"]]
                rows = body.get("result", {}).get("data_array", []) or []
                who = ""
                if "current_user" in cols:
                    i = cols.index("current_user")
                    who = rows[0][i] if rows else ""
                out[label] = PersonaResult(name, app_id, who, rows, cols)
            except Exception as e:  # noqa: BLE001
                out[label] = PersonaResult(name, app_id, "?", [], [], str(e)[:300])
    except Exception as e:  # noqa: BLE001
        for label in queries:
            out.setdefault(
                label, PersonaResult(name, app_id, "?", [], [], f"auth failed: {str(e)[:250]}")
            )
    finally:
        if secret_id:
            try:
                w.service_principal_secrets_proxy.delete(
                    service_principal_id=internal, secret_id=secret_id
                )
            except Exception:  # noqa: BLE001
                pass
    return out


#: Column mask probe: cost and margin on a handful of SKUs.
Q_MASK = f"""
SELECT current_user() AS current_user, sku_id, brand,
       unit_cost_usd, gross_margin_pct, wholesale_price_usd
FROM {CATALOG}.gold.dim_product
WHERE sku_id IN ('SKU-00001','SKU-00002','SKU-00003')
ORDER BY sku_id
""".strip()

#: Row filter probe: which retailers are visible at all.
Q_FILTER = f"""
SELECT current_user() AS current_user, retailer_code,
       COUNT(*) AS rows_visible, SUM(pos_units) AS pos_units
FROM {CATALOG}.gold.fct_demand_weekly
GROUP BY ALL ORDER BY retailer_code
""".strip()


def fmt(res: PersonaResult) -> str:
    if res.error:
        return f"  ERROR: {res.error}"
    if not res.rows:
        return "  (no rows returned -- row filter removed everything)"
    w = [len(c) for c in res.columns]
    body = [["NULL" if v is None else str(v) for v in r] for r in res.rows]
    for r in body:
        for i, v in enumerate(r):
            w[i] = max(w[i], len(v))
    lines = ["  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(res.columns))]
    lines.append("  " + "  ".join("-" * x for x in w))
    lines += ["  " + "  ".join(v.ljust(w[i]) for i, v in enumerate(r)) for r in body]
    return "\n".join(lines)


def main() -> int:
    w = WorkspaceClient(profile=PROFILE)

    # make sure each persona can reach the warehouse and the catalog at all
    for app_id in PERSONAS.values():
        try:
            from databricks.sdk.service.sql import (
                WarehouseAccessControlRequest,
                WarehousePermissionLevel,
            )

            w.warehouses.update_permissions(
                warehouse_id=WAREHOUSE,
                access_control_list=[
                    WarehouseAccessControlRequest(
                        service_principal_name=app_id,
                        permission_level=WarehousePermissionLevel.CAN_USE,
                    )
                ],
            )
        except Exception as e:  # noqa: BLE001
            print(f"warning: could not grant warehouse CAN_USE to {app_id}: {str(e)[:160]}")

    report: list[str] = []
    a = report.append

    a("### My own identity (catalog owner, workspace admin)")
    a("")
    a("```text")
    a(table("SELECT current_user() AS current_user, is_account_group_member('admins') AS in_admins"))
    a("```")
    a("")
    a("#### Column mask as the catalog owner")
    a("")
    a("```text")
    a(table(Q_MASK))
    a("```")
    a("")
    a("#### Row filter as the catalog owner")
    a("")
    a("```text")
    a(table(Q_FILTER))
    a("```")
    a("")

    for name, app_id in PERSONAS.items():
        a(f"### Persona `{name}` (service principal `{app_id}`)")
        a("")
        res = run_as_persona(w, name, app_id, {"mask": Q_MASK, "filter": Q_FILTER})
        a("#### Column mask on `unit_cost_usd` / `gross_margin_pct`")
        a("")
        a("```text")
        a(fmt(res["mask"]))
        a("```")
        a("")
        a("#### Row filter on `retailer_code`")
        a("")
        a("```text")
        a(fmt(res["filter"]))
        a("```")
        a("")

    text = "\n".join(report)
    dest = pathlib.Path("evidence/_policy_verification.md")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(text)
    print(text)
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
