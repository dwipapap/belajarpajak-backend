"""SQLModel table models. Import all here so Alembic autogenerate sees the metadata."""

from app.models.bp21 import Bp21WithholdingSlip
from app.models.bp26 import Bp26WithholdingSlip
from app.models.enrollment import Enrollment
from app.models.enums import (
    Bp21Status,
    Bp21TaxFacility,
    Bp21TaxNature,
    Bp26SptFlag,
    Bp26Status,
    Bp26TaxFacility,
    Bp26TaxNature,
    Role,
    TenantType,
)
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
    "Role",
    "TenantType",
    "Bp21Status",
    "Bp21TaxNature",
    "Bp21TaxFacility",
    "Bp26Status",
    "Bp26TaxNature",
    "Bp26TaxFacility",
    "Bp26SptFlag",
    "Tenant",
    "User",
    "SchoolClass",
    "Enrollment",
    "Bp21WithholdingSlip",
    "Bp26WithholdingSlip",
]
