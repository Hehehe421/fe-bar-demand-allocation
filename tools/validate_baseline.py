"""Measure the KPI baseline directly from a generated landing zone.

Used during generator calibration and as an independent local cross-check of
the Stage B baseline that is computed on the platform. It reads the emitted
flat files, applies the same case-to-each normalisation that Silver applies,
and reports every KPI in the project's frame.

Unit handling matters: ``ETAIL_C`` reports POS and on-hand in CASES while
``true_demand_units`` is always in eaches. Comparing them without converting
makes every ETAIL_C row look like a stockout, which silently triples the
apparent stockout rate.

    python tools/validate_baseline.py /tmp/tda_smoke
"""

from __future__ import annotations

import glob
import pathlib
import sys

import pandas as pd

CASE_REPORTING_RETAILERS = ("ETAIL_C",)


def _read(landing: pathlib.Path, source: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(landing / source / "*.csv")))
    if not files:
        raise FileNotFoundError(f"no csv files under {landing / source}")
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def normalise_pos(pos: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Silver-equivalent cleanup: drop invalid rows, dedupe, convert cases."""
    n_raw = len(pos)
    # referential integrity against the item master
    pos = pos[pos.sku_id.isin(set(products.sku_id))].copy()
    n_orphan = n_raw - len(pos)
    # negative quantities are invalid
    pos = pos[pos.pos_units >= 0].copy()
    # exact duplicates collapse
    key = ["sku_id", "retailer_code", "dc_code", "week_end_date"]
    n_pre = len(pos)
    pos = pos.drop_duplicates(subset=key, keep="first").copy()
    n_dupe = n_pre - len(pos)

    pos = pos.merge(products[["sku_id", "case_pack"]], on="sku_id", how="left")
    is_case = pos.retailer_code.isin(CASE_REPORTING_RETAILERS)
    pos["pos_units_each"] = pos.pos_units.where(~is_case, pos.pos_units * pos.case_pack)
    pos["on_hand_units_each"] = pos.on_hand_units.where(
        ~is_case, pos.on_hand_units * pos.case_pack
    )
    pos.attrs["dropped_orphan"] = n_orphan
    pos.attrs["dropped_duplicate"] = n_dupe
    return pos


def measure(landing: pathlib.Path) -> pd.DataFrame:
    products = _read(landing, "product_master")
    pos = normalise_pos(_read(landing, "pos_weekly"), products)
    orders = _read(landing, "orders")
    orders = orders[orders.requested_units > 0].copy()
    inv = _read(landing, "inventory_snapshots")
    asn = _read(landing, "inbound_shipments")
    mdn = _read(landing, "markdown_clearance")

    rows: list[dict[str, object]] = []

    def add(kpi: str, value: float, unit: str, basis: str) -> None:
        rows.append({"kpi": kpi, "value": round(float(value), 4), "unit": unit, "basis": basis})

    # ---- service -------------------------------------------------------
    add(
        "retailer_fill_rate_requested",
        orders.shipped_units.sum() / orders.requested_units.sum() * 100,
        "pct",
        "shipped units / requested units, all PO lines",
    )
    add(
        "retailer_fill_rate_confirmed",
        orders.shipped_units.sum() / orders.confirmed_units.sum() * 100,
        "pct",
        "shipped units / confirmed units, all PO lines",
    )
    add(
        "order_acceptance_rate",
        orders.confirmed_units.sum() / orders.requested_units.sum() * 100,
        "pct",
        "confirmed units / requested units",
    )

    on_time = pd.to_datetime(orders.actual_ship_date) <= pd.to_datetime(
        orders.requested_ship_date
    ) + pd.Timedelta(days=2)
    in_full = orders.shipped_units >= orders.requested_units
    add("on_time_pct", on_time.mean() * 100, "pct", "ship date within 2-day grace of request")
    add("in_full_pct", in_full.mean() * 100, "pct", "shipped >= requested on the PO line")
    add("otif_pct", (on_time & in_full).mean() * 100, "pct", "on-time AND in-full PO lines")

    # ---- demand loss ---------------------------------------------------
    act = pos[pos.true_demand_units > 0]
    short = act.true_demand_units > act.pos_units_each
    add(
        "stockout_week_incidence",
        short.mean() * 100,
        "pct",
        "share of active SKU x retailer x DC x week cells not fully served",
    )
    add(
        "lost_units_pct_of_demand",
        (1 - act.pos_units_each.sum() / act.true_demand_units.sum()) * 100,
        "pct",
        "unserved consumer demand as share of true demand",
    )
    add(
        "stockout_weeks_total",
        int(short.sum()),
        "count",
        "count of SKU x retailer x DC weeks not fully served",
    )

    # ---- financial -----------------------------------------------------
    invoiced = (orders.shipped_units * orders.wholesale_price_usd).sum()
    add(
        "compliance_chargeback_pct",
        orders.chargeback_usd.sum() / invoiced * 100,
        "pct",
        "chargeback $ / invoiced wholesale $",
    )
    add("invoiced_wholesale_usd", invoiced, "usd", "shipped units x wholesale price")

    # excess: residual on-hand on EOL/DECLINE items is what clears at markdown
    latest = inv[inv.snapshot_date == inv.snapshot_date.max()]
    latest = latest.merge(
        products[["sku_id", "lifecycle_stage", "unit_cost_usd", "wholesale_price_usd"]],
        on="sku_id",
        how="left",
    )
    eol = latest[latest.lifecycle_stage.isin(["EOL", "DECLINE"])]
    excess_at_cost = (eol.on_hand_units * eol.unit_cost_usd).sum()
    add(
        "excess_eol_units",
        eol.on_hand_units.sum(),
        "units",
        "on-hand units on EOL/DECLINE items at the latest snapshot",
    )
    add(
        "markdown_exposure_pct_of_invoiced",
        excess_at_cost / invoiced * 100,
        "pct",
        "EOL/DECLINE on-hand at cost / invoiced wholesale $",
    )

    # ---- expedite freight premium (cost of chasing spikes late) --------
    air = asn[asn.transport_mode == "AIR"]
    add("air_expedite_units", air.shipped_units.sum(), "units",
        "units air-freighted to chase detected demand surges")
    add("air_freight_usd", air.freight_cost_usd.sum(), "usd",
        "air freight spend on expedites")
    add("air_freight_pct_of_invoiced", air.freight_cost_usd.sum() / invoiced * 100, "pct",
        "air freight spend / invoiced wholesale $")
    add("expedite_share_of_inbound_units",
        air.shipped_units.sum() / max(asn.shipped_units.sum(), 1) * 100, "pct",
        "air units / all inbound units")

    # ---- realised markdown --------------------------------------------
    add("markdown_cleared_units", mdn.cleared_units.sum() if len(mdn) else 0, "units",
        "units liquidated at close-out")
    add("markdown_loss_usd", mdn.markdown_loss_usd.sum() if len(mdn) else 0, "usd",
        "wholesale margin forgone on close-out units")
    add("markdown_loss_pct_of_invoiced",
        (mdn.markdown_loss_usd.sum() if len(mdn) else 0) / invoiced * 100, "pct",
        "markdown loss / invoiced wholesale $")

    # ---- inventory efficiency -----------------------------------------
    recent_weekly = (
        pos.groupby("week_end_date").pos_units_each.sum().sort_index().tail(13).mean()
    )
    total_oh = latest.on_hand_units.sum()
    add(
        "weeks_of_supply",
        total_oh / max(recent_weekly, 1),
        "weeks",
        "latest total on-hand / mean weekly POS over last 13 weeks",
    )
    add(
        "inventory_turns_annual",
        52.0 / max(total_oh / max(recent_weekly, 1), 0.1),
        "turns",
        "52 / weeks of supply",
    )

    # ---- data quality (what Stage A expectations will catch) -----------
    add("dq_dropped_orphan_sku_rows", pos.attrs["dropped_orphan"], "count",
        "pos_weekly rows referencing a SKU absent from product_master")
    add("dq_dropped_duplicate_rows", pos.attrs["dropped_duplicate"], "count",
        "exact duplicate pos_weekly grain keys collapsed")

    return pd.DataFrame(rows)


def main() -> int:
    landing = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tda_smoke")
    df = measure(landing)
    with pd.option_context("display.width", 200, "display.max_colwidth", 60):
        print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
