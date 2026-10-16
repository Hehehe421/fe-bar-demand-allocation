# ruff: noqa: E501  (business-case prose is kept as readable sentences)
"""The value model: measured baseline -> annual value range, per lever, with every assumption named.

    python tools/value_model.py

Reads only ``evidence/metrics/kpi_baseline.csv`` (measured from the generated data by notebook 02)
and the labelled assumptions below. Writes ``evidence/metrics/value_model.csv`` and
``docs/BUSINESS_CASE.md``. Nothing is typed into the business case by hand, so a change to the
baseline or to an assumption changes every number that depends on it.

Two kinds of number appear, and they are never mixed up:

* **measured**: computed from the synthetic portfolio (3 years, 156 weeks). Annualised by / 3.
* **assumed**: a capture rate (how much of a measured loss the solution removes) or a scale.
  Every assumption has a low, mid and high value and a one-line reason.
"""

from __future__ import annotations

import csv
import pathlib
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.config as config  # noqa: E402

YEARS = 3.0                       # 156 weeks of history, 2023-10 to 2026-09
CASES = ("low", "mid", "high")

# ---- measured elsewhere in this repo (cited, not re-derived) --------------------------------
WAPE_MODEL, WAPE_NAIVE = 67.419, 95.342          # evidence/metrics/forecast_leaderboard.csv
P90_COVERAGE_TREND = 74.1                        # evidence/03_ml_forecast.md, SOCIAL_TREND
REVENUE_AT_RISK_13W, CRITICAL_POSITIONS, CRITICAL_REVENUE = 1_381_392, 434, 509_958   # evidence/07_app.md
RUN_COST_14D_LIST_USD = 321      # system.billing.usage x list price, 2026-10-01..15, this build's resources (see BUSINESS_CASE §4)


@dataclass(frozen=True)
class Lever:
    key: str
    name: str
    kpi: str                     # the buyer's KPI it moves
    base_key: str                # kpi_baseline.csv row (3-year total, USD)
    capture: tuple[float, float, float]
    capture_reason: str
    value_factor: float = 1.0    # converts the measured $ into value $ (margin, avoidable share)
    factor_reason: str = ""


def load_baseline() -> dict[str, float]:
    with (ROOT / "evidence/metrics/kpi_baseline.csv").open() as fh:
        return {r["kpi"]: float(r["value"]) for r in csv.DictReader(fh)}


def levers(bl: dict[str, float]) -> list[Lever]:
    premium = bl["air_vs_ocean_freight_premium_x"]
    error_cut = 1 - WAPE_MODEL / WAPE_NAIVE
    return [
        Lever("lost_sales", "Recover sales lost to stockouts", "Stockout-weeks, fill rate",
              "lost_revenue_usd", (0.10, 0.20, 0.30),
              f"Forecast error is {100 * error_cut:.0f}% lower than the naive forecast (measured). The high case assumes lost sales fall in proportion; low and mid assume a third and two thirds of that.",
              config.ASSUMED_GROSS_MARGIN_PCT,
              f"Lost sales are valued at gross margin ({100 * config.ASSUMED_GROSS_MARGIN_PCT:.0f}%, an assumption), not at revenue: the margin is what the business loses."),
        Lever("air_freight", "Plan inbound on ocean instead of expediting by air", "Freight cost per unit",
              "air_freight_usd", (0.15, 0.30, 0.45),
              "Share of today's air-expedited units that a 13-week forecast flags early enough to move by ocean (9-16 week lead time). Assumed; the earlier the spike is visible, the higher.",
              1 - 1 / premium,
              f"Only the premium is saved: air costs {premium:.1f}x ocean per unit (measured), so moving a unit saves {100 * (1 - 1 / premium):.0f}% of its air cost."),
        Lever("markdown", "Buy and allocate less into a cooling licence", "Markdown $, weeks of supply",
              "markdown_loss_usd", (0.05, 0.10, 0.15),
              "Conservative: markdown is driven mostly by when a licence collapses, which a demand forecast sees only partly. Measured loss is already margin forgone, so no margin factor."),
        Lever("chargebacks", "Fewer compliance chargebacks", "OTIF, chargeback $",
              "compliance_chargeback_usd", (0.10, 0.20, 0.30),
              "Share of late or short PO lines avoided by allocating constrained supply to commitments before the PO week rather than in the meeting after it."),
    ]


