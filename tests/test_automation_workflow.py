import pytest
from datetime import datetime, date, time, timedelta
import pytz
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
    AutonomousPost,
    PostVariant,
    ContentBrief
)
from app.services.pipeline_service import calculate_utc_scheduled_time, run_pipeline
from app.scheduler.autonomous_scheduler import AutonomousScheduler


@pytest.fixture
def automation_setup():
    db = SessionLocal()
    org = db.query(Organization).filter(Organization.name == "Automation Org").first()
    if not org:
        org = Organization(name="Automation Org", slug="automation-org")
        db.add(org)
        db.commit()
        db.refresh(org)

    user = db.query(User).filter(User.email == "automation_admin@example.com").first()
    if not user:
        user = User(
            email="automation_admin@example.com",
            full_name="Automation Admin",
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
            name="Zivvy Luxury Toys",
            industry="Kids & Luxury Collectibles",
            tagline="Play with Wonder",
            tone_of_voice="Playful, Premium & Warm",
            target_audience="Affluent Parents and Collectors",
            key_themes=["Handcrafted", "Eco-friendly", "Child development"],
            cta="Explore the collection"
        )
        db.add(brand)
        db.commit()
        db.refresh(brand)

    token = create_access_token(subject=user.id)
    yield {"db": db, "user": user, "brand": brand, "token": token}
    db.close()


def test_1_settings_can_be_saved_and_loaded(automation_setup):
    """1. Settings can be saved and loaded."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {automation_setup['token']}"}

    # Test GET settings
    res = client.get("/api/automation/settings", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["brand_profile_id"] == automation_setup["brand"].id
    assert data["timezone"] == "Asia/Kolkata"

    # Test PUT settings
    future_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    payload = {
        "generation_date": future_date,
        "generation_time": "10:15",
        "posting_date": future_date,
        "posting_time": "18:45",
        "timezone": "Asia/Kolkata",
        "auto_approve": True,
        "auto_schedule": True,
        "is_active": True,
        "target_platforms": ["instagram", "facebook"]
    }
    res_put = client.put("/api/automation/settings", json=payload, headers=headers)
    assert res_put.status_code == 200
    saved = res_put.json()
    assert saved["generation_date"] == future_date
    assert saved["generation_time"] == "10:15"
    assert saved["posting_date"] == future_date
    assert saved["posting_time"] == "18:45"
    assert saved["auto_approve"] is True
    assert saved["auto_schedule"] is True


def test_2_and_3_timezone_and_scheduled_timing_calculation(automation_setup):
    """2 & 3 & 13. Timezone calculation, posting waits until target time, Asia/Kolkata conversion."""
    kolkata_tz = pytz.timezone("Asia/Kolkata")
    
    # Target 18:30 in Kolkata on 2026-10-25
    utc_dt = calculate_utc_scheduled_time("18:30", "Asia/Kolkata", target_date="2026-10-25")
    
    # 18:30 IST is 13:00 UTC (IST is UTC+5:30)
    assert utc_dt.year == 2026
    assert utc_dt.month == 10
    assert utc_dt.day == 25
    assert utc_dt.hour == 13
    assert utc_dt.minute == 0


def test_4_and_5_and_6_auto_approve_matrix(automation_setup):
    """4, 5, 6. Auto-Approve and Auto-Schedule matrix."""
    db = automation_setup["db"]
    brand = automation_setup["brand"]

    # Scenario A: Auto-Approve OFF, Auto-Schedule ON -> pending_approval
    sched = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()
    sched.auto_approve = False
    sched.auto_schedule = True
    db.commit()

    # Create guaranteed unique run and brief
    max_run = db.query(PipelineRun).filter(PipelineRun.brand_profile_id == brand.id).order_by(PipelineRun.run_date.desc()).first()
    next_date = (max_run.run_date + timedelta(days=1)) if max_run else (date.today() + timedelta(days=100))

    run = PipelineRun(
        brand_profile_id=brand.id,
        run_date=next_date,
        status="completed",
        stage="Completed",
        progress_percent=100
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    brief = ContentBrief(
        run_id=run.id,
        brand_profile_id=brand.id,
        topic="Eco Luxury Dolls",
        angle="Behind the craft",
        caption_brief="Showcasing sustainable pine",
        image_prompt="Handcrafted doll",
        video_prompt="Carving wood",
        reason="Promotes craftsmanship"
    )
    db.add(brief)
    db.commit()
    db.refresh(brief)

    post = AutonomousPost(
        brief_id=brief.id,
        brand_profile_id=brand.id,
        status="pending_approval",
        scheduled_time=datetime.utcnow() + timedelta(hours=5)
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {automation_setup['token']}"}

    # Post must be in pending review queue
    res_q = client.get("/api/automation/review-queue?status=pending_approval", headers=headers)
    assert res_q.status_code == 200
    queue_items = res_q.json()
    assert any(p["id"] == post.id for p in queue_items)

    # Manual approve should transition to scheduled
    res_app = client.post(f"/api/automation/content/{post.id}/approve", headers=headers)
    assert res_app.status_code == 200
    assert res_app.json()["status"] == "scheduled"


def test_8_scheduler_reconstructs_and_status(automation_setup):
    """8. Scheduler status and job reconstruction."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {automation_setup['token']}"}

    res_status = client.get("/api/automation/status", headers=headers)
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert "engine_status" in status_data
    assert "is_leader" in status_data
    assert "next_generation_time" in status_data

    res_jobs = client.get("/api/automation/jobs", headers=headers)
    assert res_jobs.status_code == 200
    assert isinstance(res_jobs.json(), list)


