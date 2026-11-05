from sqlalchemy import Column, String, Integer, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base


class Employee(Base):
    """
    Employee model representing business employees/staff members,
    fulfilling the Employee.js architectural model requirement.
    """
    __tablename__ = "employees"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_code = Column(String(50), nullable=True, unique=True)
    department = Column(String(100), default="Marketing", nullable=False)
    designation = Column(String(100), default="Content Creator", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])


__all__ = ["Employee"]