def compute() -> tuple[dict[str, float], list[dict[str, object]], dict[str, float]]:
    bl = load_baseline()
    invoiced_yr = bl["invoiced_wholesale_usd"] / YEARS
    rows: list[dict[str, object]] = []
    totals = dict.fromkeys(CASES, 0.0)
    for lv in levers(bl):
        base_yr = bl[lv.base_key] / YEARS
        rec: dict[str, object] = {"lever": lv.key, "name": lv.name, "kpi": lv.kpi,
                                  "measured_loss_per_year_usd": round(base_yr),
                                  "value_factor": round(lv.value_factor, 3)}
        for case, cap in zip(CASES, lv.capture, strict=True):
            v = base_yr * cap * lv.value_factor
            rec[f"capture_{case}"] = cap
            rec[f"value_{case}_usd"] = round(v)
            totals[case] += v
        rows.append(rec)
    pct = {c: 100 * totals[c] / invoiced_yr for c in CASES}
    meta = {"invoiced_per_year_usd": invoiced_yr,
            "leakage_per_year_usd": sum(bl[k] for k in ("lost_revenue_usd", "air_freight_usd",
                                                        "markdown_loss_usd", "compliance_chargeback_usd")) / YEARS,
            **{f"total_{c}_usd": totals[c] for c in CASES}, **{f"pct_{c}": pct[c] for c in CASES}}
    return bl, rows, meta


def kpi_impact(bl: dict[str, float], rows: list[dict[str, object]], meta: dict[str, float]) -> list[dict[str, str]]:
    """The mid case expressed in each buyer's KPIs: baseline -> modelled, per year.

    Every delta comes from the same capture rates as the dollar value, so the KPI view and the dollar
    view cannot disagree. Two links are assumptions and are named: stockout-weeks fall in proportion
    to lost sales, and OTIF misses fall in proportion to chargebacks.
    """
    cap = {str(r["lever"]): float(r["capture_mid"]) for r in rows}
    val = {str(r["lever"]): float(r["value_mid_usd"]) for r in rows}
    inv, s500 = meta["invoiced_per_year_usd"], config.SENSITIVITY_REVENUE_SCALES_USD[1]

    def at500(v: float) -> str:
        return m(s500 * v / inv)

    sow = bl["stockout_weeks"] / YEARS
    otif = bl["otif_pct"]
    lost = bl["lost_revenue_usd"] / YEARS
    cb = bl["compliance_chargeback_usd"] / YEARS
    md = bl["markdown_loss_usd"] / YEARS
    air_u = bl["air_expedite_units"] / YEARS
    air = bl["air_freight_usd"] / YEARS
    return [
        {"persona": "sponsor", "kpi": "Margin recovered (share of revenue)", "baseline": "—",
         "modelled": f"+{meta['pct_mid']:.1f} pts of revenue", "per_year_synthetic": m(meta["total_mid_usd"]),
         "at_500m": at500(meta["total_mid_usd"]), "basis": "sum of the four levers, mid case"},
        {"persona": "sponsor", "kpi": "Markdown on excess", "baseline": f"{m(md)} / yr",
         "modelled": f"{m(md * (1 - cap['markdown']))} / yr (−{100 * cap['markdown']:.0f}%)",
         "per_year_synthetic": m(val["markdown"]), "at_500m": at500(val["markdown"]), "basis": "markdown capture rate"},
        {"persona": "sponsor", "kpi": "Air-freight expedites", "baseline": f"{air_u:,.0f} units, {m(air)} / yr",
         "modelled": f"{air_u * (1 - cap['air_freight']):,.0f} units (−{100 * cap['air_freight']:.0f}%)",
         "per_year_synthetic": m(val["air_freight"]), "at_500m": at500(val["air_freight"]), "basis": "units moved to ocean; only the premium is saved"},
        {"persona": "sponsor", "kpi": "Revenue at risk, next 13 weeks", "baseline": "not visible today",
         "modelled": f"{m(REVENUE_AT_RISK_13W)}, {CRITICAL_POSITIONS} critical positions, ranked", "per_year_synthetic": "—",
         "at_500m": "—", "basis": "the app, today"},
        {"persona": "owner", "kpi": "Stockout-weeks", "baseline": f"{sow:,.0f} / yr",
         "modelled": f"{sow * (1 - cap['lost_sales']):,.0f} / yr (−{100 * cap['lost_sales']:.0f}%)", "per_year_synthetic": "—",
         "at_500m": "—", "basis": "assumed to fall in proportion to lost sales"},
        {"persona": "owner", "kpi": "Sales lost to stockouts (revenue)", "baseline": f"{m(lost)} / yr",
         "modelled": f"{m(lost * (1 - cap['lost_sales']))} / yr (−{100 * cap['lost_sales']:.0f}%)",
         "per_year_synthetic": f"{m(lost * cap['lost_sales'])} revenue ({m(val['lost_sales'])} margin)",
         "at_500m": f"{at500(lost * cap['lost_sales'])} revenue", "basis": "lost-sales capture rate"},
        {"persona": "owner", "kpi": "OTIF", "baseline": f"{otif:.1f}%",
         "modelled": f"{100 - (100 - otif) * (1 - cap['chargebacks']):.1f}% (+{(100 - otif) * cap['chargebacks']:.1f} pts)",
         "per_year_synthetic": "—", "at_500m": "—", "basis": "late or short lines avoided at the chargeback capture rate"},
        {"persona": "owner", "kpi": "Compliance chargebacks", "baseline": f"{m(cb)} / yr",
         "modelled": f"{m(cb * (1 - cap['chargebacks']))} / yr (−{100 * cap['chargebacks']:.0f}%)",
         "per_year_synthetic": m(val["chargebacks"]), "at_500m": at500(val["chargebacks"]), "basis": "chargeback capture rate"},
        {"persona": "owner", "kpi": "Critical positions with a recorded decision", "baseline": "6% (24 of 434)",
         "modelled": "every critical position, each override with a reason", "per_year_synthetic": "—", "at_500m": "—",
         "basis": "the decision loop in the app"},
    ]


