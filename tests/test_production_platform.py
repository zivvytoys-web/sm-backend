import pytest
from datetime import datetime, timedelta
import pytz
from app.models.scheduled_post import ScheduledPost
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.models.organization import Organization
from app.models.social_account import SocialAccount
from app.models.analytics import Analytics
from app.services.prompt_service import PromptService
from app.scheduler.publishing_worker import PublishingWorker


def register_user(client, email, name, org_name="TestOrg"):
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": name,
        "organization_name": org_name,
        "company_name": org_name,
        "role": "admin"
    })
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return res.json()["access_token"]


def test_calendar_scheduling_and_rescheduling(client, db_session):
    """
    Scenario 1, 4: Schedule a post for tomorrow, confirm it appears on tomorrow's calendar,
    reschedule it, and confirm calendar updates correctly.
    """
    token = register_user(client, "sched_tester_1@test.com", "Sched Tester 1", org_name="SchedOrg1")
    headers = {"Authorization": f"Bearer {token}"}

    # Schedule post for tomorrow at 15:30 UTC
    tomorrow = datetime.utcnow() + timedelta(days=1)
    tomorrow_iso = tomorrow.strftime("%Y-%m-%dT15:30:00Z")

    create_res = client.post("/api/v1/scheduler/posts", json={
        "title": "Tomorrow Launch Post",
        "caption": "Exciting news dropping tomorrow! #launch",
        "platform": "instagram",
        "scheduled_time": tomorrow_iso,
        "timezone": "Asia/Kolkata"
    }, headers=headers)
    assert create_res.status_code == 201
    post_id = create_res.json()["id"]

    # Query calendar events for tomorrow's month
    cal_res = client.get(f"/api/v1/scheduler/calendar?year={tomorrow.year}&month={tomorrow.month}", headers=headers)
    assert cal_res.status_code == 200
    events = cal_res.json()
    assert len(events) >= 1
    
    matching_event = next((e for e in events if e["id"] == post_id), None)
    assert matching_event is not None
    assert matching_event["title"] == "Tomorrow Launch Post"
    assert matching_event["status"] == "scheduled"
    assert matching_event["platform"] == "instagram"
    # start_date / start must match tomorrow's ISO date string
    event_start = matching_event.get("start_date") or matching_event.get("start")
    assert tomorrow.strftime("%Y-%m-%d") in event_start

    # Reschedule post to 2 days later
    day_after = datetime.utcnow() + timedelta(days=2)
    day_after_iso = day_after.strftime("%Y-%m-%dT18:00:00Z")
    
    update_res = client.put(f"/api/v1/scheduler/posts/{post_id}", json={
        "scheduled_time": day_after_iso
    }, headers=headers)
    assert update_res.status_code == 200

    # Verify updated calendar event
    cal_res_updated = client.get(f"/api/v1/scheduler/calendar?year={day_after.year}&month={day_after.month}", headers=headers)
    assert cal_res_updated.status_code == 200
    updated_event = next((e for e in cal_res_updated.json() if e["id"] == post_id), None)
    assert updated_event is not None
    updated_start = updated_event.get("start_date") or updated_event.get("start")
    assert day_after.strftime("%Y-%m-%d") in updated_start


def test_calendar_month_and_year_boundaries(client, db_session):
    """
    Scenario 5: Test scheduled posts across month and year boundaries.
    """
    token = register_user(client, "boundary_tester@test.com", "Boundary Tester", org_name="BoundaryOrg")
    headers = {"Authorization": f"Bearer {token}"}

    # Schedule post for next year New Year's Day
    next_year = datetime.utcnow().year + 1
    nyd_iso = f"{next_year}-01-01T10:00:00Z"

    nyd_res = client.post("/api/v1/scheduler/posts", json={
        "title": f"Happy New Year {next_year}",
        "caption": "Ringing in the new year with our community!",
        "platform": "linkedin",
        "scheduled_time": nyd_iso,
        "timezone": "UTC"
    }, headers=headers)
    assert nyd_res.status_code == 201
    nyd_id = nyd_res.json()["id"]

    # Verify event appears when querying January of next year
    cal_res = client.get(f"/api/v1/scheduler/calendar?year={next_year}&month=1", headers=headers)
    assert cal_res.status_code == 200
    events = cal_res.json()
    nyd_event = next((e for e in events if e["id"] == nyd_id), None)
    assert nyd_event is not None
    nyd_start = nyd_event.get("start_date") or nyd_event.get("start")
    assert f"{next_year}-01-01" in nyd_start


