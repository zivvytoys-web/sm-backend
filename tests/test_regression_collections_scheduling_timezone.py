import pytest
import uuid
from datetime import datetime, timedelta
import pytz
from fastapi import HTTPException
from app.models.user import User
from app.models.organization import Organization
from app.models.collection import Collection
from app.models.media_asset import MediaAsset
from app.models.scheduled_post import ScheduledPost
from app.models.schedule_rule import ScheduleRule
from app.schemas.collections import CollectionCreate, CollectionUpdate
from app.schemas.scheduling import ScheduledPostCreate, ScheduledPostUpdate
from app.services.collection_service import CollectionService
from app.services.scheduler_engine import SchedulerEngine
from app.scheduler.publishing_worker import PublishingWorker
from app.api.routes.scheduler import (
    normalize_and_validate_schedule_time,
    to_utc_iso,
    serialize_post
)
from app.utils.enums import MediaType, UserRole


@pytest.fixture
def regression_setup(db_session):
    uid = uuid.uuid4().hex[:6]
    # Create Organization A
    org_a = Organization(name=f"Agency Alpha {uid}", slug=f"agency-alpha-{uid}")
    db_session.add(org_a)
    db_session.commit()
    db_session.refresh(org_a)

    # Admin User in Org A
    user_a = User(
        email=f"admin_alpha_{uid}@alpha.com",
        full_name="Alpha Admin",
        hashed_password="secret_hash_a",
        role=UserRole.ADMIN,
        organization_id=org_a.id
    )
    db_session.add(user_a)

    # Create Organization B for isolation testing
    org_b = Organization(name=f"Agency Beta {uid}", slug=f"agency-beta-{uid}")
    db_session.add(org_b)
    db_session.commit()
    db_session.refresh(org_b)

    # Admin User in Org B
    user_b = User(
        email=f"admin_beta_{uid}@beta.com",
        full_name="Beta Admin",
        hashed_password="secret_hash_b",
        role=UserRole.ADMIN,
        organization_id=org_b.id
    )
    db_session.add(user_b)
    db_session.commit()
    db_session.refresh(user_a)
    db_session.refresh(user_b)

    # Media Asset in Org A
    media1 = MediaAsset(
        file_name="product_launch.jpg",
        file_url="https://res.cloudinary.com/demo/image/upload/sample.jpg",
        public_id="prod_launch",
        media_type=MediaType.IMAGE,
        title="Product Launch",
        organization_id=org_a.id,
        user_id=user_a.id
    )
    media2 = MediaAsset(
        file_name="promo_video.mp4",
        file_url="https://res.cloudinary.com/demo/video/upload/sample.mp4",
        public_id="promo_vid",
        media_type=MediaType.VIDEO,
        title="Promo Video",
        organization_id=org_a.id,
        user_id=user_a.id
    )
    db_session.add_all([media1, media2])
    db_session.commit()
    db_session.refresh(media1)
    db_session.refresh(media2)

    return {
        "org_a": org_a,
        "user_a": user_a,
        "org_b": org_b,
        "user_b": user_b,
        "media1": media1,
        "media2": media2
    }


# ==============================================================================
# ISSUE 1: COLLECTION TESTS
# ==============================================================================

def test_collection_create_with_valid_name(db_session, regression_setup):
    """Test creating a collection with valid name and optional description."""
    user = regression_setup["user_a"]
    col_data = CollectionCreate(
        name="Summer Campaign 2026",
        description="Curated promotional assets for summer.",
        cover_asset_id=regression_setup["media1"].id
    )
    col = CollectionService.create_collection(db_session, user, col_data)

    assert col.id is not None
    assert col.name == "Summer Campaign 2026"
    assert col.description == "Curated promotional assets for summer."
    assert col.cover_asset_id == regression_setup["media1"].id
    assert col.organization_id == user.organization_id

    # Verify query
    listed = CollectionService.list_collections(db_session, user.organization_id)
    assert len(listed) == 1
    assert listed[0]["id"] == col.id
    assert listed[0]["cover_asset_url"] == regression_setup["media1"].file_url


def test_collection_rejects_missing_or_invalid_name(db_session, regression_setup):
    """Test that missing, empty, or whitespace-only names are strictly rejected."""
    user = regression_setup["user_a"]

    # Pydantic schema level rejection
    with pytest.raises(Exception):
        CollectionCreate(name="")

    with pytest.raises(Exception):
        CollectionCreate(name="   ")

    # Service level rejection
    class MockInvalidData:
        name = "   "
        description = None
        cover_asset_id = None

    with pytest.raises(HTTPException) as exc_info:
        CollectionService.create_collection(db_session, user, MockInvalidData())
    assert exc_info.value.status_code == 400
    assert "cannot be empty" in exc_info.value.detail


