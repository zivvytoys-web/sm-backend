import pytest
from fastapi.testclient import TestClient
from src.app import app
from src.config.env import settings
from src.config.db import SessionLocal
from src.models import User, Employee, Role
from src.services.employee_service import EmployeeService
from src.validations.employee_validation import EmployeeCreateValidation
from app.models.organization import Organization
from app.core.security import get_password_hash

client = TestClient(app)


def test_layered_architecture_modules():
    """Verify that all folders in the architecture exist, import, and function cleanly."""
    # 1. Config
    from src.config.db import engine, SessionLocal, get_db
    from src.config.env import settings, env
    assert engine is not None
    assert settings.PROJECT_NAME is not None

    # 2. Models
    assert User.__tablename__ == "users"
    assert Employee.__tablename__ == "employees"
    assert Role.ADMIN == "admin"
    assert Role.EMPLOYEE == "employee"

    # 3. Validations
    from src.validations import (
        LoginValidation,
        RegisterValidation,
        EmployeeCreateValidation,
        MediaAssetResponseValidation
    )
    login_val = LoginValidation(email="test@example.com", password="password123")
    assert login_val.email == "test@example.com"

    # 4. Middleware
    from src.middleware import (
        get_current_user,
        require_admin,
        require_creator,
        ErrorHandlingMiddleware,
        UploadMiddleware
    )
    assert get_current_user is not None
    assert UploadMiddleware is not None

    # 5. Services
    from src.services import AuthService, UserService, EmployeeService, MediaService
    assert AuthService is not None
    assert UserService is not None
    assert EmployeeService is not None
    assert MediaService is not None

    # 6. Controllers
    from src.controllers import (
        AuthController,
        UserController,
        EmployeeController,
        MediaController
    )
    assert AuthController is not None
    assert UserController is not None
    assert EmployeeController is not None
    assert MediaController is not None

    # 7. Routes
    from src.routes import api_router
    assert api_router is not None

    # 8. App & Server
    from src.server import start_server
    assert app.title == settings.PROJECT_NAME
    assert callable(start_server)


def test_layered_auth_and_employee_routes_e2e():
    """Test full controller-service-model flow for auth and employee management."""
    # 1. Login Admin
    login_res = client.post("/api/v1/auth/login", json={
        "email": "admin@socialai.com",
        "password": "password123"
    })
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Query /api/v1/auth/me through src.routes
    me_res = client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    user_data = me_res.json()
    assert user_data["email"] == "admin@socialai.com"
    org_id = user_data["organization_id"]

    # 3. Create an employee via /api/v1/employees/
    emp_payload = {
        "email": "sarah.marketing@testcorp.com",
        "full_name": "Sarah Marketing Lead",
        "employee_code": "EMP-SRH-01",
        "department": "Brand Marketing",
        "designation": "Senior Content Strategist",
        "role": "creator"
    }

    create_emp_res = client.post("/api/v1/employees/", headers=headers, json=emp_payload)
    assert create_emp_res.status_code == 201, f"Create employee failed: {create_emp_res.text}"
    emp = create_emp_res.json()
    assert emp["department"] == "Brand Marketing"
    assert emp["designation"] == "Senior Content Strategist"
    emp_id = emp["id"]

    # 4. List employees via /api/v1/employees/
    list_res = client.get("/api/v1/employees/", headers=headers)
    assert list_res.status_code == 200
    employees = list_res.json()
    assert any(e["id"] == emp_id for e in employees)

    # 5. Update employee via /api/v1/employees/{id}
    update_res = client.put(f"/api/v1/employees/{emp_id}", headers=headers, json={
        "designation": "Director of Creative Content"
    })
    assert update_res.status_code == 200
    assert update_res.json()["designation"] == "Director of Creative Content"

    # 6. Delete employee via /api/v1/employees/{id}
    del_res = client.delete(f"/api/v1/employees/{emp_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 7. Clean up user created for employee
    db = SessionLocal()
    u = db.query(User).filter(User.email == "sarah.marketing@testcorp.com").first()
    if u:
        db.delete(u)
        db.commit()
    db.close()
