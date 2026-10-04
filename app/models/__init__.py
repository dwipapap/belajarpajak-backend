"""SQLModel table models. Import all here so Alembic autogenerate sees the metadata."""

from app.models.billing import BillingCode
from app.models.enrollment import Enrollment
from app.models.enums import (
    BillingStatus,
    BuyerIdentityType,
    Role,
    SignerCapacity,
    SlipSptFlag,
    SlipStatus,
    SlipTaxFacility,
    SlipTaxNature,
    SlipType,
    TaxInputCreditStatus,
    TaxInvoiceKind,
    TaxInvoiceLineType,
    TaxInvoiceStatus,
    TaxReturnKind,
    TaxReturnStatus,
    TaxReturnType,
    TenantType,
)
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.tarif_pajak import TarifProgresifPasal17, TierPtkp
from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine
from app.models.tax_return import TaxReturn, TaxReturnLine
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
    "TaxInputCreditStatus",
    "TaxReturnKind",
    "TaxReturnType",
    "TaxReturnStatus",
    "SignerCapacity",
    "BillingStatus",
    "TaxInputInvoice",
    "TaxInputLine",
    "Tenant",
    "User",
    "SchoolClass",
    "Enrollment",
    "WithholdingSlip",
    "TaxInvoice",
    "TaxInvoiceLine",
    "TaxReturn",
    "TaxReturnLine",
    "BillingCode",
    "TierPtkp",
    "TarifProgresifPasal17",
]
