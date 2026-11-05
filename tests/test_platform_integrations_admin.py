import os
import sys
import pytest
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.platform_integration import PlatformIntegration
from app.models.user import User
from app.utils.enums import UserRole, PlatformType
from app.services.platform_integration_service import PlatformIntegrationService
from app.schemas.platform_integration import PlatformIntegrationUpdate, TestIntegrationRequest
from app.core.security import encrypt_token, decrypt_token, mask_secret


def test_platform_integrations_initialization_and_encryption():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # 1. Initialize default 8 platforms
        PlatformIntegrationService.initialize_default_platforms(db)
        
        platforms = PlatformIntegrationService.get_all_platforms(db)
        assert len(platforms) >= 8, f"Expected at least 8 platforms, got {len(platforms)}"

        expected_platforms = ["youtube", "instagram", "facebook", "tiktok", "linkedin", "twitter", "pinterest", "snapchat"]
        found_keys = [p.platform for p in platforms]
        for ep in expected_platforms:
            assert ep in found_keys, f"Missing platform {ep}"

        # 2. Test encryption and masking
        test_secret = "super_secret_meta_app_key_12345"
        encrypted = encrypt_token(test_secret)
        assert encrypted != test_secret
        decrypted = decrypt_token(encrypted)
        assert decrypted == test_secret

        masked = mask_secret(test_secret)
        assert masked.endswith("2345")
        assert test_secret not in masked[:-4]

        # 3. Test updating YouTube integration config
        update_payload = PlatformIntegrationUpdate(
            display_name="YouTube Studio & Shorts",
            client_id="123456789.apps.googleusercontent.com",
            client_secret="ya29.google_secret_token",
            is_enabled=True,
            environment="production"
        )
        updated = PlatformIntegrationService.update_platform(
            db=db,
            platform_name="youtube",
            payload=update_payload,
            admin_user_id=1
        )
        assert updated.is_enabled is True
        assert updated.client_id == "123456789.apps.googleusercontent.com"
        assert updated.client_secret_configured is True
        assert "ya29.google_secret_token" not in updated.client_secret_masked

        print("SUCCESS: Platform integrations verification passed successfully!")
    finally:
        db.close()


if __name__ == "__main__":
    test_platform_integrations_initialization_and_encryption()
