import os
import sys
from fastapi.testclient import TestClient

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.user import User
from app.utils.enums import UserRole

client = TestClient(app)

def test_admin_platform_integrations_api():
    db = SessionLocal()
    try:
        # Create or fetch an admin user for testing
        admin_user = db.query(User).filter(User.role == UserRole.ADMIN).first()
        if not admin_user:
            admin_user = db.query(User).first()
            if admin_user:
                admin_user.role = UserRole.ADMIN
                db.commit()
                db.refresh(admin_user)

        assert admin_user is not None, "Need at least one user in database for testing."
        
        token = create_access_token(subject=admin_user.id)
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Test listing platforms
        res = client.get("/api/v1/admin/integrations/platforms", headers=headers)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert len(data) >= 8, f"Expected 8 platforms, got {len(data)}"

        # 2. Test getting specific platform (TikTok)
        res_tt = client.get("/api/v1/admin/integrations/platforms/tiktok", headers=headers)
        assert res_tt.status_code == 200
        tt_data = res_tt.json()
        assert tt_data["platform"] == "tiktok"
        assert "client_secret" not in tt_data  # Ensure unmasked secret is NEVER returned!

        # 3. Test updating Pinterest configuration
        update_body = {
            "client_id": "pin_app_123456",
            "client_secret": "pin_secret_987654",
            "is_enabled": True,
            "environment": "production"
        }
        res_update = client.put("/api/v1/admin/integrations/platforms/pinterest", json=update_body, headers=headers)
        assert res_update.status_code == 200
        pin_data = res_update.json()
        assert pin_data["client_id"] == "pin_app_123456"
        assert pin_data["client_secret_configured"] is True
        assert pin_data["client_secret_masked"].endswith("7654")
        assert "pin_secret_987654" not in str(pin_data)

        # 4. Test toggle platform endpoint
        res_toggle = client.post("/api/v1/admin/integrations/platforms/pinterest/toggle", json={"is_enabled": False}, headers=headers)
        assert res_toggle.status_code == 200
        assert res_toggle.json()["is_enabled"] is False

        # 5. Test testing endpoint
        res_test = client.post("/api/v1/admin/integrations/platforms/youtube/test", json={}, headers=headers)
        assert res_test.status_code == 200
        assert "test_status" in res_test.json()

        print("SUCCESS: Admin platform integrations API endpoints verified successfully!")
    finally:
        db.close()


if __name__ == "__main__":
    test_admin_platform_integrations_api()
