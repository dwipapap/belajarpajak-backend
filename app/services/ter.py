"""TER (Tarif Efektif Rata-rata) engine for monthly PPh 21 withholding.

Sources: PP 58/2023 (TER concept and rate tables in its appendices),
PMK 168/2023 Art. 3 (TER categories by PTKP status), Art. 4 (monthly
``monthly_tax = TER_rate * monthly_gross`` for Jan-Nov and other non-final
months), Art. 5-6 (December/final-month annualization correction).

Authoritative TER numbers live ONLY in the PP 58/2023 appendix tables — do
not copy rates from blogs. The lookup table below is intentionally unseeded
(``TER_TABLE`` is empty); rates must be seeded as tariff data (like PTKP)
from the PP before the monthly lookup can be used in production. Until then
every function here is pure and testable without a database, and BPMP slips
fall back to their explicit ``rate_percent``.
"""

from __future__ import annotations

from app.core.money import apply_basis_points

#: TER category by PTKP status (PMK 168/2023 Art. 3).
TER_CATEGORY_BY_PTKP: dict[str, str] = {
    "TK/0": "A",
    "TK/1": "A",
    "K/0": "A",
    "TK/2": "B",
    "TK/3": "B",
    "K/1": "B",
    "K/2": "B",
    "K/3": "C",
}

TER_CATEGORIES: tuple[str, ...] = ("A", "B", "C")

#: Monthly-gross-bracket TER lookup, per category.
#:
#: Shape: ``{category: ((upper_bound_inclusive, rate_basis_points), ...)}``,
#: brackets ascending, last bracket open-ended via ``upper_bound = None``.
#: TODO(seed): transcribe the brackets and rates verbatim from the PP 58/2023
#: appendix tables (source of truth). No rate from any other source — blog,
#: summary table, or memory — may be entered here. Until seeded, ``lookup_rate``
#: returns ``None`` and callers fall back to the slip's explicit rate.
TER_TABLE: dict[str, tuple[tuple[int | None, int], ...]] = {}


def ter_category_for_ptkp(ptkp_status: str | None) -> str | None:
    """Map a PTKP status code to its TER category (A/B/C), or None if unknown."""
    if not ptkp_status:
        return None
    return TER_CATEGORY_BY_PTKP.get(ptkp_status.strip().upper())


def lookup_rate(category: str, monthly_gross: int) -> int | None:
    """Return the TER rate (basis points) for a category + monthly gross.

    Returns None when the category has no seeded brackets yet — callers must
    fall back to an explicit rate rather than invent one.
    """
    brackets = TER_TABLE.get(category)
    if not brackets:
        return None
    for upper_bound, rate_basis_points in brackets:
        if upper_bound is None or monthly_gross <= upper_bound:
            return rate_basis_points
    return None  # pragma: no cover - open-ended last bracket always matches


def monthly_tax(monthly_gross: int, rate_basis_points: int) -> int:
    """PMK 168/2023 Art. 4: monthly withholding = TER rate x monthly gross."""
    if monthly_gross < 0:
        raise ValueError("monthly_gross must be non-negative")
    if rate_basis_points < 0:
        raise ValueError("rate_basis_points must be non-negative")
    return apply_basis_points(monthly_gross, rate_basis_points)


def december_tax(annual_tax: int, jan_nov_total: int) -> int:
    """PMK 168/2023 Art. 5-6: December = annual Art.17 tax - Jan-Nov withheld.

    May be zero when the monthly TER already covered the annual liability;
    a negative difference means over-withheld (handled as credit, never as
    negative withholding on the slip).
    """
    return annual_tax - jan_nov_total
