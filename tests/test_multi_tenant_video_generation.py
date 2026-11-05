import os
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset
from app.core.security import get_password_hash, create_access_token
from app.services.video_generation_service import VideoGenerationService

client = TestClient(app)


@pytest.fixture
def test_setup():
    db = SessionLocal()
    try:
        # 1. Tenant A: Athletic Footwear
        org_a = Organization(name="Tenant Alpha Corp", slug=f"tenant-alpha-{uuid.uuid4().hex[:6]}", settings={})
        # 2. Tenant B: Aesthetic Medical Clinic
        org_b = Organization(name="Tenant Beta Corp", slug=f"tenant-beta-{uuid.uuid4().hex[:6]}", settings={})
        db.add_all([org_a, org_b])
        db.flush()

        user_a = User(
            email=f"usera_{uuid.uuid4().hex[:6]}@alpha.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="User Alpha",
            role="creator",
            organization_id=org_a.id,
            is_active=True
        )
        user_b = User(
            email=f"userb_{uuid.uuid4().hex[:6]}@beta.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="User Beta",
            role="creator",
            organization_id=org_b.id,
            is_active=True
        )
        db.add_all([user_a, user_b])
        db.flush()

        brand_a = BrandProfile(
            organization_id=org_a.id,
            user_id=user_a.id,
            name="Apex Footwear",
            industry="Athletic Footwear & Performance",
            tagline="Step Beyond Limits",
            tone_of_voice="Bold & High-Energy",
            target_audience="Runners and marathon athletes",
            cta="Shop the Aero Runner collection today",
            key_themes=["Speed", "Cushioning", "Marathon"],
            color_palette=["#ff4500", "#111827"],
            meta_info={
                "products": [
                    {"name": "Aero Runner X1", "price": 140, "description": "Carbon plate marathon shoe"},
                    {"name": "Cloud Sprint Foam", "price": 110, "description": "Daily responsive running shoe"}
                ]
            }
        )

        brand_b = BrandProfile(
            organization_id=org_b.id,
            user_id=user_b.id,
            name="Aura Aesthetic Clinic",
            industry="Dermatology & Medical Aesthetics",
            tagline="Revealing Your True Radiance",
            tone_of_voice="Sophisticated & Caring",
            target_audience="Individuals seeking premium skincare",
            cta="Book your personalized consultation",
            key_themes=["Skin Health", "Glow", "Rejuvenation"],
            color_palette=["#d4af37", "#fdfbf7"],
            meta_info={
                "services": [
                    {"name": "Hydra Glow Facial", "price": 180, "description": "Deep clinical hydration therapy"},
                    {"name": "Laser Skin Resurfacing", "price": 350, "description": "Non-invasive collagen booster"}
                ]
            }
        )
        db.add_all([brand_a, brand_b])
        db.commit()

        token_a = create_access_token(subject=user_a.id)
        token_b = create_access_token(subject=user_b.id)

        yield {
            "db": db,
            "org_a": org_a,
            "org_b": org_b,
            "user_a": user_a,
            "user_b": user_b,
            "brand_a": brand_a,
            "brand_b": brand_b,
            "token_a": token_a,
            "token_b": token_b
        }
    finally:
        db.close()