def m(v: float) -> str:
    return f"${v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K"


def write(bl: dict[str, float], rows: list[dict[str, object]], meta: dict[str, float]) -> None:
    with (ROOT / "evidence/metrics/value_model.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    lv = {x.key: x for x in levers(bl)}
    run_yr = RUN_COST_14D_LIST_USD * 365 / 14
    scales = config.SENSITIVITY_REVENUE_SCALES_USD
    L: list[str] = []
    a = L.append
    a("# Business case — what the Allocation Command Center is worth")
    a("")
    a("> Generated by `tools/value_model.py` from `evidence/metrics/kpi_baseline.csv`. Do not edit by hand.")
    a("> **All data is synthetic.** Measured numbers describe the synthetic portfolio; capture rates and")
    a("> revenue scales are labelled assumptions, to be replaced with the customer's own figures in discovery.")
    a("")
    a("## 1. The answer, first")
    a("")
    a(f"On the synthetic portfolio (**{m(meta['invoiced_per_year_usd'])} wholesale a year**), four measured leaks cost "
      f"**{m(meta['leakage_per_year_usd'])} a year, {100 * meta['leakage_per_year_usd'] / meta['invoiced_per_year_usd']:.1f}% of revenue**. "
      f"The solution is modelled to recover **{m(meta['total_low_usd'])} to {m(meta['total_high_usd'])} a year "
      f"(mid {m(meta['total_mid_usd'])}), {meta['pct_low']:.1f}% to {meta['pct_high']:.1f}% of revenue**.")
    a("")
    a(f"For a toy manufacturer with **$500M** of wholesale revenue and the same loss profile, that is "
      f"**{m(scales[1] * meta['pct_low'] / 100)} to {m(scales[1] * meta['pct_high'] / 100)} a year (mid "
      f"{m(scales[1] * meta['pct_mid'] / 100)})**, against a measured run cost of about **{m(run_yr)} a year** at list "
      "price at this scale. Retiring the incumbent AutoML licence is extra and is not in these numbers (the customer supplies it).")
    a("")
    a("And today, before any of that: the app shows **"
      f"{m(REVENUE_AT_RISK_13W)} of revenue at risk in the next 13 weeks, {CRITICAL_POSITIONS} critical positions "
      f"({m(CRITICAL_REVENUE)}) that run out within two weeks**, ranked, with a recommended split for each.")
    a("")
    a("## 2. Value by lever (per year, synthetic portfolio)")
    a("")
    a("| Lever | Buyer's KPI | Measured loss / yr | Capture low · mid · high | Value low · mid · high |")
    a("|---|---|---:|---|---|")
    for r in rows:
        a(f"| {r['name']} | {r['kpi']} | {m(float(r['measured_loss_per_year_usd']))} | "
          f"{100 * float(r['capture_low']):.0f}% · {100 * float(r['capture_mid']):.0f}% · {100 * float(r['capture_high']):.0f}% | "
          f"{m(float(r['value_low_usd']))} · **{m(float(r['value_mid_usd']))}** · {m(float(r['value_high_usd']))} |")
    a(f"| **Total** | | **{m(meta['leakage_per_year_usd'])}** | | {m(meta['total_low_usd'])} · **{m(meta['total_mid_usd'])}** · {m(meta['total_high_usd'])} |")
    a(f"| **% of revenue** | | {100 * meta['leakage_per_year_usd'] / meta['invoiced_per_year_usd']:.1f}% | | "
      f"{meta['pct_low']:.2f}% · **{meta['pct_mid']:.2f}%** · {meta['pct_high']:.2f}% |")
    a("")
    a("### Why each capture rate, and how the dollars are valued")
    a("")
    for k in ("lost_sales", "air_freight", "markdown", "chargebacks"):
        x = lv[k]
        a(f"* **{x.name}.** {x.capture_reason}" + (f" {x.factor_reason}" if x.factor_reason else ""))
    a("")
    a("## 3. Sensitivity to revenue scale")
    a("")
    a("The value is a share of revenue, so it scales with the business. Revenue scale is an **assumption**;")
    a("the shares come from the table above.")
    a("")
    a("| Wholesale revenue (assumed) | Low | Mid | High |")
    a("|---:|---:|---:|---:|")
    for s in scales:
        a(f"| ${s / 1e6:,.0f}M | {m(s * meta['pct_low'] / 100)} | **{m(s * meta['pct_mid'] / 100)}** | {m(s * meta['pct_high'] / 100)} |")
    a("")
    a("## 4. What it costs to run")
    a("")
    a("Measured from `system.billing.usage` joined to `system.billing.list_prices`, 2026-10-01 to 2026-10-15, for this build's")
    a("own resources (SQL warehouse, Lakebase, the two serving endpoints, the pipeline, jobs, the cluster, and the app): **about")
    a(f"${RUN_COST_14D_LIST_USD} at list price in 14 days, about {m(run_yr)} a year**. The app's own billing rows had not landed; it is")
    a("estimated from an identically sized always-on app in the same workspace. Foundation-model tokens for the agent and Genie")
    a("are inside the endpoint and Genie lines. Production adds daily refresh jobs and more users; even at ten times this run rate")
    a(f"({m(10 * run_yr)} a year) the cost is under {100 * 10 * run_yr / (scales[1] * meta['pct_low'] / 100):.0f}% of the low-case value at $500M.")
    a("")
    kp = kpi_impact(bl, rows, meta)
    with (ROOT / "evidence/metrics/kpi_impact.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(kp[0]))
        w.writeheader()
        w.writerows(kp)
    a("## 5. The value in each buyer's KPIs (mid case, per year)")
    a("")
    a("The same capture rates as §2, expressed in the KPIs each stakeholder is measured on. Two links are assumptions:")
    a("stockout-weeks fall in proportion to lost sales, and OTIF misses fall in proportion to chargebacks.")
    for persona, label in (("sponsor", "Executive sponsor (CFO / COO)"), ("owner", "Domain owner (VP Demand Planning / Supply Chain)")):
        a("")
        a(f"### {label}")
        a("")
        a("| KPI | Baseline | Modelled | Value / yr (synthetic) | At $500M revenue |")
        a("|---|---|---|---:|---:|")
        for r in kp:
            if r["persona"] == persona:
                a(f"| {r['kpi']} | {r['baseline']} | **{r['modelled']}** | {r['per_year_synthetic']} | {r['at_500m']} |")
    a("")
    a("**Head of AI / Data:** forecasting, the agent, governance and serving on one platform instead of an AutoML tool beside it;")
    a("lineage from the source file to the decision.")
    a("")
    a("## 6. What would make these numbers wrong")
    a("")
    a(f"* **The loss profile is synthetic.** The real business may leak more or less than {100 * meta['leakage_per_year_usd'] / meta['invoiced_per_year_usd']:.1f}% of revenue. "
      "Discovery step one is to replace the four measured losses with the customer's own (lost sales, air freight, markdown, chargebacks).")
    a(f"* **The forecast is weakest where the loss is.** On trend-driven items the high-case forecast covers {P90_COVERAGE_TREND:.0f}% of actual "
      "demand against a 90% target, so exposure there is understated and the high case is optimistic for those items.")
    a("* **Capture rates are assumptions**, set conservatively and shown as a range. They should be measured in a pilot: one licence "
      "family, one account, 8-12 weeks, decisions captured in the app against a hold-out set planned the old way.")
    a("* **Gross margin (38%) is an assumption.** Lost sales scale linearly with it.")
    a("* **Not counted:** planner time saved, shelf facings kept at the next line review, and the incumbent AutoML licence. All are real, none is measured here.")
    (ROOT / "docs/BUSINESS_CASE.md").write_text("\n".join(L) + "\n")


def main() -> int:
    bl, rows, meta = compute()
    write(bl, rows, meta)
    for c in CASES:
        print(f"{c:5} {m(meta[f'total_{c}_usd']):>8}  {meta[f'pct_{c}']:.2f}% of revenue  -> $500M: "
              f"{m(config.SENSITIVITY_REVENUE_SCALES_USD[1] * meta[f'pct_{c}'] / 100)}")
    print("wrote evidence/metrics/value_model.csv, docs/BUSINESS_CASE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
