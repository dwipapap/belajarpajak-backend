"""KAP-KJS code catalog for the SPT Masa forms.

KAP-KJS ("Kode Administrasi Pembayaran — Kode Jenis Setoran") identifies what a
payment settles. The SPT Masa PPh Unifikasi Induk lists one row per KJS under
each pasal; the SPT Masa PPN bills through the single masa-PPN KJS. The codes
below mirror the real catalog visible in the reference material; grouping keys
(``pasal_4_2`` etc.) match ``TaxReturnLine.section``.
"""

from __future__ import annotations

#: KJS groups for SPT PPh Unifikasi, keyed by pasal section code.
KJS_PPH_UNIFIKASI: dict[str, list[str]] = {
    "pasal_4_2": ["411128-100", "411128-402", "411128-403"],
    "pasal_15": ["411115-600", "411129-600"],
    "pasal_22": ["411122-100", "411122-900", "411122-910"],
    "pasal_23": ["411124-100"],
    "pasal_26": ["411127-110", "411127-460"],
}

#: Human labels for the pasal sections, as they appear on the Induk table.
PASAL_LABELS: dict[str, str] = {
    "pasal_4_2": "Pasal 4 Ayat 2",
    "pasal_15": "Pasal 15",
    "pasal_22": "Pasal 22",
    "pasal_23": "Pasal 23",
    "pasal_26": "Pasal 26",
}

#: Single KJS for SPT Masa PPN kurang bayar.
KJS_PPN_MASA = "411121-100"

#: Fallback KJS for a BP23 slip that carries no kap_kjs (jasa default).
KJS_BP23_DEFAULT = "411124-100"
#: KJS for BP26 (luar negeri) slips.
KJS_BP26_DEFAULT = "411127-110"


def pasal_for_kjs(kjs: str) -> str | None:
    """Map a KAP-KJS code back to its pasal section code, or None if unknown."""
    for pasal, codes in KJS_PPH_UNIFIKASI.items():
        if kjs in codes:
            return pasal
    return None