def test_tenant_video_generation_uses_correct_brand(test_setup):
    """Test 1: Every video generation request strictly retrieves the authenticated tenant's brand."""
    token_a = test_setup["token_a"]
    brand_a = test_setup["brand_a"]

    headers = {"Authorization": f"Bearer {token_a}"}
    payload = {
        "prompt": "Highlight our flagship marathon shoe",
        "brand_profile_id": brand_a.id,
        "aspect_ratio": "9:16",
        "duration_seconds": 30
    }
    res = client.post("/api/v1/generations/gemini/video", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["status"] in ["processing", "completed"]
    assert "Apex Footwear" in data["prompt"]
    assert data["parameters"]["brand_profile_id"] == brand_a.id
    assert data["result_data"]["brand_name"] == "Apex Footwear"
    assert data["result_data"]["brand_industry"] == "Athletic Footwear & Performance"


def test_two_tenants_receive_isolated_prompts_and_data(test_setup):
    """Test 2: Two tenants generating videos concurrently receive their own distinct brand context."""
    token_a = test_setup["token_a"]
    brand_a = test_setup["brand_a"]
    token_b = test_setup["token_b"]
    brand_b = test_setup["brand_b"]

    # Tenant A (Apex Footwear)
    res_a = client.post("/api/v1/generations/gemini/video", json={
        "prompt": "New arrival product showcase",
        "brand_profile_id": brand_a.id
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert res_a.status_code == 200
    data_a = res_a.json()

    # Tenant B (Aura Aesthetic Clinic)
    res_b = client.post("/api/v1/generations/gemini/video", json={
        "prompt": "Signature skin treatment experience",
        "brand_profile_id": brand_b.id
    }, headers={"Authorization": f"Bearer {token_b}"})
    assert res_b.status_code == 200
    data_b = res_b.json()

    # Verify complete isolation
    assert "Apex Footwear" in data_a["prompt"]
    assert "Aura Aesthetic Clinic" not in data_a["prompt"]
    assert data_a["result_data"]["brand_name"] == "Apex Footwear"

    assert "Aura Aesthetic Clinic" in data_b["prompt"]
    assert "Apex Footwear" not in data_b["prompt"]
    assert data_b["result_data"]["brand_name"] == "Aura Aesthetic Clinic"


def test_cross_tenant_access_is_forbidden(test_setup):
    """Test 3: A tenant cannot use or access another tenant's brand profile ID (HTTP 403 Forbidden)."""
    token_a = test_setup["token_a"]
    brand_b = test_setup["brand_b"]  # Belongs to Tenant B

    # Tenant A attempts to use Tenant B's brand profile
    res = client.post("/api/v1/generations/gemini/video", json={
        "prompt": "Commercial video",
        "brand_profile_id": brand_b.id  # Unauthorized cross-tenant ID
    }, headers={"Authorization": f"Bearer {token_a}"})

    assert res.status_code == 403
    assert "Cross-tenant access" in res.json()["detail"] or "forbidden" in res.json()["detail"].lower()


def test_brand_profile_update_immediately_reflects_in_video_gen(test_setup):
    """Test 4: Updating a brand profile immediately takes effect in the very next video generation request."""
    db = test_setup["db"]
    token_a = test_setup["token_a"]
    brand_a = test_setup["brand_a"]

    # Update brand profile in database
    brand_a.name = "Apex Carbon Lab"
    brand_a.tagline = "Carbon Plate Propulsion"
    brand_a.updated_at = datetime.now(timezone.utc)
    db.commit()

    res = client.post("/api/v1/generations/gemini/video", json={
        "prompt": "Propulsion technology reveal",
        "brand_profile_id": brand_a.id
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert res.status_code == 200
    data = res.json()

    assert "Apex Carbon Lab" in data["prompt"]
    assert data["result_data"]["brand_name"] == "Apex Carbon Lab"
    assert "Carbon Plate Propulsion" in data["prompt"] or "Apex Carbon Lab" in data["prompt"]


def test_missing_or_placeholder_brand_profile_fails(test_setup):
    """Test 5: Generation fails with a clear HTTP 400 error if brand profile is missing or placeholder."""
    db = test_setup["db"]

    # Create new tenant with no brand profile
    org_c = Organization(name="Empty Tenant", slug=f"empty-{uuid.uuid4().hex[:6]}", settings={})
    db.add(org_c)
    db.flush()
    user_c = User(
        email=f"empty_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=get_password_hash("Password123!"),
        full_name="User C",
        role="creator",
        organization_id=org_c.id,
        is_active=True
    )
    db.add(user_c)
    db.commit()

    token_c = create_access_token(subject=user_c.id)

    # No brand profile created yet
    res1 = client.post("/api/v1/generations/gemini/video", json={"prompt": "Video"}, headers={"Authorization": f"Bearer {token_c}"})
    assert res1.status_code == 400
    assert "Brand Profile is required" in res1.json()["detail"]

    # Create brand profile with generic placeholder name "Brand"
    bp_bad = BrandProfile(
        organization_id=org_c.id,
        user_id=user_c.id,
        name="Brand",
        industry="Retail"
    )
    db.add(bp_bad)
    db.commit()

    res2 = client.post("/api/v1/generations/gemini/video", json={"prompt": "Video", "brand_profile_id": bp_bad.id}, headers={"Authorization": f"Bearer {token_c}"})
    assert res2.status_code == 400
    assert "placeholder" in res2.json()["detail"].lower()


def test_bundle_video_generation_multi_tenant_isolation(test_setup):
    """Test 6: Full bundle generation enforces multi-tenant brand isolation."""
    token_a = test_setup["token_a"]
    brand_b = test_setup["brand_b"]  # Foreign brand

    # Calling full bundle with foreign brand_profile_id fails with 403
    res_forbidden = client.post("/api/v1/generations/gemini/full-bundle", json={
        "brand_profile_id": brand_b.id,
        "campaign_goal": "product_launch",
        "aspect_ratio": "16:9"
    }, headers={"Authorization": f"Bearer {token_a}"})
    assert res_forbidden.status_code == 403
