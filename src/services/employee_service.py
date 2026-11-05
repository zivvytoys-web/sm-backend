import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from src.models.employee import Employee
from src.models.user import User
from src.models.user_organization import UserOrganization
from src.models.role import UserRole
from src.validations.employee_validation import EmployeeCreateValidation, EmployeeUpdateValidation
from src.utils.helper import get_password_hash


class EmployeeService:
    """Business logic for Employee & Team Member operations."""

    @staticmethod
    def list_employees(db: Session, organization_id: int) -> List[Employee]:
        return db.query(Employee).filter(
            Employee.organization_id == organization_id
        ).order_by(Employee.created_at.desc()).all()

    @staticmethod
    def get_employee(db: Session, employee_id: int, organization_id: int) -> Employee:
        emp = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.organization_id == organization_id
        ).first()
        if not emp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee #{employee_id} not found."
            )
        return emp

    @staticmethod
    def create_employee(db: Session, organization_id: int, data: EmployeeCreateValidation) -> Employee:
        # If user_id not provided, check or create a user by email
        user_id = data.user_id
        if not user_id and data.email:
            existing_user = db.query(User).filter(User.email == data.email).first()
            if existing_user:
                user_id = existing_user.id
            else:
                new_user = User(
                    email=data.email,
                    hashed_password=get_password_hash("Password123!"),
                    full_name=data.full_name or data.email.split("@")[0].title(),
                    role=UserRole(data.role) if data.role in UserRole._value2member_map_ else UserRole.CREATOR,
                    organization_id=organization_id,
                    is_active=True
                )
                db.add(new_user)
                db.flush()
                user_id = new_user.id

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Must provide user_id or valid email to register employee."
            )

        # Generate employee code if omitted
        emp_code = data.employee_code or f"EMP-{uuid.uuid4().hex[:6].upper()}"

        emp = Employee(
            user_id=user_id,
            organization_id=organization_id,
            employee_code=emp_code,
            department=data.department,
            designation=data.designation,
            is_active=True
        )
        db.add(emp)

        # Also add or update UserOrganization
        user_org = db.query(UserOrganization).filter(
            UserOrganization.user_id == user_id,
            UserOrganization.organization_id == organization_id
        ).first()
        if not user_org:
            user_org = UserOrganization(
                user_id=user_id,
                organization_id=organization_id,
                role=UserRole(data.role) if data.role in UserRole._value2member_map_ else UserRole.CREATOR,
                is_default=False
            )
            db.add(user_org)

        db.commit()
        db.refresh(emp)
        return emp

    @staticmethod
    def update_employee(db: Session, employee_id: int, organization_id: int, data: EmployeeUpdateValidation) -> Employee:
        emp = EmployeeService.get_employee(db, employee_id, organization_id)
        update_data = data.model_dump(exclude_unset=True)
        for key, val in update_data.items():
            if hasattr(emp, key):
                setattr(emp, key, val)
        db.commit()
        db.refresh(emp)
        return emp

    @staticmethod
    def delete_employee(db: Session, employee_id: int, organization_id: int) -> bool:
        emp = EmployeeService.get_employee(db, employee_id, organization_id)
        db.delete(emp)
        db.commit()
        return True


__all__ = ["EmployeeService"]
