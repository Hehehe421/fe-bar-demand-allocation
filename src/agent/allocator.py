"""Deterministic constrained-allocation logic.

This is the piece the LLM is explicitly **not** allowed to do. Splitting scarce
inventory across retailer accounts is arithmetic with contractual consequences,
so it runs in Python, and the function returns every input it used alongside the
result. The model's job is to *explain* that arithmetic in a planner's language,
not to invent it -- which is also what makes the numeric-grounding scorer in the
evaluation meaningful: every figure in the brief has to appear in this output.

Priority weight per retailer blends three things a planner actually trades off:

* **severity** -- how much is at stake and how soon, from `gold.fct_stockout_risk`
* **value density** -- margin per unit where it is visible, wholesale price per
  unit where it is not
* **contractual gap** -- how far the account is from its committed fill rate

Margin deserves a note. `gold.dim_product.gross_margin_pct` carries the Stage B
column mask, so it reads NULL for every identity except the finance persona --
including the one the agent runs as. Rather than pretend otherwise, the
allocator detects that and falls back to wholesale price as the value signal,
recording which basis it used in `value_basis`. The governance is not bypassed
and the output says plainly which number it had access to.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RetailerClaim:
    retailer_code: str
    retailer_label: str
    units_needed: int
    severity_score: float
    weeks_to_stockout: int
    wholesale_price_usd: float
    contractual_fill_commitment: float
    recent_fill_rate_pct: float | None = None
    gross_margin_pct: float | None = None
    case_pack: int = 1

    # populated by allocate()
    priority_weight: float = 0.0
    units_allocated: int = 0
    units_unmet: int = 0
    revenue_protected_usd: float = 0.0


@dataclass
class AllocationResult:
    sku_id: str
    week_end_date: str | None
    supply_available_units: int
    total_units_needed: int
    total_units_allocated: int
    total_units_unmet: int
    is_constrained: bool
    value_basis: str
    weights: dict[str, float]
    claims: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


#: Relative influence of each factor on the priority weight. Exposed so the
#: brief can state them and a planner can argue with them.
WEIGHTS = {"severity": 0.45, "value_density": 0.30, "contractual_gap": 0.25}


def _normalise(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if math.isclose(hi, lo):
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def allocate(
    sku_id: str,
    supply_available_units: int,
    claims: list[RetailerClaim],
    week_end_date: str | None = None,
) -> AllocationResult:
    """Split ``supply_available_units`` across ``claims``.

    Allocation is proportional to priority weight, capped at each account's
    actual need, rounded **down** to whole cases (shipping a part case is not a
    thing), with any remainder passed to the next account by weight so units are
    not silently lost to rounding.
    """
    notes: list[str] = []
    if not claims:
        return AllocationResult(
            sku_id=sku_id, week_end_date=week_end_date,
            supply_available_units=supply_available_units,
            total_units_needed=0, total_units_allocated=0, total_units_unmet=0,
            is_constrained=False, value_basis="none",
            weights=dict(WEIGHTS), claims=[], notes=["no at-risk accounts for this SKU"],
        )

    total_need = sum(c.units_needed for c in claims)
    constrained = supply_available_units < total_need
    if not constrained:
        notes.append(
            "Supply covers every account's need in full, so no trade-off is "
            "required and each account is allocated exactly what it needs."
        )

    # value basis: margin if the identity can see it, otherwise wholesale price
    have_margin = all(
        c.gross_margin_pct is not None and c.gross_margin_pct > 0 for c in claims
    )
    if have_margin:
        value_basis = "gross_margin_usd_per_unit"
        value = [c.wholesale_price_usd * float(c.gross_margin_pct or 0) for c in claims]
    else:
        value_basis = "wholesale_price_usd_per_unit"
        value = [c.wholesale_price_usd for c in claims]
        notes.append(
            "Gross margin is not visible to this identity because of the Unity "
            "Catalog column mask on cost and margin, so value density uses "
            "wholesale price per unit instead. Allocation ordering is unaffected "
            "where margin rates are similar across accounts."
        )

    sev = _normalise([c.severity_score for c in claims])
    val = _normalise(value)
    # how far below its contractual commitment the account currently sits
    gaps = []
    for c in claims:
        if c.recent_fill_rate_pct is None:
            gaps.append(0.5)
        else:
            gaps.append(
                max(0.0, c.contractual_fill_commitment * 100.0 - c.recent_fill_rate_pct)
            )
    gap = _normalise(gaps)

    for i, c in enumerate(claims):
        c.priority_weight = round(
            WEIGHTS["severity"] * sev[i]
            + WEIGHTS["value_density"] * val[i]
            + WEIGHTS["contractual_gap"] * gap[i],
            6,
        )

    # proportional split, capped at need, floored to whole cases
    remaining = int(supply_available_units)
    order = sorted(claims, key=lambda c: c.priority_weight, reverse=True)
    weight_sum = sum(c.priority_weight for c in claims) or 1.0

    for c in order:
        if remaining <= 0:
            c.units_allocated = 0
            continue
        target = min(
            c.units_needed,
            int(supply_available_units * c.priority_weight / weight_sum),
        )
        cp = max(1, int(c.case_pack))
        target = (target // cp) * cp
        c.units_allocated = int(min(target, remaining, c.units_needed))
        remaining -= c.units_allocated

    # pass the rounding remainder down the priority order rather than losing it
    if remaining > 0:
        for c in order:
            if remaining <= 0:
                break
            headroom = c.units_needed - c.units_allocated
            if headroom <= 0:
                continue
            cp = max(1, int(c.case_pack))
            extra = min(headroom, remaining)
            extra = (extra // cp) * cp if extra >= cp else 0
            if extra <= 0:
                continue
            c.units_allocated += int(extra)
            remaining -= int(extra)
        if remaining > 0:
            notes.append(
                f"{remaining} units remain unallocated because the residue is "
                "smaller than a full case for every account still short."
            )

    for c in claims:
        c.units_unmet = int(max(0, c.units_needed - c.units_allocated))
        c.revenue_protected_usd = round(c.units_allocated * c.wholesale_price_usd, 2)

    allocated = sum(c.units_allocated for c in claims)
    return AllocationResult(
        sku_id=sku_id,
        week_end_date=week_end_date,
        supply_available_units=int(supply_available_units),
        total_units_needed=int(total_need),
        total_units_allocated=int(allocated),
        total_units_unmet=int(max(0, total_need - allocated)),
        is_constrained=bool(constrained),
        value_basis=value_basis,
        weights=dict(WEIGHTS),
        claims=[asdict(c) for c in sorted(
            claims, key=lambda c: c.priority_weight, reverse=True
        )],
        notes=notes,
    )
