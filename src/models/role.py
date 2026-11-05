from enum import Enum
from app.utils.enums import UserRole


class Role(str, Enum):
    """System-wide role definitions matching the architecture."""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    CREATOR = "creator"
    VIEWER = "viewer"
    EMPLOYEE = "employee"


__all__ = ["Role", "UserRole"]
