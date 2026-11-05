import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.models.content import Content
from app.models.scheduled_post import ScheduledPost
from app.core.security import encrypt_token, decrypt_token
from app.services.youtube_token_manager import YouTubeTokenManager, YouTubeAuthError
from app.services.meta_token_manager import MetaTokenManager, MetaAuthError
from app.services.social_token_manager import SocialTokenManager, SocialAuthError
from app.scheduler.publishing_worker import PublishingWorker


@pytest.fixture(scope="module")
def test_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    org = Organization(name="Test Org", slug="test-org")
    session.add(org)
    session.flush()

    user = User(
        email="token_test@example.com",
        hashed_password="hashed_pwd",
        full_name="Token Tester",
        organization_id=org.id,
        role="CREATOR",
        is_active=True
    )
    session.add(user)
    session.flush()

    brand = BrandProfile(
        organization_id=org.id,
        name="Test Brand",
        industry="Retail",
        tone_of_voice="Friendly"
    )
    session.add(brand)
    session.flush()

    yield session

    session.close()


@pytest.mark.asyncio
async def test_youtube_token_expiration_detection(test_db):
    """Test YouTubeTokenManager correctly flags expired and expiring tokens."""
    brand = test_db.query(BrandProfile).first()

    # Expired token
    acc_expired = SocialAccount(
        platform="youtube",
        account_name="Expired Channel",
        account_id="UC_exp_123",
        access_token=encrypt_token("ya29.expired"),
        refresh_token=encrypt_token("1//04_refresh_valid"),
        token_expires_at=datetime.utcnow() - timedelta(minutes=10),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(acc_expired)
    test_db.commit()

    assert YouTubeTokenManager.is_token_expired(acc_expired, buffer_seconds=300) is True

    # Fresh token
    acc_fresh = SocialAccount(
        platform="youtube",
        account_name="Fresh Channel",
        account_id="UC_fresh_123",
        access_token=encrypt_token("ya29.fresh"),
        refresh_token=encrypt_token("1//04_refresh_valid"),
        token_expires_at=datetime.utcnow() + timedelta(hours=1),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(acc_fresh)
    test_db.commit()

    assert YouTubeTokenManager.is_token_expired(acc_fresh, buffer_seconds=300) is False


@pytest.mark.asyncio
async def test_youtube_refresh_preserves_existing_refresh_token(test_db):
    """
    CRITICAL REQUIREMENT: Google omits refresh_token in renewal responses.
    Verify that existing refresh_token is preserved and NOT overwritten.
    """
    brand = test_db.query(BrandProfile).first()

    initial_refresh = "1//04_permanent_secret_refresh_token"
    acc = SocialAccount(
        platform="youtube",
        account_name="Preserve Refresh Test",
        account_id="UC_pres_123",
        access_token=encrypt_token("ya29.old_access"),
        refresh_token=encrypt_token(initial_refresh),
        token_expires_at=datetime.utcnow() - timedelta(minutes=5),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(acc)
    test_db.commit()

    mock_google_response = {
        "access_token": "ya29.brand_new_access_token_123",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/youtube.upload"
        # Notice: NO refresh_token returned by Google
    }

    with patch.object(YouTubeTokenManager, "_execute_google_token_refresh", new=AsyncMock(return_value=mock_google_response)), \
         patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_client_id", "test_client_secret")):

        res = await YouTubeTokenManager.refresh_social_account_token(test_db, acc, force=True)

        assert res["access_token"] == "ya29.brand_new_access_token_123"
        assert res["refreshed"] is True

        test_db.refresh(acc)
        # Verify access token is updated and encrypted
        assert decrypt_token(acc.access_token) == "ya29.brand_new_access_token_123"
        # Verify refresh token is PRESERVED intact
        assert decrypt_token(acc.refresh_token) == initial_refresh
        assert acc.is_connected is True
        assert acc.meta_info.get("verification_status") == "VERIFIED"
        assert acc.meta_info.get("requires_reauthorization") is False


@pytest.mark.asyncio
async def test_youtube_revocation_marks_reauthorization_required(test_db):
    """Test that Google 400 invalid_grant automatically flags requires_reauthorization."""
    brand = test_db.query(BrandProfile).first()

    acc = SocialAccount(
        platform="youtube",
        account_name="Revoked Channel",
        account_id="UC_rev_123",
        access_token=encrypt_token("ya29.revoked"),
        refresh_token=encrypt_token("1//04_revoked_refresh"),
        token_expires_at=datetime.utcnow() - timedelta(minutes=5),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(acc)
    test_db.commit()

    with patch.object(YouTubeTokenManager, "get_client_credentials", return_value=("test_cid", "test_secret")), \
         patch("httpx.AsyncClient.post") as mock_post:

        mock_post.return_value = MagicMock(
            status_code=400,
            json=lambda: {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
        )

        with pytest.raises(YouTubeAuthError) as exc_info:
            await YouTubeTokenManager.refresh_social_account_token(test_db, acc, force=True)

        assert "expired or was revoked" in str(exc_info.value)

        test_db.refresh(acc)
        assert acc.is_connected is False
        assert acc.meta_info.get("requires_reauthorization") is True
        assert acc.meta_info.get("verification_status") == "REQUIRES_REAUTHORIZATION"


@pytest.mark.asyncio
async def test_meta_token_renewal_flow(test_db):
    """
    Test MetaTokenManager long-lived token renewal via fb_exchange_token and Page Access Token re-stamping.
    """
    brand = test_db.query(BrandProfile).first()

    user_token = "EAAB_user_long_lived_token_60d"
    old_page_token = "EAAB_old_page_token"
    new_user_token = "EAAB_renewed_user_token_60d"
    new_page_token = "EAAB_fresh_stamped_page_token"

    fb_acc = SocialAccount(
        platform="facebook",
        account_name="Test Facebook Page",
        account_id="1092837465",
        access_token=encrypt_token(old_page_token),
        refresh_token=encrypt_token(user_token),
        token_expires_at=datetime.utcnow() - timedelta(days=2),  # Expired
        is_connected=True,
        meta_info={"auth_method": "OAUTH_2", "token_type": "PAGE"},
        brand_profile_id=brand.id
    )
    test_db.add(fb_acc)
    test_db.commit()

    mock_debug_data = {
        "is_valid": True,
        "expires_at": 0,
        "data_access_expires_at": int((datetime.utcnow() + timedelta(days=90)).timestamp()),
        "scopes": ["pages_manage_posts", "pages_read_engagement"],
        "type": "PAGE",
        "error": None
    }

    with patch.object(MetaTokenManager, "get_app_credentials", return_value=("fb_app_id", "fb_app_secret")), \
         patch.object(MetaTokenManager, "debug_token", new=AsyncMock(return_value=mock_debug_data)), \
         patch.object(MetaTokenManager, "_exchange_fb_token", new=AsyncMock(return_value={"access_token": new_user_token, "expires_in": 5184000})), \
         patch.object(MetaTokenManager, "_fetch_page_access_token", new=AsyncMock(return_value=new_page_token)):

        res = await MetaTokenManager.renew_meta_tokens(test_db, fb_acc, force=True)

        assert res["status"] == "HEALTHY"
        assert res["access_token"] == new_page_token
        assert res["refreshed"] is True

        test_db.refresh(fb_acc)
        assert decrypt_token(fb_acc.access_token) == new_page_token
        assert decrypt_token(fb_acc.refresh_token) == new_user_token
        assert fb_acc.is_connected is True
        assert fb_acc.token_expires_at > datetime.utcnow() + timedelta(days=50)
        assert fb_acc.meta_info.get("verification_status") == "HEALTHY"
        assert fb_acc.meta_info.get("requires_reauthorization") is False


@pytest.mark.asyncio
async def test_meta_revocation_marks_reauthorization_required(test_db):
    """Test Meta Error Code 190 (Token Expired/Revoked) flags requires_reauthorization."""
    brand = test_db.query(BrandProfile).first()

    fb_acc = SocialAccount(
        platform="facebook",
        account_name="Revoked FB Page",
        account_id="999888777",
        access_token=encrypt_token("EAAB_revoked_token"),
        refresh_token=None,  # No user token to fall back on
        token_expires_at=datetime.utcnow() - timedelta(days=1),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(fb_acc)
    test_db.commit()

    mock_debug_revoked = {
        "is_valid": False,
        "error": {
            "code": 190,
            "error_subcode": 463,
            "message": "Error validating access token: Session has expired."
        }
    }

    with patch.object(MetaTokenManager, "get_app_credentials", return_value=("app_id", "app_sec")), \
         patch.object(MetaTokenManager, "debug_token", new=AsyncMock(return_value=mock_debug_revoked)):

        with pytest.raises(MetaAuthError) as exc_info:
            await MetaTokenManager.renew_meta_tokens(test_db, fb_acc, force=True)

        assert "expired" in str(exc_info.value).lower()

        test_db.refresh(fb_acc)
        assert fb_acc.is_connected is False
        assert fb_acc.meta_info.get("requires_reauthorization") is True
        assert fb_acc.meta_info.get("verification_status") == "REQUIRES_REAUTHORIZATION"


@pytest.mark.asyncio
async def test_social_token_manager_unified_dispatch(test_db):
    """Test that SocialTokenManager dispatches to the correct manager per platform."""
    brand = test_db.query(BrandProfile).first()

    yt_acc = SocialAccount(
        platform="youtube",
        account_name="YT Dispatch Test",
        account_id="UC_disp",
        access_token=encrypt_token("ya29.valid_token"),
        token_expires_at=datetime.utcnow() + timedelta(hours=2),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(yt_acc)

    fb_acc = SocialAccount(
        platform="facebook",
        account_name="FB Dispatch Test",
        account_id="12345",
        access_token=encrypt_token("EAAB_valid_token"),
        token_expires_at=datetime.utcnow() + timedelta(days=30),
        is_connected=True,
        meta_info={},
        brand_profile_id=brand.id
    )
    test_db.add(fb_acc)
    test_db.commit()

    yt_tok = await SocialTokenManager.ensure_valid_token(test_db, yt_acc)
    assert yt_tok == "ya29.valid_token"

    fb_tok = await SocialTokenManager.ensure_valid_token(test_db, fb_acc)
    assert fb_tok == "EAAB_valid_token"


@pytest.mark.asyncio
async def test_publishing_worker_aborts_and_flags_on_auth_failure(test_db):
    """
    Test that background PublishingWorker calls SocialTokenManager.ensure_valid_token
    and cleanly marks post failed with auth_required: True when reconnection is needed.
    """
    brand = test_db.query(BrandProfile).first()

    acc = SocialAccount(
        platform="youtube",
        account_name="Publish Worker Auth Test",
        account_id="UC_pub_auth_fail",
        access_token=encrypt_token("ya29.dead"),
        refresh_token=encrypt_token("1//04_dead"),
        token_expires_at=datetime.utcnow() - timedelta(hours=2),
        is_connected=False,
        meta_info={"requires_reauthorization": True},
        brand_profile_id=brand.id
    )
    test_db.add(acc)
    test_db.flush()

    content = Content(
        brand_profile_id=brand.id,
        title="Scheduled Post 1",
        caption="Check out this awesome video!",
        status="approved"
    )
    test_db.add(content)
    test_db.flush()

    post = ScheduledPost(
        organization_id=brand.organization_id,
        content_id=content.id,
        social_account_id=acc.id,
        platform="youtube",
        status="scheduled",
        scheduled_time=datetime.utcnow() - timedelta(minutes=1)
    )
    test_db.add(post)
    test_db.commit()

    # PublishingWorker will call SocialTokenManager.ensure_valid_token
    with patch.object(SocialTokenManager, "ensure_valid_token", new=AsyncMock(side_effect=SocialAuthError("Reauthorization required."))):
        res = await PublishingWorker.publish_single_post(test_db, post.id, is_manual=True)

        assert res["success"] is False
        assert "reauthorization required" in res["error"].lower()

        test_db.refresh(post)
        assert post.status == "failed"
        assert "authentication failed" in post.error_message.lower()
        assert post.publish_metadata.get("auth_required") is True
