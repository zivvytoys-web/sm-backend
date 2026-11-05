import pytest
from fastapi.testclient import TestClient
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.models.user_organization import UserOrganization
from app.core.security import get_password_hash
from app.utils.enums import UserRole


def test_multi_business_workflow(client: TestClient, db_session):
    # Cleanup previous run entities if any
    old_admin = db_session.query(User).filter(User.email == "test_super_admin@example.com").first()
    if old_admin:
        db_session.query(UserOrganization).filter(UserOrganization.user_id == old_admin.id).delete()
        db_session.delete(old_admin)
        db_session.commit()

    # 1. Setup predefined businesses in test DB
    biz1 = Organization(
        name="Predefined Store Alpha",
        slug="store-alpha",
        description="Alpha Fashion & Apparel",
        is_active=True,
        settings={"flagshipAddr": "123 Alpha St, Chennai", "phone": "+91 99999 11111"}
    )
    biz2 = Organization(
        name="Predefined Store Beta",
        slug="store-beta",
        description="Beta Luxury Horology",
        is_active=True,
        settings={"flagshipAddr": "456 Beta Ave, Bangalore", "phone": "+91 99999 22222"}
    )
    db_session.add(biz1)
    db_session.add(biz2)
    db_session.flush()

    bp1 = BrandProfile(
        organization_id=biz1.id,
        name="Alpha Brand",
        industry="Fashion",
        tagline="Style Alpha",
        location="Chennai",
        color_palette=["#4f46e5", "#06b6d4"]
    )
    bp2 = BrandProfile(
        organization_id=biz2.id,
        name="Beta Brand",
        industry="Watches",
        tagline="Time Beta",
        location="Bangalore",
        color_palette=["#1e293b", "#d97706"]
    )
    db_session.add(bp1)
    db_session.add(bp2)

    # 2. Setup Super Admin user
    admin_user = User(
        email="test_super_admin@example.com",
        hashed_password=get_password_hash("adminPass123!"),
        full_name="Global Super Admin",
        role=UserRole.SUPER_ADMIN,
        organization_id=biz1.id,
        is_active=True,
        is_verified=True
    )
    db_session.add(admin_user)
    db_session.flush()

    # Assign admin to both businesses
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=biz1.id, role=UserRole.SUPER_ADMIN, is_default=True))
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=biz2.id, role=UserRole.SUPER_ADMIN, is_default=False))
    db_session.commit()

    # 3. Test Admin Login
    admin_login_res = client.post("/api/v1/auth/login", json={
        "email": "test_super_admin@example.com",
        "password": "adminPass123!"
    })
    assert admin_login_res.status_code == 200
    admin_token = admin_login_res.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 4. Test Available Businesses API (Public)
    avail_res = client.get("/api/v1/auth/available-businesses")
    assert avail_res.status_code == 200
    avail_data = avail_res.json()
    assert len(avail_data) >= 2
    assert any(b["name"] == "Predefined Store Alpha" for b in avail_data)

    # 5. Test Admin Businesses API (/admin/businesses)
    admin_biz_res = client.get("/api/v1/admin/businesses", headers=admin_headers)
    assert admin_biz_res.status_code == 200
    admin_biz_data = admin_biz_res.json()
    assert len(admin_biz_data) >= 2
    alpha_biz = next(b for b in admin_biz_data if b["name"] == "Predefined Store Alpha")
    assert alpha_biz["brand_profile"]["name"] == "Alpha Brand"

    # 6. Test Admin Create New Business
    create_biz_res = client.post("/api/v1/admin/businesses", headers=admin_headers, json={
        "name": "Gamma Coffee Roastery",
        "description": "Artisanal specialty coffee",
        "settings": {"flagshipAddr": "789 Gamma Rd, Hyderabad"},
        "brand_profile": {
            "name": "Gamma Coffee",
            "tagline": "Pure Organic Roast",
            "color_palette": ["#059669", "#78350f"]
        }
    })
    assert create_biz_res.status_code == 201
    gamma_id = create_biz_res.json()["id"]

    # 7. Test User Registration with Business Assignment (Assigned to Store Alpha)
    user_reg_res = client.post("/api/v1/auth/register", json={
        "email": "user_alpha@example.com",
        "password": "userPass123!",
        "full_name": "Alpha Creator",
        "business_id": biz1.id,
        "role": "creator"
    })
    assert user_reg_res.status_code == 201
    alpha_user_id = user_reg_res.json()["id"]

    # 8. Test User Login and Dashboard Data Loading
    user_login_res = client.post("/api/v1/auth/login", json={
        "email": "user_alpha@example.com",
        "password": "userPass123!"
    })
    assert user_login_res.status_code == 200
    user_token = user_login_res.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # Fetch User Profile & Assigned Businesses
    me_res = client.get("/api/v1/auth/me", headers=user_headers)
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["organization_id"] == biz1.id

    my_biz_res = client.get("/api/v1/auth/my-businesses", headers=user_headers)
    assert my_biz_res.status_code == 200
    my_biz = my_biz_res.json()
    # User should only see their assigned business (Store Alpha)
    assert len(my_biz) == 1
    assert my_biz[0]["name"] == "Predefined Store Alpha"

    # Fetch Current Organization details
    curr_org_res = client.get("/api/v1/organizations/current", headers=user_headers)
    assert curr_org_res.status_code == 200
    curr_org = curr_org_res.json()
    assert curr_org["name"] == "Predefined Store Alpha"
    assert curr_org["brand_profile"]["name"] == "Alpha Brand"

    # 9. Test Data Isolation: User attempts unauthorized switch to Store Beta
    unauth_switch = client.post(f"/api/v1/auth/switch-business/{biz2.id}", headers=user_headers)
    assert unauth_switch.status_code == 403
    assert "Access denied" in unauth_switch.json()["detail"]

    # 10. Test Admin Assigning User to Store Beta
    assign_res = client.post(f"/api/v1/admin/users/{alpha_user_id}/assign-business", headers=admin_headers, json={
        "business_id": biz2.id,
        "role": "creator",
        "is_default": False
    })
    assert assign_res.status_code == 200

    # 11. User now has multiple businesses: Can switch to Store Beta
    user_biz_now = client.get("/api/v1/auth/my-businesses", headers=user_headers).json()
    assert len(user_biz_now) == 2

    switch_res = client.post(f"/api/v1/auth/switch-business/{biz2.id}", headers=user_headers)
    assert switch_res.status_code == 200
    new_user_token = switch_res.json()["access_token"]
    new_user_headers = {"Authorization": f"Bearer {new_user_token}"}

    # Now verify dashboard data reflects Store Beta
    curr_org_after = client.get("/api/v1/organizations/current", headers=new_user_headers).json()
    assert curr_org_after["name"] == "Predefined Store Beta"
    assert curr_org_after["brand_profile"]["name"] == "Beta Brand"
