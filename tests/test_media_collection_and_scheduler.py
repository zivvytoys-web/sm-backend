import pytest
import uuid
from datetime import datetime, date, time, timedelta
import pytz
from app.models.user import User
from app.models.organization import Organization
from app.models.collection import Collection
from app.models.media_asset import MediaAsset
from app.models.schedule_rule import ScheduleRule
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.brand_profile import BrandProfile
from app.schemas.collections import CollectionCreate, CollectionUpdate
from app.schemas.scheduling_rule import ScheduleRuleCreate, ScheduleRuleUpdate
from app.services.collection_service import CollectionService
from app.services.scheduler_engine import SchedulerEngine
from app.scheduler.publishing_worker import PublishingWorker
from app.utils.enums import MediaType, UserRole


@pytest.fixture
def test_setup(db_session):
    uid = uuid.uuid4().hex[:6]
    org = Organization(name=f"Marketing Agency Inc {uid}", slug=f"marketing-agency-inc-{uid}")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)

    user = User(
        email=f"admin_{uid}@marketingagency.com",
        full_name="Admin Director",
        hashed_password="hashed_secret",
        role=UserRole.ADMIN,
        organization_id=org.id
    )
    db_session.add(user)

    brand = BrandProfile(
        name="Acme Toys",
        industry="Retail",
        organization_id=org.id
    )
    db_session.add(brand)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(brand)

    # Connected social account
    acc = SocialAccount(
        platform="instagram",
        account_name="acme_toys_official",
        account_id="1784140001",
        access_token="test_encrypted_token",
        is_connected=True,
        brand_profile_id=brand.id
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)

    # 3 media assets
    media1 = MediaAsset(
        file_name="summer_banner.jpg",
        file_url="https://res.cloudinary.com/demo/image/upload/sample.jpg",
        public_id="sample_banner",
        media_type=MediaType.IMAGE,
        title="Summer Promotion Banner",
        description="Great summer deals on toys and games",
        tags=["summer", "toys", "sale"],
        organization_id=org.id,
        user_id=user.id
    )
    media2 = MediaAsset(
        file_name="toy_demo.mp4",
        file_url="https://res.cloudinary.com/demo/video/upload/demo.mp4",
        public_id="sample_video",
        media_type=MediaType.VIDEO,
        title="Action Figure Demo Video",
        description="Demo reel for new transformer figure",
        tags=["actionfigure", "demo", "reels"],
        organization_id=org.id,
        user_id=user.id
    )
    media3 = MediaAsset(
        file_name="autumn_preview.jpg",
        file_url="https://res.cloudinary.com/demo/image/upload/autumn.jpg",
        public_id="autumn_preview",
        media_type=MediaType.IMAGE,
        title="Autumn Collection Preview",
        description="Coming soon this autumn",
        tags=["autumn", "preview"],
        organization_id=org.id,
        user_id=user.id
    )
    db_session.add_all([media1, media2, media3])
    db_session.commit()
    db_session.refresh(media1)
    db_session.refresh(media2)
    db_session.refresh(media3)

    return {
        "org": org,
        "user": user,
        "brand": brand,
        "account": acc,
        "media": [media1, media2, media3]
    }


def test_collection_lifecycle_and_media_association(db_session, test_setup):
    user = test_setup["user"]
    org = test_setup["org"]
    media = test_setup["media"]

    # 1. Create collection
    create_data = CollectionCreate(
        name="Summer Toy Promotions",
        description="High engagement assets for summer campaign"
    )
    col = CollectionService.create_collection(db_session, user, create_data)
    assert col.id is not None
    assert col.name == "Summer Toy Promotions"

    # 2. Add media to collection
    CollectionService.add_media_to_collection(db_session, col.id, org.id, [media[0].id, media[1].id])
    assert len(col.media_assets) == 2
    assert col.cover_asset_id == media[0].id

    # 3. Create second collection and test moving media
    col2 = CollectionService.create_collection(
        db_session,
        user,
        CollectionCreate(name="Video Reels")
    )
    CollectionService.move_media(
        db_session,
        source_collection_id=col.id,
        target_collection_id=col2.id,
        organization_id=org.id,
        media_ids=[media[1].id]
    )

    db_session.refresh(col)
    db_session.refresh(col2)
    assert len(col.media_assets) == 1
    assert len(col2.media_assets) == 1
    assert col2.media_assets[0].id == media[1].id

    # 4. Safe delete collection without deleting underlying media
    media_id_before = media[0].id
    CollectionService.delete_collection(db_session, col.id, org.id, delete_media=False)
    still_existing = db_session.query(MediaAsset).filter(MediaAsset.id == media_id_before).first()
    assert still_existing is not None
    assert still_existing.file_name == "summer_banner.jpg"


def test_scheduler_engine_hourly_recurrence_window(db_session, test_setup):
    org = test_setup["org"]
    user = test_setup["user"]
    acc = test_setup["account"]

    # Rule: Every 2 hours between 09:00 and 19:00 in Asia/Kolkata
    rule = ScheduleRule(
        name="Hourly Campaign",
        organization_id=org.id,
        user_id=user.id,
        platform="instagram",
        social_account_id=acc.id,
        recurrence_type="hourly",
        recurrence_interval=2,
        start_time="09:00",
        end_time="19:00",
        timezone="Asia/Kolkata",
        max_posts_per_day=5,
        status="active"
    )

    tz = pytz.timezone("Asia/Kolkata")
    # Simulate current time at 08:00 AM Kolkata
    test_dt_tz = tz.localize(datetime(2026, 10, 15, 8, 0, 0))
    test_dt_utc = test_dt_tz.astimezone(pytz.utc).replace(tzinfo=None)

    slots = SchedulerEngine.calculate_upcoming_slots(rule, from_utc=test_dt_utc, max_slots=5)
    assert len(slots) == 5

    # Check that slots correspond to 9:00, 11:00, 13:00, 15:00, 17:00 in Asia/Kolkata
    tz_slots = [pytz.utc.localize(s).astimezone(tz) for s in slots]
    hours = [s.hour for s in tz_slots]
    assert hours == [9, 11, 13, 15, 17]