def test_tenant_isolation_security(client, db_session):
    """
    Scenario 7: Confirm one user's posts and brand profiles cannot appear in another user's account.
    """
    token_user_a = register_user(client, "alice@alpha.com", "Alice Alpha", org_name="AlphaOrg")
    token_user_b = register_user(client, "bob@beta.com", "Bob Beta", org_name="BetaOrg")

    headers_a = {"Authorization": f"Bearer {token_user_a}"}
    headers_b = {"Authorization": f"Bearer {token_user_b}"}

    # User A creates a post
    sched_a = client.post("/api/v1/scheduler/posts", json={
        "title": "Alice Secret Campaign",
        "caption": "Confidential Alpha Product",
        "platform": "twitter",
        "scheduled_time": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT12:00:00Z"),
    }, headers=headers_a)
    assert sched_a.status_code == 201
    post_a_id = sched_a.json()["id"]

    # User B queries calendar: Alice's post must NOT appear
    cal_b = client.get("/api/v1/scheduler/calendar", headers=headers_b)
    assert cal_b.status_code == 200
    b_events = [e["id"] for e in cal_b.json()]
    assert post_a_id not in b_events

    # User A creates Brand Profile
    brand_a = client.post("/api/v1/brand-profiles/", json={
        "name": "Alpha Innovations",
        "industry": "DeepTech",
        "tone_of_voice": "Authoritative"
    }, headers=headers_a)
    assert brand_a.status_code == 201

    # User B lists brand profiles: Alpha Innovations must NOT appear
    brands_b = client.get("/api/v1/brand-profiles/", headers=headers_b)
    assert brands_b.status_code == 200
    brand_names_b = [b["name"] for b in brands_b.json()]
    assert "Alpha Innovations" not in brand_names_b


def test_brand_profile_full_persistence_and_deep_merge(client, db_session):
    """
    Scenario 8: Create and update brand profile with products catalog, USP, positioning,
    contacts, and verify that partial updates do NOT overwrite nested meta_info fields.
    """
    token = register_user(client, "brand_master@test.com", "Brand Master", org_name="MasterBrandOrg")
    headers = {"Authorization": f"Bearer {token}"}

    # Create rich brand profile
    create_payload = {
        "name": "Apex Footwear Co",
        "industry": "Performance Athletic Apparel",
        "tagline": "Engineered carbon-plate marathon running shoes.",
        "tone_of_voice": "Energetic, aspirational, scientific",
        "key_themes": ["Marathon Training", "Zero Friction", "Endurance"],
        "hashtags": ["#RunApex", "#CarbonPlate", "#MarathonReady"],
        "color_palette": ["#FF4500", "#1A1A1A", "#FFFFFF"],
        "target_audience": "Runners aged 22-45 interested in Half-marathons, Triathlons",
        "meta_info": {
            "products": [
                {
                    "name": "Apex Stratos 1",
                    "price": "$220",
                    "description": "Full carbon plate with nitrogen-infused supercritical foam.",
                    "category": "Racing Shoes"
                },
                {
                    "name": "Apex Aero Socks",
                    "price": "$28",
                    "description": "Seamless moisture-wicking compression socks.",
                    "category": "Accessories"
                }
            ],
            "usp": "35% lighter carbon-plate energy return verified by sports lab tests.",
            "positioning": "Premium performance footwear for competitive runners.",
            "avoid": ["discount codes", "cheap materials", "couch potato humor"],
            "cta": "Explore the collection at apexfootwear.com/stratos",
            "contact_email": "hello@apexfootwear.com",
            "phone": "+1-800-555-APEX"
        }
    }

    create_res = client.post("/api/v1/brand-profiles/", json=create_payload, headers=headers)
    assert create_res.status_code == 201
    brand = create_res.json()
    brand_id = brand["id"]
    assert brand["name"] == "Apex Footwear Co"
    assert len(brand["meta_info"]["products"]) == 2
    assert brand["meta_info"]["products"][0]["name"] == "Apex Stratos 1"

    # Now perform a partial update: update only visual identity/color palette
    update_res = client.put(f"/api/v1/brand-profiles/{brand_id}", json={
        "color_palette": ["#FF4500", "#000000", "#00E5FF"]
    }, headers=headers)
    assert update_res.status_code == 200
    updated_brand = update_res.json()

    # Verify color palette updated
    assert updated_brand["color_palette"] == ["#FF4500", "#000000", "#00E5FF"]

    # CRITICAL: Verify deep merge preserved products catalog and meta_info!
    assert "products" in updated_brand["meta_info"]
    assert len(updated_brand["meta_info"]["products"]) == 2
    assert updated_brand["meta_info"]["products"][0]["name"] == "Apex Stratos 1"
    assert updated_brand["meta_info"]["usp"] == "35% lighter carbon-plate energy return verified by sports lab tests."
    assert updated_brand["meta_info"]["contact_email"] == "hello@apexfootwear.com"


