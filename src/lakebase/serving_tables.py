"""Create the two serving projections that feed the product and KPI synced tables.

Unity Catalog refuses to build an online copy of a table that carries a row filter or a column
mask ("cannot have both row/column security and online materialized views"): the replica would
sit outside the policy. ``gold.dim_product`` has the cost/margin mask and
``gold.agg_fill_rate_summary`` has the retailer row filter, so neither can be synced as-is.

The resolution is a projection that holds nothing the policy exists to protect:

* ``gold.svc_product_lookup``   -- ``dim_product`` WITHOUT ``unit_cost_usd`` / ``gross_margin_pct``,
  so there is nothing left to mask.
* ``gold.svc_fill_rate_summary`` -- ``agg_fill_rate_summary`` as an unfiltered copy. It contains no
  protected column, but it is no longer row-scoped, so **retailer scoping for this read path lives
  in the app's own query** (the authoritative governed path stays Unity Catalog and Genie).

    python -m src.lakebase.serving_tables
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import src.config as config  # noqa: E402
from tools.dbsql import sql  # noqa: E402

G = config.GOLD

PRODUCT = f"""
CREATE OR REPLACE TABLE {G}.svc_product_lookup
COMMENT 'Product lookup for the app read path (Lakebase synced table). dim_product without the
masked cost and margin columns, because Unity Catalog will not replicate a column-masked table.
SYNTHETIC DATA.'
AS SELECT sku_id, product_name, brand, category, license_family, lifecycle_stage, case_pack,
          msrp_usd, wholesale_price_usd, figure_type, material_composition, country_of_origin,
          planned_lifespan_weeks, launch_week_index
   FROM {G}.dim_product
"""

FILL = f"""
CREATE OR REPLACE TABLE {G}.svc_fill_rate_summary
COMMENT 'Fill rate, OTIF and chargeback by retailer x week for the app KPI header (Lakebase
synced table). Unfiltered copy of agg_fill_rate_summary: Unity Catalog will not replicate a
row-filtered table, so retailer scoping is applied in the app query. SYNTHETIC DATA.'
AS SELECT * FROM {G}.agg_fill_rate_summary
"""


def main() -> int:
    for name, ddl in (("svc_product_lookup", PRODUCT), ("svc_fill_rate_summary", FILL)):
        sql(ddl)
        _, rows = sql(f"SELECT COUNT(*) FROM {G}.{name}")
        print(f"  created {G}.{name}: {int(rows[0][0]):,} rows")
    _, cols = sql(f"SELECT column_name FROM {config.CATALOG}.information_schema.columns "
                  f"WHERE table_schema='gold' AND table_name='svc_product_lookup'")
    names = {c[0] for c in cols}
    leaked = {"unit_cost_usd", "gross_margin_pct"} & names
    assert not leaked, f"masked columns leaked into the projection: {leaked}"
    print("  confirmed: no cost or margin column in svc_product_lookup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
