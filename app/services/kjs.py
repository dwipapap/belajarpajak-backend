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
    "pasal_21": "Pasal 21",
    "pasal_22": "Pasal 22",
    "pasal_23": "Pasal 23",
    "pasal_26": "Pasal 26",
}

#: KJS groups for SPT PPh 21/26, keyed by pasal section code. Only codes already
#: used elsewhere in the simulator appear here — no new KJS invented.
KJS_PPH_21_26: dict[str, list[str]] = {
    "pasal_21": ["411121-100"],
    "pasal_26": ["411127-110", "411127-460"],
}
#: Single KJS for SPT Masa PPN kurang bayar.
KJS_PPN_MASA = "411121-100"

#: Fallback KJS for a BPMP slip that carries no kap_kjs (PPh 21 masa default).
KJS_BPMP_DEFAULT = "411121-100"

#: Fallback KJS for a BP23 slip that carries no kap_kjs (jasa default).
KJS_BP23_DEFAULT = "411124-100"
#: KJS for BP26 (luar negeri) slips.
KJS_BP26_DEFAULT = "411127-110"


def pasal_for_kjs(kjs: str) -> str | None:
    """Map a KAP-KJS code back to its pasal section code, or None if unknown."""
    for pasal, codes in KJS_PPH_UNIFIKASI.items():
        if kjs in codes:
            return pasal
    for pasal, codes in KJS_PPH_21_26.items():
        if kjs in codes:
            return pasal
    return None


#: Whitelist for Layanan Mandiri Kode Billing (YAGNI: 1 code, 1 period, 1 amount).
#: Matches what students already meet in slips — no new codes to memorise.
#: NOTE: local PPN masa uses 411121-100 (historical); the DJP catalog number
#: commonly cited for PPN (411211) is intentionally NOT introduced here to avoid
#: breaking tests, PDFs, and seeded data. Dropdowns show human labels instead.
MANDIRI_KJS: tuple[str, ...] = (
    "411121-100",
    "411124-100",
    "411127-100",
    "411128-100",
    "411122-100",
)

MANDIRI_KJS_LABELS: dict[str, str] = {
    "411121-100": "PPN Masa / PPh 21 Setor Sendiri",
    "411124-100": "PPh 23",
    "411127-100": "PPh 26",
    "411128-100": "PPh Final 4(2)",
    "411122-100": "PPh 22",
}


def is_mandiri_kjs(kap_kjs: str) -> bool:
    """Whether a KAP-KJS code may be used for self-service billing."""
    return kap_kjs in MANDIRI_KJS


def parse_masa_pajak(masa_pajak: str) -> tuple[int, int]:
    """Split MMYYYY into (month, year); raises ValueError on bad format."""
    if len(masa_pajak) != 6 or not masa_pajak.isdigit():
        raise ValueError("Masa pajak harus MMYYYY (6 digit)")
    month, year = int(masa_pajak[:2]), int(masa_pajak[2:])
    if not 1 <= month <= 12:
        raise ValueError("Bulan masa pajak harus 01–12")
    if not 2020 <= year <= 2100:
        raise ValueError("Tahun masa pajak harus 2020–2100")
    return month, year
