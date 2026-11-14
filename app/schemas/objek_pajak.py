"""Response schema for the official tax-object catalog."""

from __future__ import annotations

from pydantic import BaseModel


class ObjekPajakRead(BaseModel):
    kode: str
    nama: str
    pasal: str
    sifat: str | None
    kap_kjs: str
    tarif: float | None
    tarif_manual: bool
    pph_manual: bool
