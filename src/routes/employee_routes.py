from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from src.config.db import get_db
from src.controllers.employee_controller import EmployeeController
from src.middleware.auth_middleware import get_current_user, require_admin
from src.models.user import User
from src.validations.employee_validation import (
    EmployeeCreateValidation,
    EmployeeUpdateValidation,
    EmployeeResponseValidation
)

router = APIRouter(prefix="/employees", tags=["Employees"])


@router.get("/", response_model=List[EmployeeResponseValidation])
def list_employees(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all employees in the current active business organization."""
    return EmployeeController.list_employees(db, current_user)


@router.post("/", response_model=EmployeeResponseValidation, status_code=status.HTTP_201_CREATED)
def create_employee(
    data: EmployeeCreateValidation,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Add a new employee / team member to the organization."""
    return EmployeeController.create_employee(db, current_user, data)


@router.get("/{employee_id}", response_model=EmployeeResponseValidation)
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get employee details by ID."""
    return EmployeeController.get_employee(db, employee_id, current_user)


@router.put("/{employee_id}", response_model=EmployeeResponseValidation)
def update_employee(
    employee_id: int,
    data: EmployeeUpdateValidation,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Update employee details."""
    return EmployeeController.update_employee(db, employee_id, current_user, data)


@router.delete("/{employee_id}", status_code=status.HTTP_200_OK)
def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Remove employee from the organization."""
    return EmployeeController.delete_employee(db, employee_id, current_user)


__all__ = ["router"]
