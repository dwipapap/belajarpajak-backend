"""Read-only reference data (official tax-object catalog)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import require_roles
from app.models.enums import Role, SlipType
from app.schemas.objek_pajak import ObjekPajakRead
from app.services import objek_pajak

router = APIRouter(prefix="/referensi", tags=["referensi"])

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))


@router.get("/objek-pajak", response_model=list[ObjekPajakRead], dependencies=[_roles])
def list_objek_pajak(
    jenis: SlipType,
    fasilitas: Annotated[str | None, Query(max_length=8)] = None,
) -> list[dict]:
    """Official kode objek pajak for a slip type, resolved for a facility code."""
    return objek_pajak.list_objects(jenis.value, fasilitas)