def test_collection_edit_and_delete_with_media_preservation(db_session, regression_setup):
    """Test editing collection and verifying media is preserved unless delete_media=True."""
    user = regression_setup["user_a"]
    media1 = regression_setup["media1"]

    col = CollectionService.create_collection(
        db_session,
        user,
        CollectionCreate(name="Autumn Promo", description="Original")
    )
    # Assign media1 to collection
    CollectionService.add_media_to_collection(db_session, col.id, user.organization_id, [media1.id])

    # Edit collection
    updated = CollectionService.update_collection(
        db_session,
        col.id,
        user.organization_id,
        CollectionUpdate(name="Autumn Promo Updated", description="New description")
    )
    assert updated.name == "Autumn Promo Updated"
    assert updated.description == "New description"

    # Delete collection without delete_media flag
    CollectionService.delete_collection(db_session, col.id, user.organization_id, delete_media=False)

    # Verify collection is deleted
    assert db_session.query(Collection).filter(Collection.id == col.id).first() is None

    # CRITICAL: Underlying media asset MUST still exist!
    persisted_media = db_session.query(MediaAsset).filter(MediaAsset.id == media1.id).first()
    assert persisted_media is not None
    assert persisted_media.id == media1.id


def test_collection_unauthorized_access_prevented(db_session, regression_setup):
    """Verify unauthorized users in another organization cannot access or modify collections."""
    user_a = regression_setup["user_a"]
    user_b = regression_setup["user_b"]

    col = CollectionService.create_collection(
        db_session,
        user_a,
        CollectionCreate(name="Alpha Secret Collection")
    )

    # User B should receive 404/Forbidden when trying to access User A's collection
    with pytest.raises(HTTPException) as exc:
        CollectionService.get_collection(db_session, col.id, user_b.organization_id)
    assert exc.value.status_code == 404

    # User B should not see it in listed collections
    b_cols = CollectionService.list_collections(db_session, user_b.organization_id)
    assert len(b_cols) == 0


# ==============================================================================
# ISSUE 2 & 4: SCHEDULED POST EDIT & WORKER CONSISTENCY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_scheduled_post_caption_and_schedule_edit(db_session, regression_setup):
    """Test loading an existing post, modifying caption, date, time, and verifying persistence."""
    media = regression_setup["media1"]
    future_time_1 = datetime.utcnow() + timedelta(days=2)

    post = ScheduledPost(
        title="Original Post",
        caption="Original Caption",
        hashtags=["first"],
        internal_tags=["tag1"],
        platform="instagram",
        scheduled_time=future_time_1,
        timezone="Asia/Kolkata",
        status="scheduled",
        media_asset_id=media.id
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)

    # Change caption, hashtags, and reschedule to future_time_2
    future_time_2 = datetime.utcnow() + timedelta(days=5)
    post.caption = "Updated High-Converting Caption"
    post.hashtags = ["first", "updated", "viral"]
    post.scheduled_time = future_time_2
    db_session.commit()
    db_session.refresh(post)

    assert post.caption == "Updated High-Converting Caption"
    assert "viral" in post.hashtags
    assert post.scheduled_time == future_time_2


@pytest.mark.asyncio
async def test_obsolete_job_invalidation_and_idempotency(db_session, regression_setup):
    """
    Verify that when a post's time is changed or cancelled, obsolete pending triggers
    cannot publish it, and published posts are never duplicated.
    """
    media = regression_setup["media1"]

    # 1. Post rescheduled into the future
    future_time = datetime.utcnow() + timedelta(hours=3)
    post = ScheduledPost(
        title="Rescheduled Post",
        caption="Will publish in 3 hours",
        platform="instagram",
        scheduled_time=future_time,
        timezone="Asia/Kolkata",
        status="scheduled",
        media_asset_id=media.id
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)

    # Worker executes at previous/earlier time (is_manual=False)
    res = await PublishingWorker.publish_single_post(db_session, post.id, is_manual=False)
    assert res["success"] is False
    assert "not due yet" in res["error"]
    assert post.status == "scheduled"

    # 2. Cancelled post must never publish
    post.status = "cancelled"
    db_session.commit()
    cancel_res = await PublishingWorker.publish_single_post(db_session, post.id, is_manual=False)
    assert cancel_res["success"] is False
    assert "cannot be published" in cancel_res["error"]

    # 3. Already published post must be idempotent
    post.status = "published"
    post.publish_metadata = {"platform_post_id": "live_12345"}
    db_session.commit()
    pub_res = await PublishingWorker.publish_single_post(db_session, post.id, is_manual=False)
    assert pub_res["success"] is True
    assert pub_res.get("already_published") is True


# ==============================================================================
# ISSUE 3: TIMEZONE PIPELINE REGRESSION TESTS
# ==============================================================================

