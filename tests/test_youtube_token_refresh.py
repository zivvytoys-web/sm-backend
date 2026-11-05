import sys
import os
import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.security import encrypt_token, decrypt_token
from app.db.session import SessionLocal
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.models.scheduled_post import ScheduledPost
from app.models.content import Content
from app.services.youtube_token_manager import (
    YouTubeTokenManager,
    YouTubeAuthError,
    YouTubeQuotaError,
    YouTubeAPIError
)
from app.integrations.social.youtube import YouTubeClient
from app.scheduler.publishing_worker import PublishingWorker
from app.utils.enums import PlatformType, ContentStatus


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def test_setup(db_session):
    """Sets up isolated Organization, BrandProfiles, and SocialAccounts for testing."""
    org = db_session.query(Organization).filter(Organization.slug == "test-yt-org").first()
    if not org:
        org = Organization(name="Test YouTube Org", slug="test-yt-org", settings={})
        db_session.add(org)
        db_session.flush()

    brand1 = db_session.query(BrandProfile).filter(
        BrandProfile.organization_id == org.id,
        BrandProfile.name == "Brand Alpha"
    ).first()
    if not brand1:
        brand1 = BrandProfile(organization_id=org.id, name="Brand Alpha", industry="Tech")
        db_session.add(brand1)
        db_session.flush()

    brand2 = db_session.query(BrandProfile).filter(
        BrandProfile.organization_id == org.id,
        BrandProfile.name == "Brand Beta"
    ).first()
    if not brand2:
        brand2 = BrandProfile(organization_id=org.id, name="Brand Beta", industry="Gaming")
        db_session.add(brand2)
        db_session.flush()

    # YouTube Account 1 (Brand Alpha)
    acc1 = db_session.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand1.id,
        SocialAccount.platform == "youtube"
    ).first()
    if not acc1:
        acc1 = SocialAccount(
            platform="youtube",
            account_name="Channel Alpha",
            account_id="UC_alpha_111",
            access_token=encrypt_token("initial_alpha_access_token"),
            refresh_token=encrypt_token("initial_alpha_refresh_token"),
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
            is_connected=True,
            meta_info={"scopes": ["https://www.googleapis.com/auth/youtube.upload"]},
            brand_profile_id=brand1.id
        )
        db_session.add(acc1)
        db_session.flush()

    # YouTube Account 2 (Brand Beta)
    acc2 = db_session.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand2.id,
        SocialAccount.platform == "youtube"
    ).first()
    if not acc2:
        acc2 = SocialAccount(
            platform="youtube",
            account_name="Channel Beta",
            account_id="UC_beta_222",
            access_token=encrypt_token("initial_beta_access_token"),
            refresh_token=encrypt_token("initial_beta_refresh_token"),
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
            is_connected=True,
            meta_info={"scopes": ["https://www.googleapis.com/auth/youtube.upload"]},
            brand_profile_id=brand2.id
        )
        db_session.add(acc2)
        db_session.flush()

    db_session.commit()
    return {"org": org, "brand1": brand1, "brand2": brand2, "acc1": acc1, "acc2": acc2}


