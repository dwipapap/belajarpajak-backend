"""Simulated Kode Otorisasi check shared by all issuing document types.

e-Bupot slips and e-Faktur keluaran both gate issuing behind a learning proxy
for the reference portal's Kode Otorisasi (min 6 characters). One helper keeps
every module's rule identical.
"""

from __future__ import annotations

from fastapi import HTTPException, status


def require_kode_otorisasi(kode_otorisasi: str | None) -> None:
    """Kode Otorisasi simulasi wajib minimal 6 karakter untuk menerbitkan dokumen."""
    if kode_otorisasi is None or str(kode_otorisasi).strip() == "":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kode Otorisasi wajib diisi minimal 6 karakter",
        )
    if len(str(kode_otorisasi).strip()) < 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kode Otorisasi simulasi minimal 6 karakter",
        )