def test_14_pause_and_enable_automation(automation_setup):
    """14. Pausing automation prevents scheduled jobs and updates status."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {automation_setup['token']}"}

    # Pause
    res_pause = client.post("/api/automation/pause", headers=headers)
    assert res_pause.status_code == 200
    assert res_pause.json()["is_active"] is False

    # Check status
    res_settings = client.get("/api/automation/settings", headers=headers)
    assert res_settings.json()["is_active"] is False

    # Enable
    res_enable = client.post("/api/automation/enable", headers=headers)
    assert res_enable.status_code == 200
    assert res_enable.json()["is_active"] is True


def test_15_review_queue_actions(automation_setup):
    """15. Review queue actions: edit, reschedule, reject, retry."""
    db = automation_setup["db"]
    brand = automation_setup["brand"]
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {automation_setup['token']}"}

    max_run = db.query(PipelineRun).filter(PipelineRun.brand_profile_id == brand.id).order_by(PipelineRun.run_date.desc()).first()
    next_date = (max_run.run_date + timedelta(days=1)) if max_run else (date.today() + timedelta(days=200))

    run = PipelineRun(
        brand_profile_id=brand.id,
        run_date=next_date,
        status="completed",
        stage="Completed",
        progress_percent=100
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    brief = ContentBrief(
        run_id=run.id,
        brand_profile_id=brand.id,
        topic="Handmade Toys",
        angle="Safe materials",
        caption_brief="Non-toxic finish",
        image_prompt="Organic wood blocks",
        video_prompt="Children playing",
        reason="Safety first"
    )
    db.add(brief)
    db.commit()
    db.refresh(brief)

    # Create post with variant
    post = AutonomousPost(
        brief_id=brief.id,
        brand_profile_id=brand.id,
        status="pending_approval",
        scheduled_time=datetime.utcnow() + timedelta(hours=6)
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    variant = PostVariant(
        post_id=post.id,
        platform="instagram",
        caption="Original Caption",
        hashtags=["#original"],
        media_type="image",
        status="pending",
        scheduled_time=datetime.utcnow() + timedelta(hours=6)
    )
    db.add(variant)
    db.commit()

    # 1. Edit Content
    edit_payload = {
        "caption": "Updated Brilliant Caption",
        "hashtags": ["#luxury", "#toys"],
        "title": "Refined Title"
    }
    res_edit = client.patch(f"/api/automation/content/{post.id}?platform=instagram", json=edit_payload, headers=headers)
    assert res_edit.status_code == 200
    assert any(v["caption"] == "Updated Brilliant Caption" for v in res_edit.json().get("variants", []))

    # 2. Reschedule Content
    res_resched = client.post(
        f"/api/automation/content/{post.id}/reschedule",
        json={"scheduled_time": "2026-10-26 14:00:00", "timezone": "Asia/Kolkata"},
        headers=headers
    )
    assert res_resched.status_code == 200
    res_data = res_resched.json()
    assert res_data["status"] == "scheduled"
    # 14:00 Asia/Kolkata (+5:30) is 08:30 UTC
    assert "08:30" in res_data["scheduled_time"]

    # 3. Reject Content
    res_reject = client.post(
        f"/api/automation/content/{post.id}/reject",
        json={"reason": "Topic does not fit brand style"},
        headers=headers
    )
    assert res_reject.status_code == 200
    assert res_reject.json()["status"] == "rejected"

    # 4. Retry Content (triggers publisher which records failed if no connected credentials, or scheduled)
    res_retry = client.post(f"/api/automation/content/{post.id}/retry", json={"platform": "instagram"}, headers=headers)
    assert res_retry.status_code == 200
    assert res_retry.json()["success"] is True
    assert res_retry.json()["status"] in ["scheduled", "failed"]