@pytest.mark.asyncio
async def test_successful_access_token_refresh(db_session, test_setup):
    """Test successful token refresh with persistence and expiry update."""
    acc = test_setup["acc1"]

    # Mock Google OAuth token response
    mock_response = httpx.Response(
        status_code=200,
        json={
            "access_token": "ya29.new_refreshed_access_token_xyz",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
        },
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_client_id", "test_client_secret")):
            result = await YouTubeTokenManager.refresh_social_account_token(
                db=db_session,
                account=acc,
                force=True
            )

    assert result["refreshed"] is True
    assert result["access_token"] == "ya29.new_refreshed_access_token_xyz"

    # Verify DB persistence
    db_session.refresh(acc)
    assert decrypt_token(acc.access_token) == "ya29.new_refreshed_access_token_xyz"
    assert acc.token_expires_at > datetime.utcnow()
    assert acc.is_connected is True
    assert acc.meta_info.get("requires_reauthorization") is False


@pytest.mark.asyncio
async def test_expired_access_token_detection_and_auto_refresh(db_session, test_setup):
    """Test that an expired token is detected and automatically refreshed via ensure_valid_token."""
    acc = test_setup["acc1"]
    acc.token_expires_at = datetime.utcnow() - timedelta(minutes=15)
    db_session.commit()

    assert YouTubeTokenManager.is_token_expired(acc) is True

    mock_response = httpx.Response(
        status_code=200,
        json={"access_token": "ya29.auto_refreshed_due_to_expiry", "expires_in": 3600},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
            valid_token = await YouTubeTokenManager.ensure_valid_token(db_session, acc)

    assert valid_token == "ya29.auto_refreshed_due_to_expiry"
    db_session.refresh(acc)
    assert decrypt_token(acc.access_token) == "ya29.auto_refreshed_due_to_expiry"
    assert acc.token_expires_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_missing_refresh_token_triggers_reauthorization(db_session, test_setup):
    """Test that a missing refresh token fails gracefully and flags requires_reauthorization."""
    acc = test_setup["acc1"]
    acc.refresh_token = None
    db_session.commit()

    with pytest.raises(YouTubeAuthError) as exc_info:
        await YouTubeTokenManager.refresh_social_account_token(db=db_session, account=acc, force=True)

    assert "Reauthorization is required" in str(exc_info.value)
    db_session.refresh(acc)
    assert acc.is_connected is False
    assert acc.meta_info.get("requires_reauthorization") is True


@pytest.mark.asyncio
async def test_invalid_or_revoked_refresh_token(db_session, test_setup):
    """Test that an invalid_grant response marks account as requiring reauthorization in DB."""
    acc = test_setup["acc1"]
    acc.refresh_token = encrypt_token("revoked_refresh_token_999")
    db_session.commit()

    mock_response = httpx.Response(
        status_code=400,
        json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
            with pytest.raises(YouTubeAuthError) as exc_info:
                await YouTubeTokenManager.refresh_social_account_token(db=db_session, account=acc, force=True)

    assert "expired or was revoked" in str(exc_info.value)
    db_session.refresh(acc)
    assert acc.is_connected is False
    assert acc.meta_info.get("requires_reauthorization") is True
    assert acc.meta_info.get("verification_status") == "REQUIRES_REAUTHORIZATION"


@pytest.mark.asyncio
async def test_refresh_token_preservation_when_omitted_by_google(db_session, test_setup):
    """Test that existing valid refresh token is preserved if Google omits it in refresh response."""
    acc = test_setup["acc1"]
    original_refresh = "valid_refresh_token_to_preserve_123"
    acc.refresh_token = encrypt_token(original_refresh)
    db_session.commit()

    # Google refresh responses usually omit refresh_token
    mock_response = httpx.Response(
        status_code=200,
        json={"access_token": "ya29.new_access_token_alone", "expires_in": 3600},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
            await YouTubeTokenManager.refresh_social_account_token(db=db_session, account=acc, force=True)

    db_session.refresh(acc)
    assert decrypt_token(acc.access_token) == "ya29.new_access_token_alone"
    # Ensure refresh_token is completely intact
    assert decrypt_token(acc.refresh_token) == original_refresh


@pytest.mark.asyncio
async def test_multi_user_and_channel_isolation(db_session, test_setup):
    """Test that token refresh on Channel Alpha does not affect Channel Beta."""
    acc1 = test_setup["acc1"]
    acc2 = test_setup["acc2"]

    initial_beta_token = decrypt_token(acc2.access_token)

    mock_response = httpx.Response(
        status_code=200,
        json={"access_token": "ya29.alpha_unique_token_only", "expires_in": 3600},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
            await YouTubeTokenManager.refresh_social_account_token(db=db_session, account=acc1, force=True)

    db_session.refresh(acc1)
    db_session.refresh(acc2)

    assert decrypt_token(acc1.access_token) == "ya29.alpha_unique_token_only"
    assert decrypt_token(acc2.access_token) == initial_beta_token


@pytest.mark.asyncio
async def test_transient_network_retry_with_backoff(db_session, test_setup):
    """Test that transient HTTP 503 is retried and succeeds on subsequent attempt."""
    acc = test_setup["acc1"]

    resp_fail = httpx.Response(status_code=503, text="Service Unavailable", request=httpx.Request("POST", "https://oauth2.googleapis.com/token"))
    resp_success = httpx.Response(status_code=200, json={"access_token": "ya29.retry_success_token", "expires_in": 3600}, request=httpx.Request("POST", "https://oauth2.googleapis.com/token"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_fail, resp_success]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
                res = await YouTubeTokenManager.refresh_social_account_token(db=db_session, account=acc, force=True)

    assert res["access_token"] == "ya29.retry_success_token"
    assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_concurrent_refresh_locking(db_session, test_setup):
    """Test that concurrent refresh requests for the same account do not perform redundant Google API calls."""
    acc = test_setup["acc1"]
    acc.token_expires_at = datetime.utcnow() - timedelta(minutes=10)
    db_session.commit()

    mock_response = httpx.Response(
        status_code=200,
        json={"access_token": "ya29.concurrent_test_token", "expires_in": 3600},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    call_count = 0

    async def mock_post_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return mock_response

    with patch("httpx.AsyncClient.post", side_effect=mock_post_call):
        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
            task1 = YouTubeTokenManager.ensure_valid_token(db_session, acc)
            task2 = YouTubeTokenManager.ensure_valid_token(db_session, acc)
            results = await asyncio.gather(task1, task2)

    assert results[0] == "ya29.concurrent_test_token"
    assert results[1] == "ya29.concurrent_test_token"
    # Locking and re-check ensured Google token endpoint was only hit once!
    assert call_count == 1


@pytest.mark.asyncio
async def test_publishing_worker_with_expired_token(db_session, test_setup):
    """Test that PublishingWorker automatically refreshes token when publishing with an expired token."""
    acc = test_setup["acc1"]
    acc.token_expires_at = datetime.utcnow() - timedelta(minutes=30)
    db_session.commit()

    
    content = Content(
        brand_profile_id=test_setup["brand1"].id,
        caption="Test YouTube Video Upload",
        status=ContentStatus.DRAFT,
        meta_info={"mediaUrl": "https://example.com/test_video.mp4"}
    )
    db_session.add(content)
    db_session.flush()

    post = ScheduledPost(
        content_id=content.id,
        social_account_id=acc.id,
        status="scheduled",
        scheduled_time=datetime.utcnow() - timedelta(minutes=1),
        platform="youtube"
    )
    db_session.add(post)
    db_session.commit()

    # Mock token refresh
    mock_token_resp = httpx.Response(
        status_code=200,
        json={"access_token": "ya29.worker_refreshed_token_123", "expires_in": 3600},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token")
    )

    # Mock client publish_post
    mock_publish_result = {
        "success": True,
        "platform": "youtube",
        "platform_post_id": "yt_video_id_999",
        "permalink": "https://youtube.com/watch?v=yt_video_id_999",
        "status": "published",
        "access_token": "ya29.worker_refreshed_token_123"
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_token_resp
        with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_csec")):
            with patch.object(YouTubeClient, "publish_post", new_callable=AsyncMock) as mock_pub:
                mock_pub.return_value = mock_publish_result

                res = await PublishingWorker.publish_single_post(db_session, post.id)

    assert res["success"] is True
    db_session.refresh(post)
    assert post.status in ("published", ContentStatus.PUBLISHED) or str(post.status).lower() == "published"
    assert post.platform_post_id == "yt_video_id_999"

    # Verify that account access token in DB was refreshed
    db_session.refresh(acc)
    assert decrypt_token(acc.access_token) == "ya29.worker_refreshed_token_123"
