import pytest
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.autonomous_pipeline import (
    BusinessSchedule,
    PipelineRun,
    ContentBrief,
    AutonomousPost,
    PostVariant
)
from app.services.pipeline_gemini_service import CreativeBriefSchema, PlatformCaptionsSchema
from app.services.quality_service import QualityService


@pytest.fixture
def test_setup():
    db = SessionLocal()
    # Create test user and brand profile
    org = db.query(Organization).filter(Organization.name == "AutoTest Org").first()
    if not org:
        org = Organization(name="AutoTest Org", slug="autotest-org")
        db.add(org)
        db.commit()
        db.refresh(org)

    user = db.query(User).filter(User.email == "autotester@example.com").first()
    if not user:
        user = User(
            email="autotester@example.com",
            full_name="Auto Tester",
            hashed_password="hashed_pw_test",
            role="admin",
            organization_id=org.id
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    brand = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
    if not brand:
        brand = BrandProfile(
            organization_id=org.id,
            name="Apex Luxury Fashion",
            industry="Fashion & Apparel",
            tagline="Sustainable Elegance",
            tone_of_voice="Sophisticated & Modern",
            target_audience="Conscious luxury shoppers",
            key_themes=["Sustainability", "Italian Craftsmanship", "Autumn Collection"],
            cta="Shop the Autumn Drop"
        )
        db.add(brand)
        db.commit()
        db.refresh(brand)

    token = create_access_token(subject=user.id)
    yield {"db": db, "user": user, "brand": brand, "token": token}
    db.close()


def test_schedule_get_and_update(test_setup):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {test_setup['token']}"}

    # 1. Get Schedule (creates default if none)
    res = client.get("/api/v1/pipeline/schedule", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["brand_profile_id"] == test_setup["brand"].id
    assert data["timezone"] == "Asia/Kolkata"

    # 2. Update Schedule
    update_payload = {
        "generation_time": "08:30",
        "posting_time": "19:45",
        "timezone": "Asia/Kolkata",
        "auto_approve": True,
        "is_active": True,
        "target_platforms": ["instagram", "linkedin", "youtube"]
    }
    res_update = client.put("/api/v1/pipeline/schedule", json=update_payload, headers=headers)
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["generation_time"] == "08:30"
    assert updated["posting_time"] == "19:45"
    assert updated["auto_approve"] is True


def test_quality_audit_engine(test_setup):
    brand = test_setup["brand"]
    brief_data = {
        "topic": "Sustainable Autumn Elegance",
        "angle": "Behind the seams of recycled cashmere",
        "caption_brief": "Showcase craftsmanship and sustainable sourcing",
        "hashtags": ["#SustainableFashion", "#LuxuryStyle", "#ApexAutumn"],
        "cta": "Shop the collection"
    }
    platform_captions = {
        "instagram": "🍂 Elegance redefined with zero compromise on sustainability.\n\nDiscover the Italian cashmere drop.\n\n👉 Shop the Autumn Drop\n\n#SustainableFashion #ApexAutumn",
        "facebook": "Discover how luxury meets sustainability this season. Shop the collection today.",
        "linkedin": "Craftsmanship at scale: Why sustainability is the future of luxury apparel.\n\n#SustainableFashion #Luxury",
        "youtube_title": "Apex Fashion - Sustainable Autumn Collection 2026",
        "youtube_description": "Behind the scenes of our newest eco-luxury line.\n\nShop the collection now."
    }
    media_assets = [{"status": "done", "file_path": "/uploads/media/sample.png"}]
    past_posts = []

    audit = QualityService.audit_post(
        brand_profile=brand,
        brief_data=brief_data,
        platform_captions=platform_captions,
        media_assets=media_assets,
        past_posts=past_posts
    )

    assert audit.passed is True
    assert audit.score >= 80.0
    assert audit.badges["safety_passed"] is True
    assert audit.badges["platform_compliant"] is True


def test_pipeline_run_now_endpoint(test_setup):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {test_setup['token']}"}

    # Mock external calls to execute lightning-fast and reliably in pytest
    mock_brief = CreativeBriefSchema(
        topic="Sustainable Cashmere Revolution",
        angle="Ethical luxury crafted in Italy",
        reason="Autumn seasonal relevance",
        caption_brief="Highlight 100% recycled Italian cashmere",
        image_prompt="High fashion studio editorial, model in beige cashmere coat, warm cinematic lighting",
        video_prompt="Dynamic 6-second vertical reel of cashmere weaving process",
        hashtags=["#SustainableLuxury", "#ApexFashion", "#Cashmere"],
        cta="Discover the collection",
        tone="Sophisticated",
        target_platforms=["instagram", "facebook", "linkedin", "youtube"]
    )

    mock_captions = PlatformCaptionsSchema(
        instagram="✨ Sustainable Cashmere Revolution\n\nEthical luxury crafted in Italy.\n\n👉 Discover the collection\n\n#SustainableLuxury #ApexFashion",
        facebook="Sustainable Cashmere Revolution. Ethical luxury crafted in Italy. Learn more today.",
        linkedin="📌 Sustainable Cashmere Revolution: Leading the next era of ethical luxury.\n\n#SustainableLuxury",
        youtube_title="Apex Luxury - Sustainable Cashmere Revolution",
        youtube_description="Watch how our sustainable cashmere collection is crafted.\n\nDiscover the collection."
    )

    mock_img = {"file_path": "https://res.cloudinary.com/demo/image/upload/sample.jpg", "media_type": "image"}
    mock_vid = {"file_path": "https://res.cloudinary.com/demo/video/upload/sample.mp4", "status": "done", "duration_seconds": 6}

    with patch("app.services.pipeline_gemini_service.PipelineGeminiService.generate_creative_brief", return_value=mock_brief), \
         patch("app.services.pipeline_gemini_service.PipelineGeminiService.generate_platform_captions", return_value=mock_captions), \
         patch("app.services.pipeline_gemini_service.PipelineGeminiService.generate_branded_image", return_value=mock_img), \
         patch("app.services.pipeline_gemini_service.PipelineGeminiService.start_async_video_generation", return_value=mock_vid), \
         patch("app.services.magic_hour_service.MagicHourVideoService.generate_and_persist_video", return_value=mock_vid):

        res = client.post("/api/v1/pipeline/run-now", json={"force": True}, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["post_id"] is not None

        post_id = data["post_id"]

        # Verify post listing
        list_res = client.get("/api/v1/pipeline/posts", headers=headers)
        assert list_res.status_code == 200
        posts = list_res.json()
        assert any(p["id"] == post_id for p in posts)

        # Verify post approval
        appr_res = client.post(f"/api/v1/pipeline/posts/{post_id}/approve", json={"notes": "Approved for publish"}, headers=headers)
        assert appr_res.status_code == 200
        assert appr_res.json()["status"] == "scheduled"

        # Verify post draft save
        draft_res = client.post(f"/api/v1/pipeline/posts/{post_id}/save-draft", headers=headers)
        assert draft_res.status_code == 200
        assert draft_res.json()["status"] == "draft"

        # Verify post rejection
        rej_res = client.post(f"/api/v1/pipeline/posts/{post_id}/reject", json={"reason": "Test rejection"}, headers=headers)
        assert rej_res.status_code == 200
        assert rej_res.json()["status"] == "rejected"