def test_scheduler_engine_every_two_days_recurrence(db_session, test_setup):
    org = test_setup["org"]
    user = test_setup["user"]

    rule = ScheduleRule(
        name="Bi-Daily Campaign",
        organization_id=org.id,
        user_id=user.id,
        platform="instagram",
        recurrence_type="every_n_days",
        recurrence_interval=2,
        start_time="10:00",
        timezone="Asia/Kolkata",
        status="active"
    )

    tz = pytz.timezone("Asia/Kolkata")
    test_dt_tz = tz.localize(datetime(2026, 10, 15, 9, 0, 0))
    test_dt_utc = test_dt_tz.astimezone(pytz.utc).replace(tzinfo=None)

    slots = SchedulerEngine.calculate_upcoming_slots(rule, from_utc=test_dt_utc, max_slots=3)
    tz_slots = [pytz.utc.localize(s).astimezone(tz) for s in slots]
    days = [s.day for s in tz_slots]
    assert days == [15, 17, 19]


def test_scheduler_engine_weekday_constraint(db_session, test_setup):
    org = test_setup["org"]
    user = test_setup["user"]

    rule = ScheduleRule(
        name="Weekdays Only Campaign",
        organization_id=org.id,
        user_id=user.id,
        platform="linkedin",
        recurrence_type="weekdays",
        recurrence_interval=1,
        start_time="09:30",
        timezone="Asia/Kolkata",
        status="active"
    )

    tz = pytz.timezone("Asia/Kolkata")
    # Friday Oct 16, 2026 at 10:00 AM (past Friday slot)
    test_dt_tz = tz.localize(datetime(2026, 10, 16, 10, 0, 0))
    test_dt_utc = test_dt_tz.astimezone(pytz.utc).replace(tzinfo=None)

    slots = SchedulerEngine.calculate_upcoming_slots(rule, from_utc=test_dt_utc, max_slots=3)
    tz_slots = [pytz.utc.localize(s).astimezone(tz) for s in slots]
    # Next slots must skip Saturday 17 and Sunday 18, so next should be Monday Oct 19
    assert tz_slots[0].weekday() == 0  # Monday
    assert tz_slots[0].day == 19


def test_scheduler_recurring_rule_media_selection_and_generation(db_session, test_setup):
    org = test_setup["org"]
    user = test_setup["user"]
    media = test_setup["media"]
    acc = test_setup["account"]

    # 1. Collection with 2 assets
    col = CollectionService.create_collection(
        db_session,
        user,
        CollectionCreate(name="Auto Bucket")
    )
    CollectionService.add_media_to_collection(db_session, col.id, org.id, [media[0].id, media[1].id])

    # 2. Rule
    rule = SchedulerEngine.create_rule(
        db_session,
        user,
        ScheduleRuleCreate(
            name="Bucket Pipeline",
            collection_id=col.id,
            social_account_id=acc.id,
            platform="instagram",
            recurrence_type="daily",
            selection_strategy="order",
            on_exhaustion="pause",
            timezone="Asia/Kolkata"
        )
    )

    # Force rule.next_run_at to past so it triggers
    rule.next_run_at = datetime.utcnow() - timedelta(minutes=5)
    db_session.commit()

    import asyncio
    dispatched = asyncio.run(SchedulerEngine.process_recurring_rules(db_session))
    assert dispatched == 1

    # Check generated post
    post = db_session.query(ScheduledPost).filter(ScheduledPost.schedule_rule_id == rule.id).first()
    assert post is not None
    assert post.media_asset_id == media[0].id
    assert post.platform == "instagram"
    assert post.status == "scheduled"

    # Trigger second run
    rule.next_run_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()
    dispatched2 = asyncio.run(SchedulerEngine.process_recurring_rules(db_session))
    assert dispatched2 == 1

    post2 = db_session.query(ScheduledPost).filter(
        ScheduledPost.schedule_rule_id == rule.id,
        ScheduledPost.media_asset_id == media[1].id
    ).first()
    assert post2 is not None

    # Third run: collection exhausted -> should pause rule
    rule.next_run_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()
    dispatched3 = asyncio.run(SchedulerEngine.process_recurring_rules(db_session))
    assert dispatched3 == 0

    db_session.refresh(rule)
    assert rule.status == "paused"


def test_post_reschedule_cancel_and_retry(db_session, test_setup):
    media = test_setup["media"][0]
    acc = test_setup["account"]

    post = ScheduledPost(
        title="Promo Test",
        caption="Check our promo",
        platform="instagram",
        scheduled_time=datetime.utcnow() + timedelta(days=1),
        timezone="Asia/Kolkata",
        status="scheduled",
        media_asset_id=media.id,
        social_account_id=acc.id
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)

    # Reschedule
    new_time = datetime.utcnow() + timedelta(days=3)
    post.scheduled_time = new_time
    db_session.commit()
    db_session.refresh(post)
    assert post.scheduled_time == new_time

    # Cancel
    post.status = "cancelled"
    db_session.commit()
    db_session.refresh(post)
    assert post.status == "cancelled"

    # Simulate failure & retry
    post.status = "failed"
    post.error_message = "Rate limit exceeded"
    db_session.commit()

    # Retry resets error and marks scheduled
    post.status = "scheduled"
    post.error_message = None
    db_session.commit()
    db_session.refresh(post)
    assert post.status == "scheduled"
    assert post.error_message is None
