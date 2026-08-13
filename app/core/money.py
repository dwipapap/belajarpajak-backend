"""Integer-rupiah money math shared by every document module.

Amounts are always whole rupiah stored as ``int``; rates are basis points
(``10000`` = 100.00%) so no percentage ever becomes a float in a stored value.
Every division goes through ``Decimal`` with ``ROUND_HALF_UP`` — the rounding
rule Indonesian tax forms use — so results are reproducible across modules.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

BASIS_POINTS_SCALE = Decimal("10000")


def percent_to_basis_points(percent: float) -> int:
    rate = Decimal(str(percent))
    return int((rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def basis_points_to_percent(basis_points: int) -> float:
    return float((Decimal(basis_points) / Decimal("100")).quantize(Decimal("0.01")))


def apply_basis_points(amount: int, basis_points: int) -> int:
    """``amount`` scaled by a basis-point rate, rounded to whole rupiah."""
    value = Decimal(amount) * Decimal(basis_points) / BASIS_POINTS_SCALE
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_rupiah(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
