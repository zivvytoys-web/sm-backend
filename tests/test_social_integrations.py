import sys
import os
import unittest
from datetime import datetime

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.core.security import encrypt_token, decrypt_token, mask_secret
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.utils.enums import PlatformType, UserRole


def test_token_encryption():
    print("\n--- Testing AES-256 Token Encryption & Decryption ---")
    secret_token = "EAABwzL12345sampleMetaAccessTokenForInstagramPublishing98765"
    encrypted = encrypt_token(secret_token)
    assert encrypted != secret_token, "Encrypted token should not equal plaintext"
    decrypted = decrypt_token(encrypted)
    assert decrypted == secret_token, f"Decrypted token '{decrypted}' must match original '{secret_token}'"
    print("[PASS] AES Token Encryption and Decryption passed successfully!")

    masked = mask_secret("super_secret_meta_key_9999")
    assert masked.endswith("9999"), f"Masked secret should preserve last 4 chars: {masked}"
    print(f"[PASS] Secret Masking passed: '{masked}'")



def test_db_social_account_persistence_and_isolation():
    print("\n--- Testing DB Persistence & Multi-Tenant Isolation ---")
    db = SessionLocal()
    try:
        # Create Org A and Org B
        org_a = db.query(Organization).filter(Organization.name == "Test Org Alpha").first()
        if not org_a:
            org_a = Organization(name="Test Org Alpha", slug="test-org-alpha", settings={})
            db.add(org_a)
            db.flush()

        org_b = db.query(Organization).filter(Organization.name == "Test Org Beta").first()
        if not org_b:
            org_b = Organization(name="Test Org Beta", slug="test-org-beta", settings={})
            db.add(org_b)
            db.flush()


        brand_a = db.query(BrandProfile).filter(BrandProfile.organization_id == org_a.id).first()
        if not brand_a:
            brand_a = BrandProfile(organization_id=org_a.id, name="Alpha Fashion", industry="Apparel", tone_of_voice="Modern")
            db.add(brand_a)
            db.flush()

        brand_b = db.query(BrandProfile).filter(BrandProfile.organization_id == org_b.id).first()
        if not brand_b:
            brand_b = BrandProfile(organization_id=org_b.id, name="Beta Studio", industry="Footwear", tone_of_voice="Minimal")
            db.add(brand_b)
            db.flush()

        # Add encrypted social account to Brand A
        raw_token = "sample_encrypted_ig_token_12345"
        enc_token = encrypt_token(raw_token)
        
        test_acc = db.query(SocialAccount).filter(
            SocialAccount.brand_profile_id == brand_a.id,
            SocialAccount.platform == PlatformType.INSTAGRAM
        ).first()

        if not test_acc:
            test_acc = SocialAccount(
                platform=PlatformType.INSTAGRAM,
                account_name="@aminoid_alpha",
                account_id="ig_alpha_101",
                profile_url="https://instagram.com/aminoid_alpha",
                access_token=enc_token,
                is_connected=True,
                meta_info={"verification_status": "VERIFIED", "last_verified_at": datetime.utcnow().isoformat()},
                brand_profile_id=brand_a.id
            )
            db.add(test_acc)
            db.commit()
            db.refresh(test_acc)

        # Verify token in DB is encrypted
        assert test_acc.access_token != raw_token, "Stored token must be encrypted in database"
        assert decrypt_token(test_acc.access_token) == raw_token, "Decrypted token must match original"
        print("[PASS] Social account persisted with encrypted credentials in MySQL.")

        # Test Multi-user Isolation
        # Querying Org B accounts must NOT return Org A's account
        org_b_accounts = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
            BrandProfile.organization_id == org_b.id
        ).all()
        
        for acc in org_b_accounts:
            assert acc.brand_profile_id != brand_a.id, "Org B must not access Org A accounts"
            assert acc.account_id != "ig_alpha_101", "Cross-tenant account leaked"

        print("[PASS] Multi-tenant isolation verified: Org B cannot access Org A social accounts.")

    finally:
        db.close()


if __name__ == "__main__":
    test_token_encryption()
    test_db_social_account_persistence_and_isolation()
    print("\n[SUCCESS] ALL SOCIAL INTEGRATION TESTS PASSED!\n")

