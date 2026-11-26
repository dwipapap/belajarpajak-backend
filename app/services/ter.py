"""TER (Tarif Efektif Rata-rata) engine for monthly PPh 21 withholding.

Sources: PP 58/2023 (TER concept and rate tables in its appendices),
PMK 168/2023 Art. 3 (TER categories by PTKP status), Art. 4 (monthly
``monthly_tax = TER_rate * monthly_gross`` for Jan-Nov and other non-final
months), Art. 5-6 (December/final-month annualization correction).

Authoritative TER numbers live ONLY in the PP 58/2023 appendix tables (as
transcribed in PMK 168/2023 Lampiran I); per-category layer boundaries below
mirror that transcription. Each category peaks at 34% open-ended.
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
#: Each entry is (batas_atas_bruto, A_bp, B_bp, C_bp) converted from decimal
#: to basis points (1% = 100 bp). Full transcription of the PMK 168/2023
#: Lampiran I tables (mirroring the PP 58/2023 appendix); every category
#: peaks at 34% open-ended (A s.d. Rp1.400M, B s.d. Rp1.405M, C s.d.
#: Rp1.419M, then the peak layer).
TER_TABLE: dict[str, tuple[tuple[int | None, int], ...]] = {
    "A": (
        (5_400_000, 0),
        (5_650_000, 25),
        (5_950_000, 50),
        (6_300_000, 75),
        (6_750_000, 100),
        (7_500_000, 125),
        (8_550_000, 150),
        (9_650_000, 175),
        (10_050_000, 200),
        (10_350_000, 225),
        (10_700_000, 250),
        (11_050_000, 300),
        (11_600_000, 350),
        (12_500_000, 400),
        (13_750_000, 500),
        (15_100_000, 600),
        (16_950_000, 700),
        (19_750_000, 800),
        (24_150_000, 900),
        (26_450_000, 1000),
        (28_000_000, 1100),
        (30_050_000, 1200),
        (32_400_000, 1300),
        (35_400_000, 1400),
        (39_100_000, 1500),
        (43_850_000, 1600),
        (47_800_000, 1700),
        (51_400_000, 1800),
        (56_300_000, 1900),
        (62_200_000, 2000),
        (68_600_000, 2100),
        (77_500_000, 2200),
        (89_000_000, 2300),
        (103_000_000, 2400),
        (125_000_000, 2500),
        (157_000_000, 2600),
        (206_000_000, 2700),
        (337_000_000, 2800),
        (454_000_000, 2900),
        (550_000_000, 3000),
        (695_000_000, 3100),
        (910_000_000, 3200),
        (1_400_000_000, 3300),
        (None, 3400),
    ),
    "B": (
        (6_200_000, 0),
        (6_500_000, 25),
        (6_850_000, 50),
        (7_300_000, 75),
        (9_200_000, 100),
        (10_750_000, 150),
        (11_250_000, 200),
        (11_600_000, 250),
        (12_600_000, 300),
        (13_600_000, 400),
        (14_950_000, 500),
        (16_400_000, 600),
        (18_450_000, 700),
        (21_850_000, 800),
        (26_000_000, 900),
        (27_700_000, 1000),
        (29_350_000, 1100),
        (31_450_000, 1200),
        (33_950_000, 1300),
        (37_100_000, 1400),
        (41_100_000, 1500),
        (45_800_000, 1600),
        (49_500_000, 1700),
        (53_800_000, 1800),
        (58_500_000, 1900),
        (64_000_000, 2000),
        (71_000_000, 2100),
        (80_000_000, 2200),
        (93_000_000, 2300),
        (109_000_000, 2400),
        (129_000_000, 2500),
        (163_000_000, 2600),
        (211_000_000, 2700),
        (374_000_000, 2800),
        (459_000_000, 2900),
        (555_000_000, 3000),
        (704_000_000, 3100),
        (957_000_000, 3200),
        (1_405_000_000, 3300),
        (None, 3400),
    ),
    "C": (
        (6_600_000, 0),
        (6_950_000, 25),
        (7_350_000, 50),
        (7_800_000, 75),
        (8_850_000, 100),
        (9_800_000, 125),
        (10_950_000, 150),
        (11_200_000, 175),
        (12_050_000, 200),
        (12_950_000, 300),
        (14_150_000, 400),
        (15_550_000, 500),
        (17_050_000, 600),
        (19_500_000, 700),
        (22_700_000, 800),
        (26_600_000, 900),
        (28_100_000, 1000),
        (30_100_000, 1100),
        (32_600_000, 1200),
        (35_400_000, 1300),
        (38_900_000, 1400),
        (43_000_000, 1500),
        (47_400_000, 1600),
        (51_200_000, 1700),
        (55_800_000, 1800),
        (60_400_000, 1900),
        (66_700_000, 2000),
        (74_500_000, 2100),
        (83_200_000, 2200),
        (95_600_000, 2300),
        (110_000_000, 2400),
        (134_000_000, 2500),
        (169_000_000, 2600),
        (221_000_000, 2700),
        (390_000_000, 2800),
        (463_000_000, 2900),
        (561_000_000, 3000),
        (709_000_000, 3100),
        (965_000_000, 3200),
        (1_419_000_000, 3300),
        (None, 3400),
    ),
}


def ter_category_for_ptkp(ptkp_status: str | None) -> str | None:
    """Map a PTKP status code to its TER category (A/B/C), or None if unknown."""
    if not ptkp_status:
        return None
    return TER_CATEGORY_BY_PTKP.get(ptkp_status.strip().upper())


def lookup_rate(category: str, monthly_gross: int) -> int | None:
    """Return the TER rate (basis points) for a category + monthly gross.

    Returns None only when the category has no brackets. Gross above the
    highest finite layer uses the open-ended peak layer (34% official rate).
    """
    brackets = TER_TABLE.get(category)
    if not brackets:
        return None
    for upper_bound, rate_basis_points in brackets:
        if upper_bound is None or monthly_gross <= upper_bound:
            return rate_basis_points
    # Defensive: no open-ended last bracket → approximate with last rate.
    return brackets[-1][1]


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
