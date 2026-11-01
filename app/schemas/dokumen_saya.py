"""Dokumen Saya — unified downloads for one siswa (BPE SPT, bukti potong, billing)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DokumenSayaRow(BaseModel):
    jenis: Literal["bpe_spt", "bukti_potong", "kode_billing"]
    nomor: str
    tanggal: datetime | None = None
    uraian: str
    masa: str
    download_path: str


class DokumenSayaListResponse(BaseModel):
    items: list[DokumenSayaRow]
    total: int