def test_timezone_manual_selection_asia_kolkata():
    """
    Scenario: Admin manually selects 6:30 PM on October 13, 2026 in Asia/Kolkata.
    Requirement:
    - Interpreted in Asia/Kolkata (IST = UTC+5:30)
    - Normalized to UTC naive for DB persistence: 1:00 PM (13:00) on October 13, 2026.
    - Serialized API timestamp: explicit ISO 8601 with +00:00.
    - Roundtrip display in Asia/Kolkata: recovers exactly 6:30 PM on October 13, 2026.
    """
    input_naive = datetime(2026, 10, 13, 18, 30, 0)
    target_tz = "Asia/Kolkata"

    # 1. Normalize to UTC for DB persistence (allow_past=True for deterministic test timestamp)
    utc_naive = normalize_and_validate_schedule_time(input_naive, tz_name=target_tz, allow_past=True)

    # 18:30 IST minus 5:30 = 13:00 UTC
    assert utc_naive == datetime(2026, 10, 13, 13, 0, 0)

    # 2. Serialize to API
    api_iso = to_utc_iso(utc_naive)
    assert api_iso == "2026-10-13T13:00:00+00:00"

    # 3. Roundtrip deserialization back in Asia/Kolkata
    parsed_utc = datetime.fromisoformat(api_iso)
    kolkata_tz = pytz.timezone("Asia/Kolkata")
    restored_local = parsed_utc.astimezone(kolkata_tz)

    assert restored_local.year == 2026
    assert restored_local.month == 10
    assert restored_local.day == 13
    assert restored_local.hour == 18
    assert restored_local.minute == 30
    assert restored_local.strftime("%I:%M %p") == "06:30 PM"


def test_timezone_midnight_crossing():
    """
    Scenario: Admin selects 2:00 AM on October 14, 2026 in Asia/Kolkata.
    Crossing midnight: 02:00 IST is 20:30 on October 13 in UTC (different day!).
    """
    input_naive = datetime(2026, 10, 14, 2, 0, 0)
    utc_naive = normalize_and_validate_schedule_time(input_naive, tz_name="Asia/Kolkata", allow_past=True)

    # Should cross backwards into previous date in UTC: Oct 13 20:30:00
    assert utc_naive.date() == datetime(2026, 10, 13).date()
    assert utc_naive.hour == 20
    assert utc_naive.minute == 30

    # Deserializing back into Asia/Kolkata restores October 14, 02:00 AM
    api_iso = to_utc_iso(utc_naive)
    parsed_utc = datetime.fromisoformat(api_iso)
    kolkata_dt = parsed_utc.astimezone(pytz.timezone("Asia/Kolkata"))

    assert kolkata_dt.date() == datetime(2026, 10, 14).date()
    assert kolkata_dt.hour == 2
    assert kolkata_dt.minute == 0

def test_post_scheduled_in_past_is_rejected():
    """Verify that scheduling a post in the past raises HTTP 400 with a clear error."""
    past_time = datetime(2020, 1, 1, 10, 0, 0)
    with pytest.raises(HTTPException) as exc:
        normalize_and_validate_schedule_time(past_time, tz_name="Asia/Kolkata", allow_past=False)
    assert exc.value.status_code == 400
    assert "cannot be in the past" in exc.value.detail


def test_daylight_saving_transition():
    """
    Verify timezone handling correctly respects Daylight Saving Time transitions.
    America/New_York is EDT (UTC-4) in July and EST (UTC-5) in December.
    """
    tz_name = "America/New_York"

    # Summer: EDT (UTC-4)
    summer_dt = datetime(2026, 7, 15, 12, 0, 0)
    summer_utc = normalize_and_validate_schedule_time(summer_dt, tz_name=tz_name, allow_past=True)
    # 12:00 EDT -> 16:00 UTC
    assert summer_utc.hour == 16

    # Winter: EST (UTC-5)
    winter_dt = datetime(2026, 12, 15, 12, 0, 0)
    winter_utc = normalize_and_validate_schedule_time(winter_dt, tz_name=tz_name, allow_past=True)
    # 12:00 EST -> 17:00 UTC
    assert winter_utc.hour == 17


def test_serialize_post_contains_explicit_timezone_metadata(db_session, regression_setup):
    """Verify that serialize_post emits unambiguous ISO timestamps and timezone."""
    post = ScheduledPost(
        title="Metadata Check Post",
        caption="Checking API contract",
        platform="twitter",
        scheduled_time=datetime(2026, 11, 20, 10, 30, 0),
        timezone="Asia/Kolkata",
        status="scheduled"
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)

    serialized = serialize_post(post)
    assert serialized["timezone"] == "Asia/Kolkata"
    assert serialized["scheduled_time"].endswith("+00:00")
    assert "2026-11-20T10:30:00" in serialized["scheduled_time"]
