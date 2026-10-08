"""Seeded, deterministic generator for every byte of data in this build.

ALL DATA PRODUCED HERE IS SYNTHETIC. No real, customer, or scraped data is used
anywhere in this repository. Re-running with the same ``seed`` reproduces the
landing zone exactly.

What it models
--------------
A licensed toy and collectibles manufacturer: collectible plush and licensed
action figures, sold wholesale B2B into three mass/e-tail accounts, shipped from
four domestic distribution centres, manufactured offshore on 60-110 day inbound
lead times.

The simulation is deliberately built so that a *realistic baseline emerges* from
the mechanics rather than being asserted:

* Consumer demand is spiky. ``FILM_TIE_IN`` items get a release-window spike;
  ``SOCIAL_TREND`` items get a sharp viral spike at random timing that then
  collapses; ``EVERGREEN`` items are stable with a Q4 holiday peak.
* Replenishment is a trailing-average order-up-to policy placed against a
  9-16 week offshore lead time. A spike that develops in three weeks therefore
  *cannot* be covered by inbound supply. Stockouts, fill-rate misses and OTIF
  failures fall out of that timing mismatch, which is exactly the business
  problem this prototype addresses.
* End-of-life items over-ordered ahead of a collapse leave excess that clears at
  markdown.

Deliberate messiness is injected so the Stage A data-quality expectations have
real violations to catch: late-arriving weeks, duplicate rows, null on-hand,
negative quantities, orphan SKU references, and one retailer (``ETAIL_C``)
reporting POS in cases rather than eaches.

Outputs (CSV, partitioned into several files per source so Auto Loader has a
realistic multi-file stream; PDFs and PNGs for the unstructured sources):

    <landing>/product_master/*.csv
    <landing>/pos_weekly/*.csv
    <landing>/orders/*.csv
    <landing>/inventory_snapshots/*.csv
    <landing>/inbound_shipments/*.csv
    <landing>/supplier_docs/*.pdf
    <landing>/product_images/*.png
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Generation parameters — every rate here is a knob, and every one is recorded
# into _manifest.json next to the data so the run is self-documenting.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GenConfig:
    seed: int = 20260408
    n_skus: int = 2400
    history_weeks: int = 156
    #: Last complete retail week (a Saturday).
    week_end_anchor: dt.date = dt.date(2026, 9, 26)
    #: Days of daily inventory snapshot retention.
    snapshot_days: int = 180

    # offshore inbound lead time, in days
    lead_time_days_min: int = 60
    lead_time_days_max: int = 110

    # Replenishment policy. The order-up-to target must cover the replenishment
    # lead time plus the review period plus safety stock -- a target of "6 weeks
    # of demand" against a 9-16 week lead time is structurally short and starves
    # the network. Safety weeks are what the planner actually tunes.
    safety_weeks: float = 3.0
    reorder_every_weeks: int = 2

    # --- deliberate messiness rates (Stage A expectations catch these) ---
    dup_row_rate: float = 0.006
    late_week_rate: float = 0.02
    null_onhand_rate: float = 0.015
    negative_qty_rate: float = 0.002
    orphan_sku_rate: float = 0.0015

    # --- failure-mode injection (gives the baseline genuine headroom) ---
    #: PO lines shipped after the retailer's requested date.
    otif_date_miss_rate: float = 0.075
    #: PO lines shipped short of the confirmed quantity.
    otif_short_ship_rate: float = 0.05
    #: Share of OTIF misses that attract a compliance chargeback.
    chargeback_rate_on_miss: float = 0.80
    #: Planner overshoot at end of life; this is what later clears at markdown.
    eol_markdown_rate: float = 0.22
    #: Weekly rate at which post-EOL residual stock is liquidated.
    eol_clearance_rate: float = 0.30
    #: Share of a detected surge that can actually be air-freighted.
    expedite_coverage: float = 0.60
    #: Freight cost per unit, ocean vs air.
    ocean_freight_usd_per_unit: float = 0.28
    air_freight_usd_per_unit: float = 1.95
    #: Supplier ships short of the placed PO.
    supplier_short_rate: float = 0.08
    #: Supplier ETA slips past the original commit.
    supplier_slip_rate: float = 0.25

    n_supplier_docs: int = 12
    n_product_images: int = 20


CFG = GenConfig()

BRANDS = ("Snugglewick", "Titan Squad", "Glowberry Pets")
BRAND_CATEGORY = {
    "Snugglewick": "PLUSH",
    "Titan Squad": "ACTION_FIGURE",
    "Glowberry Pets": "PLUSH",
}
BRAND_WEIGHTS = (0.40, 0.35, 0.25)

LICENSE_FAMILIES = ("EVERGREEN", "FILM_TIE_IN", "SOCIAL_TREND")
LICENSE_WEIGHTS = (0.45, 0.30, 0.25)

DCS = ("DC_EAST", "DC_CENTRAL", "DC_WEST", "DC_SOUTH")
RETAILERS = ("MASS_A", "MASS_B", "ETAIL_C")
RETAILER_LABEL = {
    "MASS_A": "Mass Retail Account A",
    "MASS_B": "Mass Retail Account B",
    "ETAIL_C": "E-tail Account C",
}
#: Contractual fill-rate commitment per retailer; drives allocation priority.
RETAILER_COMMITMENT = {"MASS_A": 0.97, "MASS_B": 0.95, "ETAIL_C": 0.93}
#: ETAIL_C reports POS in CASES, not eaches. Silver must normalise this.
RETAILER_REPORTS_CASES = {"MASS_A": False, "MASS_B": False, "ETAIL_C": True}
RETAILER_DCS = {
    "MASS_A": DCS,
    "MASS_B": DCS,
    "ETAIL_C": ("DC_EAST", "DC_WEST"),
}

MATERIALS = (
    "80% polyester fibre fill, 20% polyester knit shell",
    "100% polyester plush pile, PP pellet weighting",
    "ABS body, PVC-free TPE joints, water-based paint",
    "ABS/polycarbonate blend, zinc-alloy articulation pins",
    "Recycled PET plush shell, polyester fill",
)
FIGURE_TYPES = ("HUMANOID", "NON_HUMANOID", "CREATURE", "VEHICLE")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _week_ends(cfg: GenConfig) -> list[dt.date]:
    """The ``history_weeks`` Saturdays ending at ``week_end_anchor``."""
    return [
        cfg.week_end_anchor - dt.timedelta(weeks=cfg.history_weeks - 1 - i)
        for i in range(cfg.history_weeks)
    ]


def _holiday_multiplier(week_ends: list[dt.date]) -> np.ndarray:
    """Toy-industry seasonality: steep Q4 peak, January trough."""
    woy = np.array([d.isocalendar().week for d in week_ends], dtype=float)
    # peak around week 47 (late November), secondary bump at week 25
    q4 = 1.0 + 1.9 * np.exp(-0.5 * ((woy - 47.0) / 4.0) ** 2)
    summer = 1.0 + 0.25 * np.exp(-0.5 * ((woy - 25.0) / 5.0) ** 2)
    january = 1.0 - 0.30 * np.exp(-0.5 * ((woy - 3.0) / 3.0) ** 2)
    return q4 * summer * january


def _lifecycle_curve(n_weeks: int, age: np.ndarray, lifespan: np.ndarray) -> np.ndarray:
    """Ramp-in over ~5 weeks, plateau, then decay to end of life.

    ``age`` and ``lifespan`` are (n_items, 1) and broadcast over weeks.
    """
    frac = np.clip(age / np.maximum(lifespan, 1.0), 0.0, None)
    ramp = np.clip(age / 5.0, 0.0, 1.0)
    decay = np.where(frac <= 0.55, 1.0, np.exp(-3.1 * (frac - 0.55)))
    alive = (age >= 0) & (frac <= 1.25)
    return ramp * decay * alive


# ---------------------------------------------------------------------------
# product master
# ---------------------------------------------------------------------------


def build_products(cfg: GenConfig, rng: np.random.Generator) -> pd.DataFrame:
    n = cfg.n_skus
    brand = rng.choice(BRANDS, size=n, p=BRAND_WEIGHTS)
    licence = rng.choice(LICENSE_FAMILIES, size=n, p=LICENSE_WEIGHTS)
    category = np.array([BRAND_CATEGORY[b] for b in brand])

    # lifespan by licence family: evergreen runs for years, trends burn out
    lifespan = np.where(
        licence == "EVERGREEN",
        rng.integers(140, 280, n),
        np.where(licence == "FILM_TIE_IN", rng.integers(40, 70, n), rng.integers(24, 42, n)),
    ).astype(float)

    # Position each SKU in its own lifecycle directly, rather than sampling a
    # launch date independently of lifespan. Sampling launch uniformly over a
    # wide window drives short-lived trend items almost entirely into EOL,
    # because most of that window is already past their death. Choosing the
    # life fraction at the end of the history window instead gives direct
    # control over the lifecycle mix and keeps every stage populated.
    life_fraction_at_end = rng.uniform(-0.03, 1.12, n)
    launch_week = np.round(cfg.history_weeks - 1 - life_fraction_at_end * lifespan)

    case_pack = rng.choice([4, 6, 8, 12, 24], size=n, p=[0.15, 0.3, 0.25, 0.22, 0.08])
    unit_cost = np.where(
        category == "PLUSH",
        rng.uniform(2.1, 7.4, n),
        rng.uniform(3.4, 12.8, n),
    ).round(2)
    msrp = (unit_cost * rng.uniform(2.6, 3.9, n)).round(2)
    wholesale_price = (msrp * rng.uniform(0.44, 0.52, n)).round(2)

    # base weekly rate of sale, heavy-tailed
    base_rate = np.exp(rng.normal(3.5, 0.95, n))

    age_now = cfg.history_weeks - 1 - launch_week
    frac_now = age_now / lifespan
    stage = np.where(
        age_now < 0,
        "LAUNCH",
        np.where(
            frac_now > 1.0,
            "EOL",
            np.where(
                frac_now > 0.78,
                "DECLINE",
                np.where(age_now <= 6, "LAUNCH", np.where(frac_now < 0.35, "GROWTH", "MATURE")),
            ),
        ),
    )

    df = pd.DataFrame(
        {
            "sku_id": [f"SKU-{i:05d}" for i in range(1, n + 1)],
            "product_name": [
                f"{b} {t} {rng.integers(100, 999)}"
                for b, t in zip(brand, rng.choice(
                    ("Plushie", "Figure", "Collector Set", "Mini", "Deluxe", "Carry Pack"), n),
                    strict=True)
            ],
            "brand": brand,
            "category": category,
            "license_family": licence,
            "lifecycle_stage": stage,
            "case_pack": case_pack,
            "unit_cost_usd": unit_cost,
            "msrp_usd": msrp,
            "wholesale_price_usd": wholesale_price,
            "launch_week_index": launch_week.astype(int),
            "planned_lifespan_weeks": lifespan.astype(int),
            "base_weekly_rate": base_rate.round(3),
            "figure_type": np.where(
                category == "ACTION_FIGURE",
                rng.choice(FIGURE_TYPES, n, p=(0.45, 0.2, 0.25, 0.1)),
                rng.choice(("NON_HUMANOID", "CREATURE"), n, p=(0.5, 0.5)),
            ),
            "material_composition": rng.choice(MATERIALS, n),
            "country_of_origin": rng.choice(("VN", "CN", "ID"), n, p=(0.45, 0.4, 0.15)),
        }
    )
    df["gross_margin_pct"] = (
        (df.wholesale_price_usd - df.unit_cost_usd) / df.wholesale_price_usd
    ).round(4)
    return df


# ---------------------------------------------------------------------------
# demand simulation
# ---------------------------------------------------------------------------


def build_demand(
    cfg: GenConfig, rng: np.random.Generator, products: pd.DataFrame
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, list[tuple[int, str, str]]]:
    """Simulate true consumer demand per (sku, retailer, dc, week).

    Returns the triplet index, the demand matrix, the promo matrix, and the
    triplet key list.
    """
    n_weeks = cfg.history_weeks
    week_ends = _week_ends(cfg)
    holiday = _holiday_multiplier(week_ends)[None, :]

    n = len(products)
    licence = products.license_family.to_numpy()
    launch = products.launch_week_index.to_numpy(dtype=float)[:, None]
    lifespan = products.planned_lifespan_weeks.to_numpy(dtype=float)[:, None]
    base = products.base_weekly_rate.to_numpy(dtype=float)[:, None]

    weeks = np.arange(n_weeks, dtype=float)[None, :]
    age = weeks - launch
    life = _lifecycle_curve(n_weeks, age, lifespan)

    # ---- licence-driven spikes -------------------------------------------
    spike = np.ones((n, n_weeks))

    film = licence == "FILM_TIE_IN"
    if film.any():
        idx = np.flatnonzero(film)
        # release lands a few weeks after launch; broad 4-6 week window
        rel = launch[idx, 0] + rng.integers(2, 10, idx.size)
        amp = rng.uniform(2.8, 7.5, idx.size)
        width = rng.uniform(2.6, 4.4, idx.size)
        spike[idx] += amp[:, None] * np.exp(
            -0.5 * ((weeks - rel[:, None]) / width[:, None]) ** 2
        )

    trend = licence == "SOCIAL_TREND"
    if trend.any():
        idx = np.flatnonzero(trend)
        # viral moment at random point in the item's life; very sharp, then collapse
        onset = launch[idx, 0] + rng.uniform(0.15, 0.6, idx.size) * lifespan[idx, 0]
        amp = rng.uniform(5.0, 14.0, idx.size)
        rise = rng.uniform(1.1, 2.0, idx.size)
        fall = rng.uniform(2.0, 3.6, idx.size)
        d = weeks - onset[:, None]
        up = np.exp(-0.5 * (d / rise[:, None]) ** 2)
        down = np.exp(-0.5 * (d / fall[:, None]) ** 2)
        spike[idx] += amp[:, None] * np.where(d <= 0, up, down)
        # post-viral collapse to a fraction of pre-spike baseline
        collapsed = (d > 3.0 * fall[:, None]).astype(float)
        spike[idx] *= 1.0 - 0.72 * collapsed

    promo = (rng.random((n, n_weeks)) < 0.055).astype(float)
    promo_lift = 1.0 + promo * rng.uniform(0.30, 0.95, (n, n_weeks))

    intensity = base * life * holiday * spike * promo_lift

    # ---- allocate SKUs to retailer/DC triplets ---------------------------
    triplets: list[tuple[int, str, str]] = []
    rows_sku: list[int] = []
    shares: list[float] = []
    for i in range(n):
        k = rng.choice((1, 2, 3), p=(0.22, 0.46, 0.32))
        rsel = rng.choice(len(RETAILERS), size=k, replace=False)
        rshare = rng.dirichlet(np.full(k, 2.2))
        for ri, rs in zip(rsel, rshare, strict=True):
            r = RETAILERS[ri]
            avail = RETAILER_DCS[r]
            m = int(rng.choice((1, 2, 3), p=(0.3, 0.45, 0.25)))
            m = min(m, len(avail))
            dsel = rng.choice(len(avail), size=m, replace=False)
            dshare = rng.dirichlet(np.full(m, 2.5))
            for di, ds in zip(dsel, dshare, strict=True):
                triplets.append((i, r, avail[di]))
                rows_sku.append(i)
                shares.append(float(rs * ds))

    rows_sku_arr = np.array(rows_sku)
    share_arr = np.array(shares)[:, None]
    demand_mean = intensity[rows_sku_arr] * share_arr
    # Poisson-ish count noise with overdispersion
    demand = rng.poisson(np.clip(demand_mean, 0, None) * rng.uniform(0.85, 1.15,
                                                                     demand_mean.shape))
    promo_tri = promo[rows_sku_arr]

    index = pd.DataFrame(
        {
            "row": np.arange(len(triplets)),
            "sku_idx": rows_sku_arr,
            "retailer_code": [t[1] for t in triplets],
            "dc_code": [t[2] for t in triplets],
        }
    )
    return index, demand.astype(float), promo_tri, triplets


# ---------------------------------------------------------------------------
# supply simulation: manufacturer DC inventory, inbound, POs
# ---------------------------------------------------------------------------


def simulate_supply(
    cfg: GenConfig,
    rng: np.random.Generator,
    products: pd.DataFrame,
    index: pd.DataFrame,
    demand: np.ndarray,
) -> dict[str, object]:
    """Weekly simulation of manufacturer DC on-hand, inbound receipts and PO flow.

    The key mechanic: replenishment is ordered from a trailing 4-week average
    against a 9-16 week offshore lead time, so a spike that develops in three
    weeks cannot be covered. Fill-rate and OTIF misses emerge from that.
    """
    n_weeks = cfg.history_weeks
    week_ends = _week_ends(cfg)
    #: The toy seasonal curve. Known to the planner, so the replenishment
    #: policy below is allowed to use it for pre-build.
    holiday = _holiday_multiplier(week_ends)
    #: Seasonal index extended past the end of history so a target window that
    #: runs off the end of the simulation still has an index to sum over.
    season_ext = np.concatenate([holiday, np.full(32, float(holiday[-6:].mean()))])
    n_ext = season_ext.size

    # aggregate demand to (sku, dc) — the level inventory is actually held at
    sku_dc = (
        index.groupby(["sku_idx", "dc_code"], sort=True)
        .row.apply(list)
        .reset_index()
        .rename(columns={"row": "rows"})
    )
    n_pairs = len(sku_dc)
    pair_rows = sku_dc.rows.tolist()
    pair_sku = sku_dc.sku_idx.to_numpy()

    pair_demand = np.zeros((n_pairs, n_weeks))
    for p, rows in enumerate(pair_rows):
        pair_demand[p] = demand[rows].sum(axis=0)

    lead_weeks = np.ceil(
        rng.integers(cfg.lead_time_days_min, cfg.lead_time_days_max + 1, n_pairs) / 7.0
    ).astype(int)

    #: Week index at which each SKU's licence/lifecycle ends, used to taper
    #: replenishment at end of life.
    end_week = (
        products.launch_week_index.to_numpy(dtype=float)
        + products.planned_lifespan_weeks.to_numpy(dtype=float)
    )
    #: Planners over-buy at end of life; this is what later clears at markdown.
    eol_overshoot = rng.uniform(1.0, 1.0 + cfg.eol_markdown_rate, n_pairs)
    #: Protection interval and launch week per pair, needed every week for the
    #: end-of-life clearance test (not just on reorder weeks).
    protect_const = lead_weeks + cfg.reorder_every_weeks + cfg.safety_weeks
    launch_week_pair = products.launch_week_index.to_numpy(dtype=float)[pair_sku]

    on_hand = np.zeros((n_pairs, n_weeks))
    receipts = np.zeros((n_pairs, n_weeks))
    shipped_pair = np.zeros((n_pairs, n_weeks))
    markdown_units = np.zeros((n_pairs, n_weeks))
    expedite_units = np.zeros((n_pairs, n_weeks))
    pipeline = np.zeros((n_pairs, n_weeks + 24))

    # Open the window in STEADY STATE. Seeding on-hand at a full
    # lead-time-plus-safety cover but leaving the inbound pipeline empty puts
    # the stock position already at the base-stock level, so the policy places
    # no orders for ~17 weeks; the pipeline stays empty, and the first demand
    # uptick then causes a 14-week stockout that is a startup artefact rather
    # than a real failure mode. So seed on-hand at a few weeks of cover AND
    # prime the pipeline with the in-transit orders a running operation would
    # already have on the water.
    warm = pair_demand[:, :6].mean(axis=1)
    stock = np.maximum(warm * (cfg.safety_weeks + cfg.reorder_every_weeks), 8.0)
    for p in range(n_pairs):
        if warm[p] <= 0:
            continue
        for w in range(1, int(lead_weeks[p]) + 1):
            pipeline[p, w] += warm[p]

    # Pre-position an opening build for items that launch *inside* the window.
    # Without this, a SKU launching at week 40 starts from nothing and cannot be
    # replenished for 9-16 weeks, so its entire ramp-up is a guaranteed
    # stockout -- an artefact of the simulation, not a real failure mode. A
    # manufacturer commits an opening order against the retailer's launch plan
    # months ahead, so the build is sized from the item's early steady demand.
    # It deliberately does NOT anticipate a later licence or viral spike.
    protect0 = (lead_weeks + cfg.reorder_every_weeks + cfg.safety_weeks).astype(int)
    has_demand = (pair_demand > 0).any(axis=1)
    first_active = np.argmax(pair_demand > 0, axis=1)
    for p in np.flatnonzero(has_demand & (first_active > 0)):
        fa = int(first_active[p])
        early = pair_demand[p, fa : fa + 6]
        if early.size == 0:
            continue
        build = float(early.mean()) * float(protect0[p]) * 1.15
        if build > 0:
            pipeline[p, fa] += np.ceil(build)

    asn_rows: list[dict[str, object]] = []
    for t in range(n_weeks):
        arrive = pipeline[:, t].copy()
        stock = stock + arrive
        receipts[:, t] = arrive

        dem = pair_demand[:, t]
        ship = np.minimum(dem, stock)
        shipped_pair[:, t] = ship
        stock = stock - ship

        # Clear unsellable residual at markdown.
        #
        # Triggering only past end-of-life is not enough: demand decays
        # exponentially through DECLINE while a trailing-average policy keeps
        # replenishing against a 13-week lead, so declining items accumulate
        # ~125 weeks of supply and dead licences ~309 weeks, which together is
        # over half the network's inventory. A planner liquidates as soon as
        # on-hand exceeds what the remaining licence life can absorb, so the
        # trigger is weeks-of-supply against remaining life, not the EOL date.
        # The cleared units and forgone margin are the markdown/excess KPI.
        # Use a smoothed demand level: a 4-week mean dips in an ordinary trough
        # and would liquidate stock the item needs days later.
        smooth = pair_demand[:, max(0, t - 7) : t + 1].mean(axis=1)
        remaining_life = np.maximum(end_week[pair_sku] - t, 0.0)
        # optimistic view of what can still be sold at full wholesale
        sellable = smooth * remaining_life * 1.15
        # never clear below normal operating cover -- liquidating into the
        # replenishment protection interval starves the item and collapses
        # fill rate, which is a modelling error rather than a markdown
        operating_cover = smooth * (cfg.reorder_every_weeks + cfg.safety_weeks + 2.0)
        keep = np.maximum(sellable, operating_cover)
        gross_excess = np.maximum(stock - keep, 0.0)
        # Clearance is an END-OF-LIFE action, so it only applies once the item
        # can no longer absorb a replenishment cycle and has actually been
        # selling for a while. Without the launch guard, a pre-launch build
        # arrives while trailing demand is still zero, `sellable` evaluates to
        # ~0, and the whole opening order is liquidated the week it lands --
        # which alone wrote off ~65% of volume at markdown.
        late_life = remaining_life < protect_const
        started = (t - launch_week_pair) > 8
        clearable = late_life & started
        # only act on a genuinely large overhang, so healthy items are untouched
        act = clearable & (gross_excess > np.maximum(smooth * 4.0, 25.0))
        liquidate = np.where(act, np.floor(gross_excess * cfg.eol_clearance_rate), 0.0)
        markdown_units[:, t] = liquidate
        stock = stock - liquidate

        # Reactive air-freight expedite.
        #
        # Ocean replenishment is 9-16 weeks while a licence or viral spike
        # develops in 2-3 weeks, so no safety stock placed off a trailing
        # forecast can cover a 14x surge -- modelled without an expedite path
        # the portfolio tops out near 78% fill, which is not a real business.
        # What actually happens is that the planner spots the surge in POS and
        # air-freights at a large freight premium. That recovers most of the
        # spike a few weeks late, leaves the opening weeks lost, and carries a
        # cost. All three of those are the baseline this prototype improves on.
        surge = dem > np.maximum(1.8 * smooth, smooth + 3.0)
        running_low = stock < dem * 1.5
        expeditable = surge & running_low & (remaining_life > 6.0)
        need = np.maximum(dem * 3.0 - stock, 0.0)
        # not all of the need can be flown: supplier and freight capacity bind
        qty_air = np.where(expeditable, np.ceil(need * cfg.expedite_coverage), 0.0)
        if t + 2 < pipeline.shape[1]:
            pipeline[:, t + 2] += qty_air
        expedite_units[:, t] = qty_air

        on_hand[:, t] = stock

        # Place replenishment every reorder_every_weeks. This models the
        # incumbent weekly spreadsheet process, and it is deliberately
        # *competent at the predictable part*: the planner knows the toy
        # seasonal curve and pre-builds for Q4, and extrapolates a damped
        # recent trend. Modelling it as a bare trailing average would be a straw
        # man -- it would miss the known holiday peak and starve the whole
        # network, which is not what the baseline business looks like.
        #
        # What this policy structurally CANNOT see is a licence moment or a
        # viral spike that develops inside the 9-16 week inbound lead time.
        # Those are the misses the prototype is built to catch.
        if t % cfg.reorder_every_weeks == 0:
            lo = max(0, t - 3)
            trailing = pair_demand[:, lo : t + 1].mean(axis=1)

            # damped trend extrapolation from the last four weeks
            recent = pair_demand[:, max(0, t - 1) : t + 1].mean(axis=1)
            prior = pair_demand[:, max(0, t - 3) : max(1, t - 1)].mean(axis=1)
            raw_trend = np.divide(
                recent, prior, out=np.ones_like(recent), where=prior > 0
            )
            # allow the downside to react to a collapsing trend, not just 15%
            trend = np.clip(1.0 + 0.5 * (raw_trend - 1.0), 0.45, 1.55)

            # Deseasonalised demand level, then a proper periodic-review
            # base-stock target: the SUM of expected demand over the protection
            # interval (lead time + review period) plus safety stock.
            #
            # Multiplying a peak seasonal RATIO by the whole protection interval
            # instead would order ~19 weeks of *Q4 peak* demand every cycle and
            # inflate on-hand to ~25 weeks of cover, which is not a business --
            # it is an over-build artefact. Summing the seasonal path keeps the
            # pre-build proportionate to the weeks it actually has to cover.
            season_now = max(float(holiday[lo : t + 1].mean()), 1e-6)
            level = trailing / season_now * trend

            # Safety stock scales with observed demand variability, as a real
            # planner buffers a volatile licence more heavily than an evergreen
            # one. A single flat safety figure across a portfolio whose top
            # items swing 14x leaves the trend SKUs structurally under-buffered
            # and drags portfolio fill rate into the 70s.
            hist_lo = max(0, t - 11)
            hist = pair_demand[:, hist_lo : t + 1]
            mu = hist.mean(axis=1)
            sd = hist.std(axis=1)
            cv = np.divide(sd, mu, out=np.zeros_like(mu), where=mu > 0)
            safety_eff = cfg.safety_weeks * np.clip(1.0 + 1.0 * cv, 1.0, 2.2)

            target = np.zeros(n_pairs)
            for lw_val in np.unique(lead_weeks):
                m = lead_weeks == lw_val
                w0 = t + 1
                w1 = t + int(lw_val) + cfg.reorder_every_weeks + 1
                window = season_ext[min(w0, n_ext) : min(w1, n_ext)]
                horizon = float(window.sum()) if window.size else 0.0
                tail = float(season_ext[min(w1 - 1, n_ext - 1)])
                target[m] = level[m] * (horizon + safety_eff[m] * tail)

            # Do not buy inventory that will outlive the licence. The planner
            # knows the licence calendar, so once an item's remaining life is
            # shorter than the protection interval the target is cut back
            # proportionately. Without this the policy keeps replenishing a
            # collapsing item for a further 12-16 weeks of lead time and the
            # network ends up holding ~35 weeks of supply, most of it dead.
            # The overshoot factor keeps a realistic residual that clears at
            # markdown -- planners are optimistic about end-of-life demand.
            remaining = np.maximum(end_week[pair_sku] - t, 0.0)
            protect_w = lead_weeks + cfg.reorder_every_weeks + safety_eff
            # Squared taper: a linear cut-back still leaves items dying with
            # ~18 weeks of supply on hand, because stock ordered up to 16
            # weeks earlier keeps arriving after demand has collapsed. A
            # planner negotiates much smaller final buys on a short licence.
            eol_taper = np.clip(remaining / np.maximum(protect_w, 1.0), 0.0, 1.0) ** 2
            # overshoot applies only where the taper is actually biting,
            # otherwise it silently inflates every healthy item's target
            overshoot = np.where(eol_taper < 1.0, eol_overshoot, 1.0)
            target *= eol_taper * overshoot

            # Cap the buy at what the remaining licence life can absorb.
            # At a spike peak the trailing level IS the spike, so an uncapped
            # base-stock target orders ~17 weeks of peak demand for an item
            # with 30 weeks of life left -- roughly twice what it will ever
            # sell. The trend then collapses and the difference is written off,
            # which is what drove markdown to ~18% of revenue. The ceiling uses
            # the SMOOTHED level, not the spike, so a transient surge cannot
            # justify a lifetime-sized buy.
            smooth_level = pair_demand[:, max(0, t - 7) : t + 1].mean(axis=1)
            lifetime_cap = smooth_level * np.maximum(end_week[pair_sku] - t, 0.0) * 1.05
            target = np.minimum(target, lifetime_cap)
            in_transit = pipeline[:, t + 1 :].sum(axis=1)
            order = np.maximum(target - stock - in_transit, 0.0)
            # round to case pack
            cp = products.case_pack.to_numpy()[pair_sku]
            order = np.ceil(order / cp) * cp
            place = order > 0
            eta_idx = np.clip(t + lead_weeks, 0, n_weeks + 23)
            # supplier shorts / slips some ASNs
            short = rng.random(n_pairs) < cfg.supplier_short_rate
            qty = np.where(short, np.floor(order * rng.uniform(0.72, 0.95, n_pairs)), order)
            slip = rng.integers(0, 4, n_pairs) * (rng.random(n_pairs) < cfg.supplier_slip_rate)
            eta_actual = np.clip(eta_idx + slip, 0, n_weeks + 23)
            for p in np.flatnonzero(place):
                pipeline[p, eta_actual[p]] += qty[p]
                if len(asn_rows) < 200_000:
                    oi = int(eta_idx[p])
                    ai = int(eta_actual[p])
                    asn_rows.append(
                        {
                            "asn_id": f"ASN-{len(asn_rows) + 1:07d}",
                            "sku_id": products.sku_id.iloc[int(pair_sku[p])],
                            "dc_code": sku_dc.dc_code.iloc[p],
                            "order_placed_date": week_ends[t].isoformat(),
                            "ordered_units": int(order[p]),
                            "shipped_units": int(qty[p]),
                            "original_eta_date": week_ends[min(oi, n_weeks - 1)].isoformat()
                            if oi < n_weeks
                            else (
                                week_ends[-1] + dt.timedelta(weeks=oi - n_weeks + 1)
                            ).isoformat(),
                            "revised_eta_date": week_ends[min(ai, n_weeks - 1)].isoformat()
                            if ai < n_weeks
                            else (
                                week_ends[-1] + dt.timedelta(weeks=ai - n_weeks + 1)
                            ).isoformat(),
                            "lead_time_days_planned": int(lead_weeks[p] * 7),
                            "lead_time_days_actual": int((lead_weeks[p] + slip[p]) * 7),
                            "origin_country": products.country_of_origin.iloc[int(pair_sku[p])],
                        }
                    )

    # distribute each (sku,dc) weekly shipment back to retailers by demand share
    fill_ratio = np.divide(
        shipped_pair, pair_demand, out=np.ones_like(shipped_pair), where=pair_demand > 0
    )
    row_fill = np.ones_like(demand)
    for p, rows in enumerate(pair_rows):
        row_fill[rows] = fill_ratio[p]

    return {
        "sku_dc": sku_dc,
        "pair_demand": pair_demand,
        "on_hand": on_hand,
        "receipts": receipts,
        "pipeline": pipeline,
        "row_fill": row_fill,
        "markdown_units": markdown_units,
        "expedite_units": expedite_units,
        "asn": pd.DataFrame(asn_rows),
        "week_ends": week_ends,
        "lead_weeks": lead_weeks,
    }


# ---------------------------------------------------------------------------
# flat-file emission
# ---------------------------------------------------------------------------


def _write_chunks(df: pd.DataFrame, out_dir: pathlib.Path, stem: str, n_chunks: int) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    if n_chunks <= 1:
        p = out_dir / f"{stem}.csv"
        df.to_csv(p, index=False)
        return [str(p)]
    bounds = np.array_split(np.arange(len(df)), n_chunks)
    for i, b in enumerate(bounds):
        if b.size == 0:
            continue
        p = out_dir / f"{stem}_part{i:03d}.csv"
        df.iloc[b].to_csv(p, index=False)
        paths.append(str(p))
    return paths


def emit_pos_weekly(
    cfg: GenConfig,
    rng: np.random.Generator,
    products: pd.DataFrame,
    index: pd.DataFrame,
    demand: np.ndarray,
    promo: np.ndarray,
    sup: dict[str, object],
    landing: pathlib.Path,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """POS and retailer on-hand by week, with deliberate messiness injected."""
    week_ends: list[dt.date] = sup["week_ends"]  # type: ignore[assignment]
    row_fill: np.ndarray = sup["row_fill"]  # type: ignore[assignment]

    # consumer takeaway is capped by what we actually shipped
    pos = np.floor(demand * np.clip(row_fill, 0, 1))
    active = demand > 0

    sku_ids = products.sku_id.to_numpy()
    case_pack = products.case_pack.to_numpy()

    r_idx, w_idx = np.nonzero(active)
    n = r_idx.size
    rec = pd.DataFrame(
        {
            "sku_id": sku_ids[index.sku_idx.to_numpy()[r_idx]],
            "retailer_code": index.retailer_code.to_numpy()[r_idx],
            "dc_code": index.dc_code.to_numpy()[r_idx],
            "week_end_date": [week_ends[w].isoformat() for w in w_idx],
            "pos_units": pos[r_idx, w_idx].astype(int),
            "true_demand_units": demand[r_idx, w_idx].astype(int),
            "promo_flag": promo[r_idx, w_idx].astype(int),
            "_cp": case_pack[index.sku_idx.to_numpy()[r_idx]],
        }
    )
    # retailer on-hand: a few weeks of cover, noisy
    rec["on_hand_units"] = np.maximum(
        0,
        (rec.pos_units * rng.uniform(0.6, 3.1, n)).round().astype(int),
    )
    rec["reported_uom"] = "EACH"

    stats: dict[str, int] = {}

    # --- ETAIL_C reports CASES not eaches (silver must normalise) ---------
    is_etail = rec.retailer_code.to_numpy() == "ETAIL_C"
    rec.loc[is_etail, "pos_units"] = np.maximum(
        1, np.floor(rec.loc[is_etail, "pos_units"] / rec.loc[is_etail, "_cp"])
    ).astype(int)
    rec.loc[is_etail, "on_hand_units"] = np.maximum(
        0, np.floor(rec.loc[is_etail, "on_hand_units"] / rec.loc[is_etail, "_cp"])
    ).astype(int)
    rec.loc[is_etail, "reported_uom"] = "CASE"
    stats["etail_case_rows"] = int(is_etail.sum())

    rec = rec.drop(columns=["_cp"])

    # --- null on-hand -----------------------------------------------------
    nulls = rng.random(len(rec)) < cfg.null_onhand_rate
    rec.loc[nulls, "on_hand_units"] = np.nan
    stats["null_onhand_rows"] = int(nulls.sum())

    # --- negative quantities (expectation must DROP these) ----------------
    negs = rng.random(len(rec)) < cfg.negative_qty_rate
    rec.loc[negs, "pos_units"] = -rec.loc[negs, "pos_units"].abs() - 1
    stats["negative_qty_rows"] = int(negs.sum())

    # --- report timestamp, with late-arriving weeks -----------------------
    wk = pd.to_datetime(rec.week_end_date)
    lag_days = np.where(
        rng.random(len(rec)) < cfg.late_week_rate,
        rng.integers(9, 31, len(rec)),
        rng.integers(1, 4, len(rec)),
    )
    rec["reported_at"] = (wk + pd.to_timedelta(lag_days, unit="D")).dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    stats["late_arriving_rows"] = int((lag_days > 8).sum())

    # --- duplicate rows (expectation/dedupe must collapse these) ----------
    dup_n = int(len(rec) * cfg.dup_row_rate)
    dups = rec.sample(n=dup_n, random_state=cfg.seed)
    stats["duplicate_rows"] = dup_n

    # --- orphan SKU references (referential check must flag these) --------
    orph_n = int(len(rec) * cfg.orphan_sku_rate)
    orph = rec.sample(n=orph_n, random_state=cfg.seed + 1).copy()
    orph["sku_id"] = [f"SKU-9{i:04d}" for i in range(orph_n)]
    stats["orphan_sku_rows"] = orph_n

    full = pd.concat([rec, dups, orph], ignore_index=True)
    full = full.sample(frac=1.0, random_state=cfg.seed + 2).reset_index(drop=True)

    _write_chunks(full, landing / "pos_weekly", "pos_weekly", 12)
    stats["total_rows_written"] = len(full)
    return full, stats


def emit_orders(
    cfg: GenConfig,
    rng: np.random.Generator,
    products: pd.DataFrame,
    index: pd.DataFrame,
    demand: np.ndarray,
    sup: dict[str, object],
    landing: pathlib.Path,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """PO lines: requested vs confirmed vs shipped qty and date -> fill rate, OTIF."""
    week_ends: list[dt.date] = sup["week_ends"]  # type: ignore[assignment]
    row_fill: np.ndarray = sup["row_fill"]  # type: ignore[assignment]
    n_weeks = cfg.history_weeks

    sku_idx = index.sku_idx.to_numpy()
    sku_ids = products.sku_id.to_numpy()
    cps = products.case_pack.to_numpy()
    wp = products.wholesale_price_usd.to_numpy()

    # retailers order every reorder_every_weeks against their own trailing average
    order_weeks = np.arange(2, n_weeks, cfg.reorder_every_weeks)
    rows: list[dict[str, object]] = []
    for t in order_weeks:
        lo = max(0, t - 4)
        trailing = demand[:, lo:t].mean(axis=1)
        want = np.ceil(trailing * cfg.reorder_every_weeks * rng.uniform(0.9, 1.25, len(trailing)))
        live = want > 0
        for r in np.flatnonzero(live):
            si = int(sku_idx[r])
            cp = int(cps[si])
            requested = int(np.ceil(want[r] / cp) * cp)
            if requested <= 0:
                continue
            # confirmed is limited by what supply could actually cover that week
            avail_ratio = float(np.clip(row_fill[r, t], 0, 1))
            confirmed = int(np.floor(requested * min(1.0, avail_ratio + rng.uniform(0.0, 0.07))))
            confirmed = max(0, min(confirmed, requested))
            # a further short-ship slice misses in-full
            if rng.random() < cfg.otif_short_ship_rate:
                shipped = int(np.floor(confirmed * rng.uniform(0.72, 0.97)))
            else:
                shipped = confirmed
            req_date = week_ends[t]
            late = rng.random() < cfg.otif_date_miss_rate
            # On-time means shipped within the 2-day grace window, so the
            # not-late branch must stay inside it; letting it reach +3 days
            # silently turned another ~17% of lines late and made the measured
            # on-time rate unrelated to otif_date_miss_rate.
            ship_lag = int(rng.integers(5, 16)) if late else int(rng.integers(-2, 3))
            ship_date = req_date + dt.timedelta(days=ship_lag)
            on_time = ship_date <= req_date + dt.timedelta(days=2)
            in_full = shipped >= confirmed and confirmed >= requested
            otif = bool(on_time and in_full)
            # Retailer compliance chargebacks are punitive, not proportional to
            # the shortfall: a non-compliant PO attracts flat handling fees plus
            # a percentage penalty, so the realistic band is a high single-digit
            # share of the line value on most non-compliant lines.
            chargeback = 0.0
            if not otif and rng.random() < cfg.chargeback_rate_on_miss:
                chargeback = round(
                    requested * float(wp[si]) * rng.uniform(0.04, 0.15), 2
                )
            rows.append(
                {
                    "po_line_id": f"PO-{len(rows) + 1:08d}",
                    "sku_id": sku_ids[si],
                    "retailer_code": index.retailer_code.iloc[r],
                    "dc_code": index.dc_code.iloc[r],
                    "requested_week_end_date": req_date.isoformat(),
                    "requested_units": requested,
                    "confirmed_units": confirmed,
                    "shipped_units": shipped,
                    "requested_ship_date": req_date.isoformat(),
                    "actual_ship_date": ship_date.isoformat(),
                    "wholesale_price_usd": float(wp[si]),
                    "chargeback_usd": chargeback,
                }
            )

    df = pd.DataFrame(rows)
    # a small slice of negative requested qty for the expectations to drop
    negs = rng.random(len(df)) < cfg.negative_qty_rate
    df.loc[negs, "requested_units"] = -df.loc[negs, "requested_units"].abs()
    _write_chunks(df, landing / "orders", "orders", 8)
    stats = {
        "total_rows_written": len(df),
        "negative_qty_rows": int(negs.sum()),
        "lines_with_chargeback": int((df.chargeback_usd > 0).sum()),
    }
    return df, stats


def emit_inventory(
    cfg: GenConfig,
    rng: np.random.Generator,
    products: pd.DataFrame,
    sup: dict[str, object],
    landing: pathlib.Path,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Daily on-hand + in-transit per (sku, DC) for the retained window."""
    week_ends: list[dt.date] = sup["week_ends"]  # type: ignore[assignment]
    on_hand: np.ndarray = sup["on_hand"]  # type: ignore[assignment]
    pipeline: np.ndarray = sup["pipeline"]  # type: ignore[assignment]
    sku_dc: pd.DataFrame = sup["sku_dc"]  # type: ignore[assignment]

    n_weeks = cfg.history_weeks
    n_days = cfg.snapshot_days
    last_day = week_ends[-1]
    days = [last_day - dt.timedelta(days=n_days - 1 - i) for i in range(n_days)]

    sku_ids = products.sku_id.to_numpy()
    pair_sku = sku_dc.sku_idx.to_numpy()
    pair_dc = sku_dc.dc_code.to_numpy()

    # only pairs with activity in the window
    first_week = n_weeks - int(np.ceil(n_days / 7)) - 1
    live = on_hand[:, first_week:].sum(axis=1) > 0
    pidx = np.flatnonzero(live)

    frames: list[pd.DataFrame] = []
    for d_i, d in enumerate(days):
        w = min(n_weeks - 1, first_week + d_i // 7)
        oh = on_hand[pidx, w]
        # smooth within-week drawdown
        oh_day = np.maximum(0, oh * rng.uniform(0.92, 1.08, oh.size)).round()
        in_transit = pipeline[pidx, w + 1 : w + 5].sum(axis=1)
        frames.append(
            pd.DataFrame(
                {
                    "snapshot_date": d.isoformat(),
                    "sku_id": sku_ids[pair_sku[pidx]],
                    "dc_code": pair_dc[pidx],
                    "on_hand_units": oh_day.astype(int),
                    "in_transit_units": in_transit.round().astype(int),
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    _write_chunks(df, landing / "inventory_snapshots", "inventory_snapshots", 10)
    return df, {"total_rows_written": len(df), "distinct_pairs": int(pidx.size)}


def emit_markdown(
    cfg: GenConfig,
    rng: np.random.Generator,
    products: pd.DataFrame,
    sup: dict[str, object],
    landing: pathlib.Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Close-out clearance of end-of-life residual stock.

    Recorded as real liquidation lines so the markdown/excess KPI in the deck
    traces to committed data rather than being inferred from a snapshot.
    """
    week_ends: list[dt.date] = sup["week_ends"]  # type: ignore[assignment]
    md: np.ndarray = sup["markdown_units"]  # type: ignore[assignment]
    sku_dc: pd.DataFrame = sup["sku_dc"]  # type: ignore[assignment]

    pair_sku = sku_dc.sku_idx.to_numpy()
    pair_dc = sku_dc.dc_code.to_numpy()
    p_idx, w_idx = np.nonzero(md > 0)
    if p_idx.size == 0:
        df = pd.DataFrame(
            columns=[
                "clearance_id", "sku_id", "dc_code", "week_end_date", "cleared_units",
                "wholesale_price_usd", "clearance_price_usd", "markdown_loss_usd",
            ]
        )
        _write_chunks(df, landing / "markdown_clearance", "markdown_clearance", 1)
        return df, {"total_rows_written": 0}

    si = pair_sku[p_idx]
    wp = products.wholesale_price_usd.to_numpy()[si]
    cost = products.unit_cost_usd.to_numpy()[si]
    units = md[p_idx, w_idx]
    # close-out recovers somewhere between below-cost and a steep discount
    recovery = rng.uniform(0.40, 0.72, units.size)
    clearance_price = np.round(wp * recovery, 2)

    df = pd.DataFrame(
        {
            "clearance_id": [f"MD-{i + 1:07d}" for i in range(units.size)],
            "sku_id": products.sku_id.to_numpy()[si],
            "dc_code": pair_dc[p_idx],
            "week_end_date": [week_ends[w].isoformat() for w in w_idx],
            "cleared_units": units.astype(int),
            "wholesale_price_usd": wp.round(2),
            "clearance_price_usd": clearance_price,
            "unit_cost_usd": cost.round(2),
        }
    )
    # margin forgone against the normal wholesale price
    df["markdown_loss_usd"] = (
        df.cleared_units * (df.wholesale_price_usd - df.clearance_price_usd)
    ).round(2)
    df = df.sort_values("week_end_date").reset_index(drop=True)
    _write_chunks(df, landing / "markdown_clearance", "markdown_clearance", 4)
    return df, {
        "total_rows_written": len(df),
        "cleared_units": int(df.cleared_units.sum()),
        "markdown_loss_usd": round(float(df.markdown_loss_usd.sum()), 2),
    }


def emit_inbound(
    cfg: GenConfig, products: pd.DataFrame, sup: dict[str, object], landing: pathlib.Path
) -> tuple[pd.DataFrame, dict]:
    """Inbound ASNs: scheduled ocean replenishment plus reactive air expedites."""
    df: pd.DataFrame = sup["asn"]  # type: ignore[assignment]
    df = df.copy()
    df["transport_mode"] = "OCEAN"
    df["freight_cost_usd"] = (df.shipped_units * cfg.ocean_freight_usd_per_unit).round(2)

    # air expedites become their own ASN lines, so the freight premium the
    # current process pays to chase spikes is visible in committed data
    week_ends: list[dt.date] = sup["week_ends"]  # type: ignore[assignment]
    exp: np.ndarray = sup["expedite_units"]  # type: ignore[assignment]
    sku_dc: pd.DataFrame = sup["sku_dc"]  # type: ignore[assignment]
    p_idx, w_idx = np.nonzero(exp > 0)
    air = pd.DataFrame()
    if p_idx.size:
        si = sku_dc.sku_idx.to_numpy()[p_idx]
        units = exp[p_idx, w_idx].astype(int)
        eta = [week_ends[min(w + 2, len(week_ends) - 1)].isoformat() for w in w_idx]
        air = pd.DataFrame(
            {
                "asn_id": [f"ASN-AIR-{i + 1:06d}" for i in range(units.size)],
                "sku_id": products.sku_id.to_numpy()[si],
                "dc_code": sku_dc.dc_code.to_numpy()[p_idx],
                "order_placed_date": [week_ends[w].isoformat() for w in w_idx],
                "ordered_units": units,
                "shipped_units": units,
                "original_eta_date": eta,
                "revised_eta_date": eta,
                "lead_time_days_planned": 14,
                "lead_time_days_actual": 14,
                "origin_country": products.country_of_origin.to_numpy()[si],
                "transport_mode": "AIR",
                "freight_cost_usd": (units * cfg.air_freight_usd_per_unit).round(2),
            }
        )
    out = pd.concat([df, air], ignore_index=True) if len(air) else df
    _write_chunks(out, landing / "inbound_shipments", "inbound_shipments", 6)
    slipped = int((out.revised_eta_date > out.original_eta_date).sum()) if len(out) else 0
    shorted = int((out.shipped_units < out.ordered_units).sum()) if len(out) else 0
    return out, {
        "total_rows_written": len(out),
        "ocean_rows": int((out.transport_mode == "OCEAN").sum()),
        "air_expedite_rows": int((out.transport_mode == "AIR").sum()),
        "air_expedite_units": int(out.loc[out.transport_mode == "AIR", "shipped_units"].sum()),
        "air_freight_usd": round(
            float(out.loc[out.transport_mode == "AIR", "freight_cost_usd"].sum()), 2
        ),
        "slipped_eta_rows": slipped,
        "short_shipped_rows": shorted,
    }


# ---------------------------------------------------------------------------
# unstructured sources: supplier PDFs + product images
# ---------------------------------------------------------------------------


def _ensure(pkg: str, mod: str | None = None) -> None:
    try:
        __import__(mod or pkg)
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", pkg],
            stdout=subprocess.DEVNULL,
        )


def emit_supplier_docs(
    cfg: GenConfig, rng: np.random.Generator, products: pd.DataFrame, landing: pathlib.Path
) -> dict[str, object]:
    """Supplier invoices and spec sheets as real PDFs with extractable text."""
    _ensure("reportlab", "reportlab")
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    out = landing / "supplier_docs"
    out.mkdir(parents=True, exist_ok=True)

    suppliers = [
        ("Pearl River Plush Works", "VN", "Binh Duong"),
        ("Summit Mould & Figure Co.", "CN", "Dongguan"),
        ("Lotus Craft Toy Manufacturing", "ID", "Tangerang"),
        ("Highland Softgoods Ltd.", "VN", "Hai Phong"),
    ]
    picks = products.sample(n=cfg.n_supplier_docs * 3, random_state=cfg.seed + 7)
    written: list[dict[str, object]] = []

    for i in range(cfg.n_supplier_docs):
        is_invoice = i % 2 == 0
        sup_name, cc, city = suppliers[i % len(suppliers)]
        items = picks.iloc[i * 3 : i * 3 + 3]
        kind = "invoice" if is_invoice else "spec_sheet"
        fname = f"{kind}_{sup_name.split()[0].lower()}_{2026}_{i + 1:02d}.pdf"
        path = out / fname
        c = canvas.Canvas(str(path), pagesize=LETTER)
        W, H = LETTER
        y = H - 1.0 * inch

        c.setFont("Helvetica-Bold", 15)
        c.drawString(1 * inch, y, sup_name)
        y -= 16
        c.setFont("Helvetica", 9)
        c.drawString(1 * inch, y, f"{city}, {cc}  |  Supplier of record  |  SYNTHETIC DOCUMENT")
        y -= 28

        c.setFont("Helvetica-Bold", 12)
        c.drawString(1 * inch, y, "COMMERCIAL INVOICE" if is_invoice else "PRODUCT SPECIFICATION")
        y -= 20
        c.setFont("Helvetica", 9)
        doc_no = f"{'INV' if is_invoice else 'SPEC'}-{cc}-{2026}-{i + 101}"
        c.drawString(1 * inch, y, f"Document number: {doc_no}")
        y -= 13
        c.drawString(1 * inch, y, f"Issue date: 2026-{(i % 9) + 1:02d}-{(i * 3 % 27) + 1:02d}")
        y -= 13
        terms = rng.choice(("NET 60", "NET 45", "NET 30"))
        c.drawString(1 * inch, y, f"Payment terms: {terms}   Incoterms: FOB {city}")
        y -= 13
        lt = int(rng.integers(cfg.lead_time_days_min, cfg.lead_time_days_max))
        c.drawString(1 * inch, y, f"Quoted production lead time: {lt} days")
        y -= 24

        for _, p in items.iterrows():
            c.setFont("Helvetica-Bold", 10)
            c.drawString(1 * inch, y, f"{p.sku_id}  {p.product_name}")
            y -= 13
            c.setFont("Helvetica", 9)
            lines = [
                f"Brand: {p.brand}    Category: {p.category}    "
                f"Licence family: {p.license_family}",
                f"Material composition: {p.material_composition}",
                f"Figure type: {p.figure_type}    Country of origin: {p.country_of_origin}",
                f"Case pack: {int(p.case_pack)} eaches    "
                f"Unit cost: USD {p.unit_cost_usd:.2f}",
            ]
            if is_invoice:
                qty = int(rng.integers(600, 9000))
                lines.append(
                    f"Invoiced quantity: {qty} eaches    "
                    f"Extended value: USD {qty * p.unit_cost_usd:,.2f}"
                )
            else:
                lines += [
                    "Safety standard: ASTM F963-17; EN71-1/2/3 compliant",
                    f"Age grading: {rng.choice(('3+', '4+', '6+', '8+'))}",
                    "Flammability: passes 16 CFR 1500.44",
                ]
            for ln in lines:
                c.drawString(1.15 * inch, y, ln)
                y -= 12
            y -= 8
            if y < 1.4 * inch:
                c.showPage()
                y = H - 1 * inch

        c.setFont("Helvetica-Oblique", 8)
        c.drawString(
            1 * inch, 0.75 * inch,
            "Synthetic document generated by src/datagen/generate.py for demonstration only.",
        )
        c.save()
        written.append(
            {
                "file": fname,
                "kind": kind,
                "supplier": sup_name,
                "skus": list(items.sku_id),
                "terms": str(terms),
                "lead_time_days": lt,
            }
        )

    (out / "_doc_index.json").write_text(json.dumps(written, indent=2))
    return {"files_written": len(written), "dir": str(out)}


def emit_product_images(
    cfg: GenConfig, rng: np.random.Generator, products: pd.DataFrame, landing: pathlib.Path
) -> dict[str, object]:
    """Simple product silhouettes: varied dominant colour, humanoid vs non-humanoid."""
    _ensure("pillow", "PIL")
    from PIL import Image, ImageDraw

    out = landing / "product_images"
    out.mkdir(parents=True, exist_ok=True)

    palette = [
        ("coral", (235, 94, 85)), ("teal", (42, 157, 143)), ("mustard", (233, 196, 106)),
        ("indigo", (69, 73, 156)), ("rose", (226, 122, 160)), ("moss", (106, 141, 93)),
        ("slate", (84, 101, 117)), ("violet", (142, 108, 196)),
    ]
    picks = products.sample(n=cfg.n_product_images, random_state=cfg.seed + 11)
    rows: list[dict[str, object]] = []

    for i, (_, p) in enumerate(picks.iterrows()):
        cname, rgb = palette[i % len(palette)]
        img = Image.new("RGB", (320, 320), (247, 247, 245))
        d = ImageDraw.Draw(img)
        humanoid = str(p.figure_type) == "HUMANOID"
        if humanoid:
            d.ellipse([130, 40, 190, 100], fill=rgb)          # head
            d.rounded_rectangle([132, 104, 188, 210], 12, fill=rgb)  # torso
            d.rounded_rectangle([96, 112, 128, 196], 10, fill=rgb)   # arms
            d.rounded_rectangle([192, 112, 224, 196], 10, fill=rgb)
            d.rounded_rectangle([136, 214, 156, 290], 9, fill=rgb)   # legs
            d.rounded_rectangle([164, 214, 184, 290], 9, fill=rgb)
        else:
            d.ellipse([74, 120, 246, 280], fill=rgb)          # rounded body
            d.ellipse([112, 48, 208, 144], fill=rgb)          # big head
            d.ellipse([96, 56, 126, 94], fill=rgb)            # ears
            d.ellipse([194, 56, 224, 94], fill=rgb)
        fname = f"{p.sku_id}_{cname}.png"
        img.save(out / fname)
        rows.append(
            {
                "file": fname,
                "sku_id": p.sku_id,
                "dominant_color_label": cname,
                "dominant_color_rgb": list(rgb),
                "figure_type_truth": str(p.figure_type),
                "silhouette": "HUMANOID" if humanoid else "NON_HUMANOID",
                "category": str(p.category),
                "country_of_origin": str(p.country_of_origin),
            }
        )
    (out / "_image_index.json").write_text(json.dumps(rows, indent=2))
    return {"files_written": len(rows), "dir": str(out)}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def generate(landing_path: str, cfg: GenConfig = CFG) -> dict[str, object]:
    """Generate the full landing zone. Returns a manifest dict (also written as JSON)."""
    landing = pathlib.Path(landing_path)
    landing.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(cfg.seed)
    t0 = dt.datetime.now(dt.UTC)

    products = build_products(cfg, rng)
    index, demand, promo, _ = build_demand(cfg, rng, products)
    sup = simulate_supply(cfg, rng, products, index, demand)

    pm = products.drop(columns=["base_weekly_rate"])
    _write_chunks(pm, landing / "product_master", "product_master", 1)

    pos, pos_stats = emit_pos_weekly(cfg, rng, products, index, demand, promo, sup, landing)
    orders, ord_stats = emit_orders(cfg, rng, products, index, demand, sup, landing)
    inv, inv_stats = emit_inventory(cfg, rng, products, sup, landing)
    asn, asn_stats = emit_inbound(cfg, products, sup, landing)
    mdn, mdn_stats = emit_markdown(cfg, rng, products, sup, landing)
    docs = emit_supplier_docs(cfg, rng, products, landing)
    imgs = emit_product_images(cfg, rng, products, landing)

    week_ends: list[dt.date] = sup["week_ends"]  # type: ignore[assignment]
    manifest: dict[str, object] = {
        "generated_at_utc": t0.isoformat(),
        "generator": "src/datagen/generate.py",
        "config": {k: (v.isoformat() if isinstance(v, dt.date) else v)
                   for k, v in dataclasses.asdict(cfg).items()},
        "data_is_synthetic": True,
        "week_range": [week_ends[0].isoformat(), week_ends[-1].isoformat()],
        "sources": {
            "product_master": {"rows": len(pm)},
            "pos_weekly": pos_stats,
            "orders": ord_stats,
            "inventory_snapshots": inv_stats,
            "inbound_shipments": asn_stats,
            "markdown_clearance": mdn_stats,
            "supplier_docs": docs,
            "product_images": imgs,
        },
        "triplets_sku_retailer_dc": int(len(index)),
        "elapsed_seconds": round(
            (dt.datetime.now(dt.UTC) - t0).total_seconds(), 1
        ),
    }
    (landing / "_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    return manifest


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "LANDING", "/Volumes/toy_demand_allocation/bronze/landing"
    )
    m = generate(target)
    print(json.dumps(m, indent=2, default=str))
