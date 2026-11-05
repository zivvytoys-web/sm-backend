from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


class EmployeeCreateValidation(BaseModel):
    user_id: Optional[int] = None
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    employee_code: Optional[str] = None
    department: str = "Marketing"
    designation: str = "Content Creator"
    role: str = "creator"


class EmployeeUpdateValidation(BaseModel):
    department: Optional[str] = None
    designation: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None


class EmployeeResponseValidation(BaseModel):
    id: int
    user_id: int
    organization_id: int
    employee_code: Optional[str] = None
    department: str
    designation: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


__all__ = [
    "EmployeeCreateValidation",
    "EmployeeUpdateValidation",
    "EmployeeResponseValidation"
]
