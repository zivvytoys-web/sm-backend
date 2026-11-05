from sqlalchemy.orm import Session
from src.services.employee_service import EmployeeService
from src.models.user import User
from src.validations.employee_validation import EmployeeCreateValidation, EmployeeUpdateValidation


class EmployeeController:
    """HTTP Controller orchestrating Employee / Team operations."""

    @staticmethod
    def list_employees(db: Session, current_user: User):
        return EmployeeService.list_employees(db, current_user.organization_id)

    @staticmethod
    def get_employee(db: Session, employee_id: int, current_user: User):
        return EmployeeService.get_employee(db, employee_id, current_user.organization_id)

    @staticmethod
    def create_employee(db: Session, current_user: User, data: EmployeeCreateValidation):
        return EmployeeService.create_employee(db, current_user.organization_id, data)

    @staticmethod
    def update_employee(db: Session, employee_id: int, current_user: User, data: EmployeeUpdateValidation):
        return EmployeeService.update_employee(db, employee_id, current_user.organization_id, data)

    @staticmethod
    def delete_employee(db: Session, employee_id: int, current_user: User):
        EmployeeService.delete_employee(db, employee_id, current_user.organization_id)
        return {"success": True, "message": f"Employee #{employee_id} deleted successfully"}


__all__ = ["EmployeeController"]
