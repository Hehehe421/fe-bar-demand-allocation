"""The executive narrative, computed from the data. No language model, no invention.

Every sentence on the executive tab is a template filled with numbers that were just read
from Lakebase or the governed metric views, so nothing here can drift from the dashboards
or hallucinate a figure. (The agent, by contrast, scored 74% on numeric grounding.)

Pure functions: no I/O, so they are unit-tested in tests/test_app_pure.py.
"""

from __future__ import annotations

from typing import Any


def money(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e6:
        return f"${v / 1e6:.2f}M"
    if a >= 1e4:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def _tier(ex: dict[str, Any], tier: str, key: str) -> float:
    for t in ex["tiers"]:
        if t["risk_tier"] == tier:
            return float(t[key])
    return 0.0


def totals(ex: dict[str, Any]) -> dict[str, float]:
    return {
        "rev": sum(float(t["rev"]) for t in ex["tiers"]),
        "units": sum(float(t["units"]) for t in ex["tiers"]),
        "rows": sum(int(t["n"]) for t in ex["tiers"]),
        "critical": int(_tier(ex, "CRITICAL", "n")),
        "critical_rev": _tier(ex, "CRITICAL", "rev"),
    }


def headline(ex: dict[str, Any]) -> tuple[str, str]:
    t = totals(ex)
    cov = ex["coverage"]
    if t["rows"] == 0:
        return "No stockout risk in scope.", "Nothing is projected to run short in the next 13 weeks."
    soon = int(cov["soon"] or 0)
    h = (f"{money(t['rev'])} of revenue is exposed to stockouts across "
         f"{int(t['rows']):,} product-and-account positions.")
    if t["critical"]:
        sub = (f"{t['critical']:,} are critical ({money(t['critical_rev'])}), and "
               + ("all of them" if soon == t["critical"] else f"{soon:,} of them")
               + " run out within two weeks.")
    else:
        sub = "None are critical."
    return h, sub


def insights(ex: dict[str, Any], perf: dict[str, Any] | None,
             dc: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Ordered list of {level, lead, text}; level is crit | warn | info | ok."""
    out: list[dict[str, str]] = []
    t = totals(ex)
    cov = ex["coverage"]
    crit, decided = int(cov["critical"] or 0), int(cov["decided"] or 0)

    if crit:
        pct = 100 * decided / crit
        out.append({"level": "crit" if pct < 50 else "warn", "lead": "Decisions are behind the risk.",
                    "text": f"Only {decided} of {crit} critical positions ({pct:.0f}%) have a recorded "
                            f"allocation decision."})

    if ex["families"] and t["rev"]:
        f = ex["families"][0]
        share = 100 * float(f["rev"]) / t["rev"]
        out.append({"level": "warn" if share >= 35 else "info", "lead": "Exposure is concentrated.",
                    "text": f"{f['license_family'].replace('_', ' ').title()} carries {share:.0f}% of the "
                            f"exposure ({money(float(f['rev']))}) from {int(f['n']):,} positions."})

    if perf:
        for fam in perf["families"]:
            if fam["p90_cov"] < 85 and fam["family"] == "SOCIAL_TREND":
                out.append({"level": "warn", "lead": "The forecast is least reliable where it matters most.",
                            "text": f"On {fam['family'].replace('_', ' ').title()} items the high-case forecast "
                                    f"covers only {fam['p90_cov']:.0f}% of actual demand against a 90% target, "
                                    f"so the exposure above is likely understated."})
        for a in perf["accounts"]:
            if a.get("commit") is not None and a["fill"] < a["commit"]:
                out.append({"level": "warn", "lead": f"{a['account']} is below its fill-rate commitment.",
                            "text": f"{a['fill']:.1f}% delivered against a {a['commit']:.0f}% contractual "
                                    f"commitment ({a['fill'] - a['commit']:+.1f} points)."})

    if dc:
        total = sum(d["revenue"] for d in dc) or 1.0
        top = max(dc, key=lambda d: d["revenue"])
        out.append({"level": "info", "lead": "Where it hurts.",
                    "text": f"The {top['label']} serves {100 * top['revenue'] / total:.0f}% of the exposed "
                            f"revenue ({money(top['revenue'])})."})

    if ex["accounts"] and len(ex["accounts"]) > 1 and t["rev"]:
        a = ex["accounts"][0]
        out.append({"level": "info", "lead": "Largest account exposure.",
                    "text": f"{a['retailer_code']}: {money(float(a['rev']))} "
                            f"({100 * float(a['rev']) / t['rev']:.0f}%), {int(a['critical'])} critical positions."})
    return out


def kpi_status(kind: str, value: float | None, target: float | None = None) -> tuple[str, str]:
    """(level, label) for a KPI chip. Always pairs an icon with text in the UI."""
    if value is None:
        return "info", "No data"
    if kind == "fill":
        if target is None:
            return "info", "Measured"
        return ("ok", "At or above commitment") if value >= target else ("warn", "Below commitment")
    if kind == "coverage":
        gap = 90.0 - value
        return ("ok", "On target") if gap <= 2 else ("warn", f"{gap:.0f} pts under target")
    if kind == "wape_vs_naive":
        return ("ok", f"{target - value:.0f} pts better than naive") if target and value < target else ("warn", "Not beating naive")
    return "info", "Measured"


def fill_status(overall: float | None, accounts: list[dict[str, Any]], scope: str | None) -> tuple[str, str]:
    """Fill-rate chip. One account below its contractual commitment is a warning even when the
    blended number looks healthy: an average commitment is meaningless to a retailer."""
    if overall is None:
        return "info", "No data"
    rows = [a for a in accounts if a.get("commit") is not None and (scope is None or a["account"] == scope)]
    below = [a for a in rows if a["fill"] < a["commit"]]
    if below:
        names = ", ".join(a["account"] for a in below)
        return "warn", f"Below commitment: {names}"
    return ("ok", "All accounts at or above commitment") if rows else ("info", "Measured")
