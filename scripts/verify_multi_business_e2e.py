import sys
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from app.main import app
from app.db.session import SessionLocal
from app.models.organization import Organization
from app.models.user import User

client = TestClient(app)

def run_e2e_verification():
    print("=" * 60)
    print("RUNNING END-TO-END MULTI-BUSINESS VERIFICATION")
    print("=" * 60)

    # 1. Admin Login
    print("\n[TEST 1] Platform Super Admin Login...")
    admin_login = client.post("/api/v1/auth/login", json={
        "email": "admin@socialai.com",
        "password": "password123"
    })
    assert admin_login.status_code == 200, f"Admin login failed: {admin_login.text}"
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    print("  [OK] Admin logged in successfully!")

    # 2. Admin retrieves all predefined businesses from MySQL
    print("\n[TEST 2] Retrieve all predefined businesses from MySQL...")
    biz_res = client.get("/api/v1/admin/businesses", headers=admin_headers)
    assert biz_res.status_code == 200, f"Get businesses failed: {biz_res.text}"
    businesses = biz_res.json()
    print(f"  âœ“ Found {len(businesses)} businesses in database:")
    for b in businesses[:5]:
        bp = b.get("brand_profile") or {}
        print(f"    - ID #{b['id']}: {b['name']} ({b['settings'].get('category') or bp.get('industry')}) | Brand: {bp.get('name')}")
    assert len(businesses) >= 4, "Expected at least 4 predefined businesses!"

    # Find Apex Luxury Watches and Zenith Cafe IDs
    apex_biz = next((b for b in businesses if "Apex" in b["name"] or b["slug"] == "apex-luxury-watches"), businesses[1])
    zenith_biz = next((b for b in businesses if "Zenith" in b["name"] or b["slug"] == "zenith-organic-cafe"), businesses[2])
    print(f"  Target Biz A (Apex): ID #{apex_biz['id']}")
    print(f"  Target Biz B (Zenith): ID #{zenith_biz['id']}")

    # 3. Public Available Businesses API
    print("\n[TEST 3] Public Available Businesses API (/auth/available-businesses)...")
    avail_res = client.get("/api/v1/auth/available-businesses")
    assert avail_res.status_code == 200
    avail_list = avail_res.json()
    assert len(avail_list) >= 4
    print(f"  âœ“ Retrieved {len(avail_list)} available businesses for registration dropdown.")

    # 4. User Registration with Business Assignment
    test_user_email = "test_creator_apex@example.com"
    # Clean up if exists
    db = SessionLocal()
    existing = db.query(User).filter(User.email == test_user_email).first()
    if existing:
        db.delete(existing)
        db.commit()
    db.close()

    print(f"\n[TEST 4] Registering new user assigned to '{apex_biz['name']}' (ID #{apex_biz['id']})...")
    reg_res = client.post("/api/v1/auth/register", json={
        "email": test_user_email,
        "password": "Password123!",
        "full_name": "Marcus Horology",
        "business_id": apex_biz["id"],
        "role": "creator"
    })
    assert reg_res.status_code == 201, f"Registration failed: {reg_res.text}"
    user_data = reg_res.json()
    user_id = user_data["id"]
    print(f"  âœ“ User registered: ID #{user_id}, Assigned Org #{user_data['organization_id']}")
    assert user_data["organization_id"] == apex_biz["id"]

    # 5. User Login & Personal Dashboard Data
    print("\n[TEST 5] Logging in as Marcus Horology & verifying personal dashboard data...")
    user_login = client.post("/api/v1/auth/login", json={
        "email": test_user_email,
        "password": "Password123!"
    })
    assert user_login.status_code == 200
    user_token = user_login.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # Verify /auth/my-businesses returns ONLY assigned business
    my_biz = client.get("/api/v1/auth/my-businesses", headers=user_headers).json()
    print(f"  âœ“ User sees {len(my_biz)} assigned business(es): {[b['name'] for b in my_biz]}")
    assert len(my_biz) == 1
    assert my_biz[0]["id"] == apex_biz["id"]

    # Verify /organizations/current returns Apex Luxury Watches details
    curr_org = client.get("/api/v1/organizations/current", headers=user_headers).json()
    print(f"  âœ“ Current Business loaded: '{curr_org['name']}'")
    assert curr_org["id"] == apex_biz["id"]
    assert "Apex" in curr_org["name"]
    print(f"    - Flagship: {curr_org['settings'].get('flagshipAddr')}")
    print(f"    - Tagline: {curr_org['brand_profile'].get('tagline')}")

    # 6. Security & Data Isolation Check
    print(f"\n[TEST 6] Data Isolation: User attempts unauthorized switch to Zenith Cafe (#{zenith_biz['id']})...")
    unauth_res = client.post(f"/api/v1/auth/switch-business/{zenith_biz['id']}", headers=user_headers)
    print(f"  âœ“ Server responded with status code {unauth_res.status_code}: {unauth_res.json()['detail']}")
    assert unauth_res.status_code == 403, f"Expected 403 Forbidden but got {unauth_res.status_code}"

    # 7. Admin assigns User to Zenith Cafe
    print(f"\n[TEST 7] Admin assigns User #{user_id} to '{zenith_biz['name']}' (#{zenith_biz['id']})...")
    assign_res = client.post(f"/api/v1/admin/users/{user_id}/assign-business", headers=admin_headers, json={
        "business_id": zenith_biz["id"],
        "role": "creator",
        "is_default": False
    })
    assert assign_res.status_code == 200
    print("  âœ“ User successfully assigned by Admin!")

    # 8. User switches business
    print(f"\n[TEST 8] User now switches active business to '{zenith_biz['name']}'...")
    switch_res = client.post(f"/api/v1/auth/switch-business/{zenith_biz['id']}", headers=user_headers)
    assert switch_res.status_code == 200
    new_token = switch_res.json()["access_token"]
    new_headers = {"Authorization": f"Bearer {new_token}"}
    print("  âœ“ Business switch successful! New token generated.")

    # 9. Verify User's dashboard now loads Zenith Cafe details
    print("\n[TEST 9] Verifying user dashboard data after switch...")
    switched_org = client.get("/api/v1/organizations/current", headers=new_headers).json()
    print(f"  âœ“ Active Business is now: '{switched_org['name']}' (ID #{switched_org['id']})")
    assert switched_org["id"] == zenith_biz["id"]
    assert "Zenith" in switched_org["name"]
    print(f"    - Flagship: {switched_org['settings'].get('flagshipAddr')}")
    print(f"    - Tagline: {switched_org['brand_profile'].get('tagline')}")

    # 10. Admin Business Creation, Update, and Deletion
    print("\n[TEST 10] Admin creates, updates, and deletes a custom business...")
    create_test_biz = client.post("/api/v1/admin/businesses", headers=admin_headers, json={
        "name": "E2E Temporary Bistro",
        "description": "Temporary test dining establishment",
        "settings": {"flagshipAddr": "99 Temporary St, Mumbai", "category": "Food & Beverage"},
        "brand_profile": {
            "name": "E2E Bistro",
            "tagline": "Taste of Quality",
            "color_palette": ["#f59e0b", "#10b981"]
        }
    })
    assert create_test_biz.status_code == 201
    temp_biz_id = create_test_biz.json()["id"]
    print(f"  âœ“ Created temporary business ID #{temp_biz_id}")

    # Update
    update_test_biz = client.put(f"/api/v1/admin/businesses/{temp_biz_id}", headers=admin_headers, json={
        "name": "E2E Updated Bistro",
        "settings": {"timings": "09:00 AM â€“ 11:00 PM"}
    })
    assert update_test_biz.status_code == 200
    assert update_test_biz.json()["name"] == "E2E Updated Bistro"
    print(f"  âœ“ Updated temporary business details")

    # Delete
    del_res = client.delete(f"/api/v1/admin/businesses/{temp_biz_id}", headers=admin_headers)
    assert del_res.status_code == 200
    print(f"  âœ“ Deleted temporary business ID #{temp_biz_id}")

    print("\n" + "=" * 60)
    print("ALL 10 END-TO-END TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 60)

if __name__ == "__main__":
    run_e2e_verification()