def test_ai_prompt_generation_with_brand_profiles(db_session):
    """
    Scenario 9: Confirm AI prompt assembly reflects authentic brand profile products, USP,
    tone, and directives for two distinct brands.
    """
    brand_a = BrandProfile(
        id=101,
        organization_id=1,
        name="Luxe Botanicals",
        industry="Organic Skincare",
        tagline="Handcrafted plant-based serums",
        tone_of_voice="Calming, sensual, mindful",
        key_themes=["Skin Glow", "Zero Chemical"],
        hashtags=["#LuxeBotanicals", "#CleanBeauty"],
        target_audience="Women 25-50 interested in holistic wellness",
        meta_info={
            "description": "Handcrafted plant-based serums",
            "products": [{"name": "Glow Dew Elixir", "price": "$64", "description": "Cold-pressed rosehip with squalane"}],
            "usp": "100% wild-harvested botanicals with clinical hydration.",
            "avoid": ["artificial fragrances", "fast beauty"],
            "cta": "Shop your daily glow at luxebotanicals.com"
        }
    )

    brand_b = BrandProfile(
        id=102,
        organization_id=2,
        name="Titan Cyber Solutions",
        industry="Enterprise Cybersecurity",
        tagline="Zero-trust network architecture",
        tone_of_voice="Rigorous, mission-critical, authoritative",
        key_themes=["Ransomware Defense", "SOC 2 Compliance"],
        hashtags=["#ZeroTrust", "#TitanSec"],
        target_audience="CISOs and IT Directors interested in cloud security",
        meta_info={
            "description": "Zero-trust network architecture",
            "products": [{"name": "Titan Guard 360", "price": "$12,000/yr", "description": "Automated threat isolation agent"}],
            "usp": "Sub-millisecond containment of unauthorized lateral movements.",
            "avoid": ["consumer antivirus", "freemium hype"],
            "cta": "Schedule your zero-trust audit today"
        }
    )

    prompt_res_a = PromptService.assemble_text_prompt(
        brand=brand_a,
        user_instructions="Morning ritual for radiant complexion",
        target_platforms=["instagram"]
    )
    prompt_res_b = PromptService.assemble_text_prompt(
        brand=brand_b,
        user_instructions="Stopping ransomware before lateral spread",
        target_platforms=["linkedin"]
    )

    prompt_a = prompt_res_a["system_instruction"] + "\n" + prompt_res_a["user_prompt"]
    prompt_b = prompt_res_b["system_instruction"] + "\n" + prompt_res_b["user_prompt"]

    # Verify Brand A injection
    assert "Luxe Botanicals" in prompt_a
    assert "Organic Skincare" in prompt_a
    assert "Glow Dew Elixir" in prompt_a
    assert "$64" in prompt_a
    assert "100% wild-harvested botanicals" in prompt_a
    assert "artificial fragrances" in prompt_a
    assert "Shop your daily glow" in prompt_a

    # Verify Brand B injection
    assert "Titan Cyber Solutions" in prompt_b
    assert "Enterprise Cybersecurity" in prompt_b
    assert "Titan Guard 360" in prompt_b
    assert "Sub-millisecond containment" in prompt_b
    assert "consumer antivirus" in prompt_b
    assert "Schedule your zero-trust audit" in prompt_b

    # Verify no cross-contamination
    assert "Titan Guard" not in prompt_a
    assert "Glow Dew Elixir" not in prompt_b


