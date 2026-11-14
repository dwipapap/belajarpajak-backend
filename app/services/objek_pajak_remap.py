"""Old hand-made kode objek -> closest official code, per slip type.

Used by the data migration that realigns existing slips with the official
catalog (``app/data/objek_pajak.json``). Keys are the OLD codes; values the
catalog code that matches the old meaning (closest one when a module has no
exact equivalent). Applied in a single CASE update per slip type, so a chain
such as 24-100-01 -> 24-101-01 -> 24-102-01 can never double-apply.
"""

from __future__ import annotations

from app.services import objek_pajak

_PPH23_COMMON = {
    "24-100-01": "24-101-01",  # Dividen
    "24-101-01": "24-102-01",  # Bunga
    "24-102-01": "24-100-02",  # Sewa dan penggunaan harta
    "24-103-01": "24-103-01",  # Royalti (code unchanged)
    "24-104-01": "24-104-03",  # Jasa konsultan
}

_PEGAWAI_TO_MP = {
    "21-100-01": "21-100-01",
    "21-100-02": "21-100-01",  # tunjangan teratur -> penghasilan pegawai tetap
    "21-100-09": "21-100-01",
    "21-401-01": "21-100-01",
    "21-401-02": "21-100-01",
    "21-499-99": "21-100-01",
}

#: slip_type -> {old_code: new_code}
REMAP: dict[str, dict[str, str]] = {
    "bp23": dict(_PPH23_COMMON),
    "bppu": {**_PPH23_COMMON, "28-100-01": "28-409-10"},  # jasa konstruksi (pelaksana kecil)
    "sp": {
        "24-100-01": "28-419-01",  # Dividen (WPDN OP, final 4(2)); SP has no Pasal 23 objects
        "24-102-01": "28-403-02",  # Sewa harta -> persewaan tanah/bangunan
    },
    "cy": {
        "24-100-01": "28-419-01",  # Dividen
        "24-102-01": "24-105-01",  # closest Pasal 23 object in CY (no sewa object exists)
        "27-100-01": "27-102-03",  # only Pasal 26 object in CY (bunga obligasi)
    },
    "bp21": {
        "21-100-01": "21-100-35",  # gaji pegawai tetap -> closest monthly salary object
        "21-100-02": "21-100-12",  # pensiun berkala
        "21-100-09": "21-100-07",  # honorarium tenaga ahli
        "21-401-01": "21-100-35",  # imbalan pegawai tidak tetap
        "21-401-02": "21-100-24",  # upah harian/mingguan
        "21-499-99": "21-100-20",  # penghasilan bukan pegawai lainnya
    },
    "bp26": {f"27-100-0{i}": "27-100-99" for i in range(1, 6)},
    "bpnr": {
        "27-100-01": "27-101-01",  # dividen WPLN
        "27-100-04": "27-104-01",  # imbalan jasa
    },
    "bpmp": dict(_PEGAWAI_TO_MP),
}


def remap_rows(slip_type: str) -> list[tuple[str, str, str, str]]:
    """(old_code, new_code, new_name, new_kap_kjs) rows for one slip type."""
    rows = []
    for old, new in REMAP.get(slip_type, {}).items():
        obj = objek_pajak.get_object(slip_type, new)
        if obj is None:
            raise ValueError(f"{slip_type}: kode {new} tidak ada di katalog")
        rows.append((old, new, obj["nama"][:150], obj["kap_kjs"]))
    return rows
