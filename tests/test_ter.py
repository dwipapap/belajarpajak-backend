"""Unit tests for the TER engine (PP 58/2023 + PMK 168/2023).

The authoritative TER rates live ONLY in the PP 58/2023 appendix tables and
are not transcribed here. Numeric vectors against the PMK worked examples
stay skipped until those numbers are copied verbatim from the primary source;
everything else below is structural (category mapping, pure arithmetic).
"""

from __future__ import annotations

import pytest

from app.services import ter


def test_ter_category_covers_all_ptkp_statuses() -> None:
    assert ter.ter_category_for_ptkp("TK/0") == "A"
    assert ter.ter_category_for_ptkp("TK/1") == "A"
    assert ter.ter_category_for_ptkp("K/0") == "A"
    assert ter.ter_category_for_ptkp("TK/2") == "B"
    assert ter.ter_category_for_ptkp("TK/3") == "B"
    assert ter.ter_category_for_ptkp("K/1") == "B"
    assert ter.ter_category_for_ptkp("K/2") == "B"
    assert ter.ter_category_for_ptkp("K/3") == "C"


def test_ter_category_is_case_insensitive_and_null_safe() -> None:
    assert ter.ter_category_for_ptkp("tk/0") == "A"
    assert ter.ter_category_for_ptkp(" k/3 ") == "C"
    assert ter.ter_category_for_ptkp(None) is None
    assert ter.ter_category_for_ptkp("") is None
    assert ter.ter_category_for_ptkp("X/9") is None


def test_monthly_tax_is_rate_times_gross() -> None:
    # Structural: caller-supplied rate, NOT a claimed TER rate.
    assert ter.monthly_tax(10_000_000, 500) == 500_000
    assert ter.monthly_tax(0, 500) == 0
    assert ter.monthly_tax(10_000_000, 0) == 0


def test_monthly_tax_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError):
        ter.monthly_tax(-1, 500)
    with pytest.raises(ValueError):
        ter.monthly_tax(1_000_000, -1)


def test_december_tax_is_annual_minus_jan_nov() -> None:
    # Structural: PMK 168/2023 Art. 5-6 shape with placeholder operands.
    assert ter.december_tax(6_000_000, 5_500_000) == 500_000
    assert ter.december_tax(5_500_000, 5_500_000) == 0
    # Over-withheld months surface as a negative difference (credit path).
    assert ter.december_tax(5_000_000, 5_500_000) == -500_000


def test_lookup_rate_uses_seeded_table_up_to_32_5jt() -> None:
    # Seeded layers (verified to Rp32,5jt): 8jt gross hits the 8,55jt layer
    # → A 125bp, B 175bp, C 200bp.
    assert ter.lookup_rate("A", 8_000_000) == 125
    assert ter.lookup_rate("B", 8_000_000) == 175
    assert ter.lookup_rate("C", 8_000_000) == 200
    # monthly_tax integration: 8jt x category rate.
    assert ter.monthly_tax(8_000_000, ter.lookup_rate("A", 8_000_000) or 0) == 100_000
    assert ter.monthly_tax(8_000_000, ter.lookup_rate("B", 8_000_000) or 0) == 140_000
    assert ter.monthly_tax(8_000_000, ter.lookup_rate("C", 8_000_000) or 0) == 160_000
    # Full Lampiran I table: layers above Rp32,5jt resolve to official rates,
    # each category peaking at 34% open-ended.
    assert ter.lookup_rate("A", 35_400_000) == 1400
    assert ter.lookup_rate("A", 100_000_000) == 2400
    assert ter.lookup_rate("A", 2_000_000_000) == 3400
    assert ter.lookup_rate("B", 1_405_000_000) == 3300
    assert ter.lookup_rate("B", 2_000_000_000) == 3400
    assert ter.lookup_rate("C", 1_419_000_000) == 3300
    assert ter.lookup_rate("C", 2_000_000_000) == 3400
    assert ter.lookup_rate("UNKNOWN", 8_000_000) is None


@pytest.mark.skip(
    reason=(
        "TODO(seed): numeric vectors must be transcribed verbatim from the "
        "PMK 168/2023 worked examples with PP 58/2023 appendix rates; "
        "no authoritative numbers available in-repo, and guessing is forbidden."
    )
)
def test_pmk_worked_examples_per_category() -> None:  # pragma: no cover
    raise AssertionError("unreachable: transcription pending")