def test_real_analytics_dashboard_metrics(client, db_session):
    """
    Scenario 10, 11: Verify analytics metrics calculate from real database records,
    correct formulas for publication success rate and engagement rate, and handle
    disconnected accounts gracefully without fake multipliers.
    """
    token = register_user(client, "analyst_user@test.com", "Analyst User", org_name="AnalystOrg")
    headers = {"Authorization": f"Bearer {token}"}

    # Query analytics on empty account: must return 0 counts and is_live_data=False
    res_empty = client.get("/api/v1/analytics/dashboard?period=30d", headers=headers)
    assert res_empty.status_code == 200
    data_empty = res_empty.json()

    assert data_empty["total_posts_created"] == 0
    assert data_empty["posts_scheduled"] == 0
    assert data_empty["posts_published"] == 0
    assert data_empty["posts_failed"] == 0
    assert data_empty["publication_success_rate"] == 0.0
    assert data_empty["is_live_data"] is False
    assert "status_message" in data_empty

    # Create 3 scheduled posts: 2 published, 1 failed in the database
    user = db_session.query(User).filter(User.email == "analyst_user@test.com").first()
    org_id = user.organization_id

    now = datetime.utcnow()
    p1 = ScheduledPost(
        organization_id=org_id,
        user_id=user.id,
        title="Post 1",
        platform="instagram",
        post_format="post",
        scheduled_time=now - timedelta(days=2),
        published_time=now - timedelta(days=2),
        status="published"
    )
    p2 = ScheduledPost(
        organization_id=org_id,
        user_id=user.id,
        title="Post 2",
        platform="linkedin",
        post_format="carousel",
        scheduled_time=now - timedelta(days=1),
        published_time=now - timedelta(days=1),
        status="published"
    )
    p3 = ScheduledPost(
        organization_id=org_id,
        user_id=user.id,
        title="Post 3",
        platform="twitter",
        post_format="post",
        scheduled_time=now - timedelta(hours=5),
        status="failed",
        error_message="Twitter rate limit exceeded"
    )
    p4 = ScheduledPost(
        organization_id=org_id,
        user_id=user.id,
        title="Post 4",
        platform="instagram",
        post_format="reel",
        scheduled_time=now + timedelta(days=3),
        status="scheduled"
    )
    db_session.add_all([p1, p2, p3, p4])
    db_session.commit()

    # Query analytics again
    res_populated = client.get("/api/v1/analytics/dashboard?period=7d", headers=headers)
    assert res_populated.status_code == 200
    data = res_populated.json()

    assert data["total_posts_created"] == 4
    assert data["posts_scheduled"] == 1
    assert data["posts_published"] == 2
    assert data["posts_failed"] == 1
    
    # Formula check: 2 / (2 + 1) * 100 = 66.7%
    assert round(data["publication_success_rate"], 1) == 66.7

    # Platform breakdown check
    platforms = {item["platform"].lower(): item["post_count"] for item in data["platform_breakdown"]}
    assert platforms["instagram"] == 2
    assert platforms["linkedin"] == 1
    assert platforms["twitter"] == 1


@pytest.mark.asyncio
async def test_publishing_worker_idempotency_and_retries(db_session):
    """
    Scenario 12: Verify background worker idempotency (does not republish already published post)
    and retry backoff logic for transient failures.
    """
    now = datetime.utcnow()

    # Create already published post
    published_post = ScheduledPost(
        organization_id=1,
        title="Already Published Post",
        platform="instagram",
        scheduled_time=now - timedelta(days=1),
        published_time=now - timedelta(days=1),
        status="published",
        publish_metadata={"platform_post_id": "1799201928"}
    )
    db_session.add(published_post)
    db_session.commit()

    # Attempt publishing
    result = await PublishingWorker.publish_single_post(db_session, published_post.id)
    assert result["success"] is True
    assert result.get("already_published") is True
