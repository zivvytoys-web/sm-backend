import asyncio
import io
import os
import uuid
from datetime import datetime
import pytest
import numpy as np
from PIL import Image
from unittest.mock import MagicMock, patch

from app.services.quality_check_engine import QualityCheckEngine, QUALITY_THRESHOLDS
from app.models.media_asset import MediaAsset
from app.models.content import Content
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.models.organization import Organization
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.utils.enums import MediaType, UserRole, ContentStatus, PlatformType


def create_sharp_pattern_image_bytes(pattern_id=1, size=(1080, 1080)):
    """Creates a high-contrast, high-sharpness pattern image that passes blur tests."""
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    tile_size = 60
    for y in range(0, size[1], tile_size):
        for x in range(0, size[0], tile_size):
            if ((x // tile_size) + (y // tile_size)) % 2 == 0:
                arr[y : y + tile_size, x : x + tile_size] = [230, 80, (pattern_id * 70) % 255]
            else:
                arr[y : y + tile_size, x : x + tile_size] = [20, 30, 90]
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def test_org_and_brand(db_session):
    org = Organization(name="Test Quality Org", slug=f"test-quality-org-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)

    brand = BrandProfile(
        organization_id=org.id,
        name="LuxeCosmetics",
        tone_of_voice="inspiring and educational",
        avoid="miracle cure, instant fix, guaranteed magic"
    )
    db_session.add(brand)
    db_session.commit()
    db_session.refresh(brand)
    return org, brand


# -----------------------------------------------------------------------------
# 1. IMAGE QUALITY & DUPLICATE CHECKS
# -----------------------------------------------------------------------------

def test_exact_duplicate_image(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    img_bytes = create_sharp_pattern_image_bytes(pattern_id=1)

    val1 = QualityCheckEngine.validate_image(
        db=db_session,
        image_bytes=img_bytes,
        organization_id=org.id
    )
    assert val1["decision"] == "PASS"
    assert val1["duplicate_status"] == "UNIQUE"
    assert val1["is_duplicate"] is False

    asset1 = MediaAsset(
        organization_id=org.id,
        user_id=1,
        file_name="hero1.jpg",
        file_url="https://res.cloudinary.com/test/hero1.jpg",
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        sha256_hash=val1["sha256_hash"],
        perceptual_hash=val1["perceptual_hash"],
        validation_status="PASS",
        quality_score=val1["quality_score"],
        is_duplicate=False
    )
    db_session.add(asset1)
    db_session.commit()
    db_session.refresh(asset1)

    val2 = QualityCheckEngine.validate_image(
        db=db_session,
        image_bytes=img_bytes,
        organization_id=org.id
    )
    assert val2["is_duplicate"] is True
    assert val2["duplicate_status"] == "CONFIRMED_DUPLICATE"
    assert val2["decision"] == "BLOCK"
    assert val2["duplicate_of_id"] == asset1.id


def test_resized_and_recompressed_image_duplicate(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    orig_bytes = create_sharp_pattern_image_bytes(pattern_id=1, size=(1080, 1080))

    val_orig = QualityCheckEngine.validate_image(
        db=db_session,
        image_bytes=orig_bytes,
        organization_id=org.id
    )
    asset_orig = MediaAsset(
        organization_id=org.id,
        user_id=1,
        file_name="orig.jpg",
        file_url="https://res.cloudinary.com/test/orig.jpg",
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        sha256_hash=val_orig["sha256_hash"],
        perceptual_hash=val_orig["perceptual_hash"],
        validation_status="PASS",
        quality_score=val_orig["quality_score"],
        is_duplicate=False
    )
    db_session.add(asset_orig)
    db_session.commit()
    db_session.refresh(asset_orig)

    # Resized & recompressed
    orig_img = Image.open(io.BytesIO(orig_bytes))
    resized_img = orig_img.resize((720, 720), Image.Resampling.BILINEAR)
    buf_resized = io.BytesIO()
    resized_img.save(buf_resized, format="JPEG", quality=75)
    resized_bytes = buf_resized.getvalue()

    val_resized = QualityCheckEngine.validate_image(
        db=db_session,
        image_bytes=resized_bytes,
        organization_id=org.id
    )
    assert val_resized["is_duplicate"] is True
    assert val_resized["duplicate_status"] in ["CONFIRMED_DUPLICATE", "POTENTIAL_DUPLICATE"]
    assert val_resized["duplicate_of_id"] == asset_orig.id


def test_distinct_creative_images_pass(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    img_a = create_sharp_pattern_image_bytes(pattern_id=1)
    val_a = QualityCheckEngine.validate_image(db=db_session, image_bytes=img_a, organization_id=org.id)

    asset_a = MediaAsset(
        organization_id=org.id,
        user_id=1,
        file_name="pattern_a.jpg",
        file_url="https://res.cloudinary.com/test/pattern_a.jpg",
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        sha256_hash=val_a["sha256_hash"],
        perceptual_hash=val_a["perceptual_hash"],
        validation_status="PASS",
        quality_score=val_a["quality_score"],
        is_duplicate=False
    )
    db_session.add(asset_a)
    db_session.commit()

    # Pattern B has very different colors/structure
    img_b = create_sharp_pattern_image_bytes(pattern_id=5)
    val_b = QualityCheckEngine.validate_image(db=db_session, image_bytes=img_b, organization_id=org.id)

    assert val_b["is_duplicate"] is False
    assert val_b["duplicate_status"] == "UNIQUE"
    assert val_b["decision"] == "PASS"


def test_corrupted_or_invalid_media(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    garbage_bytes = b"CORRUPTED_NOT_VALID_IMAGE_BYTES"

    val = QualityCheckEngine.validate_image(
        db=db_session,
        image_bytes=garbage_bytes,
        organization_id=org.id
    )
    assert val["decision"] == "BLOCK"
    assert val["technical_score"] == 0.0
    assert "CORRUPTED_FILE" in val["failed_checks"]


# -----------------------------------------------------------------------------
# 2. VIDEO QUALITY & DUPLICATE CHECKS
# -----------------------------------------------------------------------------

def test_video_corrupted_file(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    garbage_video_bytes = b"CORRUPTED_VIDEO_CONTAINER_STREAM"

    val = QualityCheckEngine.validate_video(
        db=db_session,
        video_bytes=garbage_video_bytes,
        organization_id=org.id
    )
    assert val["decision"] == "BLOCK"
    assert "VIDEO_DECODE_FAILED" in val["failed_checks"]
    assert val["technical_score"] == 0.0


# -----------------------------------------------------------------------------
# 3. DESCRIPTION COPY QUALITY & DUPLICATE CHECKS
# -----------------------------------------------------------------------------

def test_exact_duplicate_description(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    copy_text = "Unlock radiant, youthful skin with our organic botanical serum. Rich in vitamin C and jojoba oil. Order today for 20% off!"

    val1 = QualityCheckEngine.validate_description(
        caption=copy_text,
        brand=brand,
        organization_id=org.id,
        db=db_session
    )
    assert val1["decision"] == "PASS"
    assert val1["duplicate_status"] == "UNIQUE"

    content1 = Content(
        organization_id=org.id,
        brand_profile_id=brand.id,
        title="Botanical Serum Launch",
        caption=copy_text,
        sha256_hash=val1["sha256_hash"],
        validation_status="PASS",
        quality_score=val1["quality_score"],
        is_duplicate=False
    )
    db_session.add(content1)
    db_session.commit()
    db_session.refresh(content1)

    # Identical normalized text
    val2 = QualityCheckEngine.validate_description(
        caption=copy_text.upper(),
        brand=brand,
        organization_id=org.id,
        db=db_session
    )
    assert val2["is_duplicate"] is True
    assert val2["decision"] == "BLOCK"
    assert val2["duplicate_status"] == "CONFIRMED_DUPLICATE"
    assert val2["duplicate_of_id"] == content1.id


def test_near_duplicate_rewritten_description(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    base_text = "Experience luminous beauty with our herbal elixir. Infused with natural rosehip and argan oil to nourish your complexion."

    val1 = QualityCheckEngine.validate_description(
        caption=base_text,
        brand=brand,
        organization_id=org.id,
        db=db_session
    )
    content1 = Content(
        organization_id=org.id,
        brand_profile_id=brand.id,
        title="Herbal Elixir",
        caption=base_text,
        sha256_hash=val1["sha256_hash"],
        validation_status="PASS",
        quality_score=val1["quality_score"],
        is_duplicate=False
    )
    db_session.add(content1)
    db_session.commit()
    db_session.refresh(content1)

    near_dup = "Experience luminous beauty with our herbal elixir! Infused with natural rosehip and argan oil to nourish your smooth complexion."
    val2 = QualityCheckEngine.validate_description(
        caption=near_dup,
        brand=brand,
        organization_id=org.id,
        db=db_session
    )
    assert val2["is_duplicate"] is True
    assert val2["similarity_score"] >= QUALITY_THRESHOLDS["DESCRIPTION_SIMILARITY_THRESHOLD"]
    assert val2["duplicate_of_id"] == content1.id
    assert val2["decision"] in ["REGENERATE", "BLOCK"]


def test_different_product_descriptions_allowed(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    post1_text = "Our foaming facial cleanser deeply purifies pores while soothing sensitive skin. Start your morning routine right."
    post2_text = "Hydrate your body all winter long with our ultra-nourishing shea body butter. Fragrance-free and dermatologist tested."

    val1 = QualityCheckEngine.validate_description(caption=post1_text, brand=brand, organization_id=org.id, db=db_session)
    c1 = Content(organization_id=org.id, brand_profile_id=brand.id, title="Facial Cleanser", caption=post1_text, sha256_hash=val1["sha256_hash"], validation_status="PASS", is_duplicate=False)
    db_session.add(c1)
    db_session.commit()

    val2 = QualityCheckEngine.validate_description(caption=post2_text, brand=brand, organization_id=org.id, db=db_session)
    assert val2["is_duplicate"] is False
    assert val2["duplicate_status"] == "UNIQUE"
    assert val2["decision"] == "PASS"


def test_prohibited_terms_and_brand_voice_violation(db_session, test_org_and_brand):
    org, brand = test_org_and_brand
    bad_text = "This miracle cure will erase all wrinkles in 2 seconds. Guaranteed magic results!"

    val = QualityCheckEngine.validate_description(
        caption=bad_text,
        brand=brand,
        organization_id=org.id,
        db=db_session
    )
    assert val["decision"] == "BLOCK"
    assert any("PROHIBITED" in f or "BANNED" in f or "AVOID" in f for f in val["failed_checks"])
    assert val["brand_score"] < 70


# -----------------------------------------------------------------------------
# 4. PUBLISHING HARD GATE INTEGRATION
# -----------------------------------------------------------------------------

def test_publishing_blocked_for_duplicate_content(db_session, test_org_and_brand):
    from app.scheduler.publishing_worker import PublishingWorker
    org, brand = test_org_and_brand

    duplicate_content = Content(
        organization_id=org.id,
        brand_profile_id=brand.id,
        title="Duplicate Post",
        caption="Some duplicate text",
        status=ContentStatus.APPROVED,
        validation_status="BLOCK",
        is_duplicate=True
    )
    db_session.add(duplicate_content)
    db_session.commit()
    db_session.refresh(duplicate_content)

    from datetime import datetime
    post = ScheduledPost(
        content_id=duplicate_content.id,
        title="Duplicate Post",
        caption="Some duplicate text",
        platform=PlatformType.INSTAGRAM,
        scheduled_time=datetime.utcnow(),
        status=ContentStatus.SCHEDULED
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)

    res = asyncio.run(PublishingWorker.publish_single_post(db_session, post.id))
    assert res["success"] is False
    assert res.get("blocked_by_quality_gate") is True
    assert "Publication blocked" in res.get("error", "")


def test_publishing_blocked_for_duplicate_attached_media(db_session, test_org_and_brand):
    from app.scheduler.publishing_worker import PublishingWorker
    org, brand = test_org_and_brand

    bad_media = MediaAsset(
        organization_id=org.id,
        user_id=1,
        file_name="bad_dup.jpg",
        file_url="https://res.cloudinary.com/test/bad_dup.jpg",
        media_type=MediaType.IMAGE,
        validation_status="BLOCK",
        is_duplicate=True
    )
    db_session.add(bad_media)
    db_session.commit()
    db_session.refresh(bad_media)

    content = Content(
        organization_id=org.id,
        brand_profile_id=brand.id,
        media_asset_id=bad_media.id,
        title="Post with duplicate photo",
        caption="Valid unique caption text here",
        status=ContentStatus.APPROVED,
        validation_status="PASS",
        is_duplicate=False
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    post = ScheduledPost(
        content_id=content.id,
        title="Post with duplicate photo",
        caption="Valid unique caption text here",
        platform=PlatformType.INSTAGRAM,
        scheduled_time=datetime.utcnow(),
        status=ContentStatus.SCHEDULED
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)

    res = asyncio.run(PublishingWorker.publish_single_post(db_session, post.id))
    assert res["success"] is False
    assert res.get("blocked_by_quality_gate") is True
    assert "Attached media asset" in res.get("error", "")


# -----------------------------------------------------------------------------
# 5. RETRY & AUTO-REGENERATION LIMITS
# -----------------------------------------------------------------------------

def test_auto_regeneration_retry_limit(db_session, test_org_and_brand):
    from app.api.routes.generations import generate_gemini_text
    from app.schemas.generation import GeminiTextGenerationRequest

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.organization_id = test_org_and_brand[0].id

    dup_caption = "Always identical duplicate marketing pitch for skincare."

    c_existing = Content(
        organization_id=mock_user.organization_id,
        brand_profile_id=test_org_and_brand[1].id,
        caption=dup_caption,
        sha256_hash=QualityCheckEngine._hash_text(dup_caption),
        validation_status="PASS",
        is_duplicate=False
    )
    db_session.add(c_existing)
    db_session.commit()

    with patch("app.services.gemini_service.GeminiService.generate_text", return_value={"caption": dup_caption, "headline": "Pitch", "hashtags": []}) as mock_gemini:
        req = GeminiTextGenerationRequest(
            prompt="Write a promo post",
            target_platforms=["instagram"]
        )
        res = asyncio.run(generate_gemini_text(
            payload=req,
            db=db_session,
            current_user=mock_user
        ))

        assert mock_gemini.call_count >= 3
        qc = res.result_data.get("quality_check", {})
        assert qc.get("retry_count") >= 3
        assert qc.get("retry_exhausted") is True

