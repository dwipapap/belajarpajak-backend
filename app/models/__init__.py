"""SQLModel table models. Import all here so Alembic autogenerate sees the metadata."""

from app.models.enrollment import Enrollment
from app.models.enums import Role, TenantType
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User

__all__ = ["Role", "TenantType", "Tenant", "User", "SchoolClass", "Enrollment"]
