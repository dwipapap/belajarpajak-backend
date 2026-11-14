"""Official e-Bupot tax-object catalog (kode objek pajak resmi), loaded once.

The JSON lives in ``app/data/objek_pajak.json`` and is keyed by catalog module
(BPU, SP, CY, BPNR, BP21, BP26, MP). Each slip type maps to one module; BP23 is
the Pasal 23 slice of the BPU module. Facility codes in ``tarif_per_fasilitas``:
"9" tanpa fasilitas, "8" fasilitas lainnya, "7" SKD, "4" PPh DTP (others are kept
as data only). The key ``"null"`` means the rate is not facility-specific.
"""

from __future__ import annotations

import json
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from app.models.enums import SlipTaxFacility, SlipType

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "objek_pajak.json"

#: slip type -> (catalog module, optional pasal filter)
_MODULES: dict[str, tuple[str, str | None]] = {
    SlipType.bppu.value: ("BPU", None),
    SlipType.bp23.value: ("BPU", "Pasal 23"),
    SlipType.sp.value: ("SP", None),
    SlipType.cy.value: ("CY", None),
    SlipType.bp21.value: ("BP21", None),
    SlipType.bp26.value: ("BP26", None),
    SlipType.bpnr.value: ("BPNR", None),
    SlipType.bpmp.value: ("MP", None),
}

DEFAULT_FACILITY_CODE = "9"

#: Stored ``tax_facility`` value -> catalog facility code. ``skb`` is legacy
#: (kept for old rows) and behaves like "Fasilitas Lainnya".
FACILITY_CODE_BY_STORED: dict[str, str] = {
    SlipTaxFacility.none.value: "9",
    SlipTaxFacility.dtp.value: "4",
    SlipTaxFacility.treaty.value: "7",
    SlipTaxFacility.lainnya.value: "8",
    SlipTaxFacility.skb.value: "8",
    SlipTaxFacility.rate_0.value: "9",
}

#: Stored facilities whose rate is the entered one, so the catalog rate is
#: never enforced (the student states the rate / exemption).
NON_ENFORCED_FACILITIES = (
    SlipTaxFacility.skb.value,
    SlipTaxFacility.lainnya.value,
    SlipTaxFacility.rate_0.value,
)

CatalogObject = dict[str, Any]


@lru_cache(maxsize=1)
def _catalog() -> dict[str, list[CatalogObject]]:
    with _DATA_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def module_for(slip_type: str) -> str | None:
    entry = _MODULES.get(str(getattr(slip_type, "value", slip_type)))
    return entry[0] if entry else None


@cache
def _objects_for(slip_type: str) -> tuple[CatalogObject, ...]:
    entry = _MODULES.get(slip_type)
    if entry is None:
        return ()
    module, pasal = entry
    items = _catalog().get(module, [])
    if pasal is not None:
        items = [item for item in items if item["pasal"] == pasal]
    return tuple(items)


@cache
def _index_for(slip_type: str) -> dict[str, CatalogObject]:
    return {item["kode"]: item for item in _objects_for(slip_type)}


def facility_code_for(stored: Any) -> str:
    """Catalog facility code for a stored ``tax_facility`` value."""
    return FACILITY_CODE_BY_STORED.get(str(getattr(stored, "value", stored)), DEFAULT_FACILITY_CODE)


def resolve_facility(obj: CatalogObject, facility: str | None) -> dict[str, Any]:
    """Rate entry of ``obj`` for a facility code; falls back to the non-specific
    ("null") entry, then to the first entry."""
    rates = obj["tarif_per_fasilitas"]
    key = facility or DEFAULT_FACILITY_CODE
    for candidate in (key, "null", DEFAULT_FACILITY_CODE):
        if candidate in rates:
            return rates[candidate]
    return next(iter(rates.values()), {"tarif": None, "tarif_manual": True, "pph_manual": True})


def expected_rate(obj: CatalogObject, facility: str | None) -> float | None:
    """Rate percent to enforce, or None when the rate is manual / not fixed."""
    entry = resolve_facility(obj, facility)
    if entry.get("tarif") is None or entry.get("tarif_manual"):
        return None
    return float(entry["tarif"])


def list_objects(slip_type: str, facility: str | None = None) -> list[dict[str, Any]]:
    """Catalog rows of a slip type, resolved for a facility code."""
    key = str(getattr(slip_type, "value", slip_type))
    rows = []
    for item in _objects_for(key):
        entry = resolve_facility(item, facility)
        rows.append(
            {
                "kode": item["kode"],
                "nama": item["nama"],
                "pasal": item["pasal"],
                "sifat": item["sifat"],
                "kap_kjs": item["kap_kjs"],
                "tarif": entry.get("tarif"),
                "tarif_manual": bool(entry.get("tarif_manual")),
                "pph_manual": bool(entry.get("pph_manual")),
            }
        )
    return rows


def get_object(slip_type: str, code: str) -> CatalogObject | None:
    return _index_for(str(getattr(slip_type, "value", slip_type))).get(code)
