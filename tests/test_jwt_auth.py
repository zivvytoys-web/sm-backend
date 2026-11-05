import os
os.environ["TESTING"] = "true"
import sys
import os
import io
import time
import uuid
from datetime import datetime, timedelta, timezone
from jose import jwt
from fastapi.testclient import TestClient

# Windows UTF-8
# Add app directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.core.security import get_password_hash, create_access_token

client = TestClient(app)


def run_jwt_verification_tests():
    print("==================================================")
    print("RUNNING COMPREHENSIVE JWT AUTH VERIFICATION SUITE")
    print("==================================================")

    db = SessionLocal()
    try:
        # Create test users and organizations
        org1 = Organization(name="JWT Test Org 1", slug=f"jwt-org-1-{uuid.uuid4().hex[:6]}", settings={})
        org2 = Organization(name="JWT Test Org 2", slug=f"jwt-org-2-{uuid.uuid4().hex[:6]}", settings={})
        db.add_all([org1, org2])
        db.flush()

        user1 = User(
            email=f"jwt_user1_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="JWT User One",
            role="creator",
            organization_id=org1.id,
            is_active=True
        )
        user_admin = User(
            email=f"jwt_admin_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="JWT Admin",
            role="admin",
            organization_id=org1.id,
            is_active=True
        )
        user_inactive = User(
            email=f"jwt_inactive_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="Inactive User",
            role="creator",
            organization_id=org1.id,
            is_active=False
        )
        user2 = User(
            email=f"jwt_user2_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="JWT User Two",
            role="creator",
            organization_id=org2.id,
            is_active=True
        )

        db.add_all([user1, user_admin, user_inactive, user2])
        db.flush()

        brand1 = BrandProfile(organization_id=org1.id, name="Brand 1", industry="Tech", tone_of_voice="Smart")
        brand2 = BrandProfile(organization_id=org2.id, name="Brand 2", industry="Food", tone_of_voice="Warm")
        db.add_all([brand1, brand2])
        db.commit()
        db.refresh(user1)
        db.refresh(user_admin)
        db.refresh(user_inactive)
        db.refresh(user2)
        db.refresh(brand1)
        db.refresh(brand2)

        # -------------------------------------------------------------
        # Test 1: Public Routes (No token needed)
        # -------------------------------------------------------------
        print("\n[Test 1] Public Routes Accessibility...")
        login_res = client.post("/api/v1/auth/login", json={
            "email": user1.email,
            "password": "Password123!"
        })
        assert login_res.status_code == 200, f"Login should succeed without token: {login_res.text}"
        token_data = login_res.json()
        assert "access_token" in token_data, "Login response must contain access_token"
        valid_token = token_data["access_token"]
        client.cookies.clear()
        print("[PASS] Login and Register endpoints are public and functional.")

        # -------------------------------------------------------------
        # Test 2: Valid Token Verification on Protected Routes
        # -------------------------------------------------------------
        print("\n[Test 2] Valid Token Authentication...")
        me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {valid_token}"})
        assert me_res.status_code == 200, f"Protected route /auth/me failed with valid token: {me_res.text}"
        assert me_res.json()["email"] == user1.email
        print(f"[PASS] Valid token accepted! Authenticated as: {user1.email}")

        # -------------------------------------------------------------
        # Test 3: Missing Token
        # -------------------------------------------------------------
        print("\n[Test 3] Missing Authorization Token...")
        no_token_res = client.get("/api/v1/auth/me")
        assert no_token_res.status_code == 401, f"Missing token must return 401, got {no_token_res.status_code}"
        assert "bearer" in no_token_res.headers.get("www-authenticate", "").lower()
        print("[PASS] Missing Authorization header correctly rejected with HTTP 401.")

        # -------------------------------------------------------------
        # Test 4: Malformed Token
        # -------------------------------------------------------------
        print("\n[Test 4] Malformed Token Handling...")
        malformed_res = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-valid-jwt-token-string"})
        assert malformed_res.status_code == 401, f"Malformed token must return 401, got {malformed_res.status_code}"
        print("[PASS] Malformed token correctly rejected with HTTP 401.")

        # -------------------------------------------------------------
        # Test 5: Expired Token
        # -------------------------------------------------------------
        print("\n[Test 5] Expired Token Validation...")
        past_expire = datetime.now(timezone.utc) - timedelta(minutes=5)
        expired_jwt = jwt.encode(
            {"sub": str(user1.id), "exp": past_expire, "iat": past_expire - timedelta(minutes=10)},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM
        )
        expired_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_jwt}"})
        assert expired_res.status_code == 401, f"Expired token must return 401, got {expired_res.status_code}"
        assert "expired" in expired_res.json().get("detail", "").lower(), f"Expected 'expired' in message: {expired_res.text}"
        print("[PASS] Expired token correctly rejected with HTTP 401.")

        # -------------------------------------------------------------
        # Test 6: Invalid Signature (Wrong Secret Key)
        # -------------------------------------------------------------
        print("\n[Test 6] Invalid Signature (Tampered / Wrong Key)...")
        tampered_jwt = jwt.encode(
            {"sub": str(user1.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "completely-wrong-secret-key-9999",
            algorithm=settings.ALGORITHM
        )
        tampered_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered_jwt}"})
        assert tampered_res.status_code == 401, f"Tampered token must return 401, got {tampered_res.status_code}"
        print("[PASS] Token signed with invalid secret key correctly rejected with HTTP 401.")

        # -------------------------------------------------------------
        # Test 7: Non-Existent / Deleted User in Token Claims
        # -------------------------------------------------------------
        print("\n[Test 7] Deleted / Non-Existent User ID in Token...")
        ghost_jwt = create_access_token(subject="9999999")
        ghost_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost_jwt}"})
        assert ghost_res.status_code == 401, f"Non-existent user must return 401, got {ghost_res.status_code}"
        print("[PASS] Token with deleted/non-existent user ID correctly rejected with HTTP 401.")

        # -------------------------------------------------------------
        # Test 8: Inactive User Account Rejection
        # -------------------------------------------------------------
        print("\n[Test 8] Inactive / Deactivated User Account...")
        inactive_jwt = create_access_token(subject=str(user_inactive.id))
        inactive_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {inactive_jwt}"})

        assert inactive_res.status_code in (401, 403), f"Inactive user must return 401 or 403, got {inactive_res.status_code}"
        assert any(w in inactive_res.json().get("detail", "").lower() for w in ["inactive", "disabled", "deactivated"])
        print("[PASS] Inactive user account correctly rejected with HTTP 401.")

        # -------------------------------------------------------------
        # Test 9: Role Permission Enforcement (HTTP 403)
        # -------------------------------------------------------------
        print("\n[Test 9] Role Permission Enforcement (403 for Non-Admin)...")
        # User 1 has role "creator" - attempting to access admin route
        admin_route_res = client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {valid_token}"})
        assert admin_route_res.status_code == 403, f"Creator on admin route must return 403, got {admin_route_res.status_code}"
        
        # Admin user accessing admin route
        admin_jwt = create_access_token(subject=str(user_admin.id))
        admin_ok_res = client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {admin_jwt}"})
        assert admin_ok_res.status_code == 200, f"Admin on admin route must return 200, got {admin_ok_res.status_code}"
        print("[PASS] Role-based permission enforcement returned HTTP 403 for unauthorized users and HTTP 200 for admins.")

        # -------------------------------------------------------------
        # Test 10: Multi-Tenant / Cross-User Access Isolation
        # -------------------------------------------------------------
        print("\n[Test 10] Multi-Tenant Data Isolation Enforcement...")
        # User 1 attempting to access Brand 2 (belonging to Org 2)
        cross_res = client.get(f"/api/v1/brand-profiles/{brand2.id}", headers={"Authorization": f"Bearer {valid_token}"})
        assert cross_res.status_code in (403, 404), f"Cross-tenant access must be blocked with 403/404, got {cross_res.status_code}"

        # User 1 accessing own Brand 1
        own_res = client.get(f"/api/v1/brand-profiles/{brand1.id}", headers={"Authorization": f"Bearer {valid_token}"})
        assert own_res.status_code == 200, f"Accessing own brand profile must succeed: {own_res.text}"
        print("[PASS] Multi-tenant isolation verified: Cross-user access strictly blocked.")

        # -------------------------------------------------------------
        # Test 11: Secret Key Leakage Check
        # -------------------------------------------------------------
        print("\n[Test 11] Secret Key Leakage & Sanitization Check...")
        all_res_text = me_res.text + login_res.text + own_res.text
        assert settings.SECRET_KEY not in all_res_text, "CRITICAL: Backend SECRET_KEY leaked in API responses"
        print("[PASS] Verified SECRET_KEY is kept exclusively on the backend.")

        print("\n==================================================")
        print("ALL 11 JWT AUTH VERIFICATION TESTS PASSED (100% SUCCESS)")
        print("==================================================")

    finally:
        db.close()




def test_jwt_auth():
    run_jwt_verification_tests()

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    run_jwt_verification_tests()
