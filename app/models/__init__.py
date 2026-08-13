"""SQLModel table models. Import all here so Alembic autogenerate sees the metadata."""

from app.models.enrollment import Enrollment
from app.models.enums import (
    BuyerIdentityType,
    Role,
    SlipSptFlag,
    SlipStatus,
    SlipTaxFacility,
    SlipTaxNature,
    SlipType,
    TaxInvoiceKind,
    TaxInvoiceLineType,
    TaxInvoiceStatus,
    TenantType,
)
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.tarif_pajak import TarifProgresifPasal17, TierPtkp
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
    "Role",
    "TenantType",
    "SlipType",
    "SlipStatus",
    "SlipTaxNature",
    "SlipTaxFacility",
    "SlipSptFlag",
    "TaxInvoiceStatus",
    "TaxInvoiceKind",
    "TaxInvoiceLineType",
    "BuyerIdentityType",
    "Tenant",
    "User",
    "SchoolClass",
    "Enrollment",
    "WithholdingSlip",
    "TaxInvoice",
    "TaxInvoiceLine",
    "TierPtkp",
    "TarifProgresifPasal17",
]
