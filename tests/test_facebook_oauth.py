"""
Facebook / Meta OAuth 2.0 Full Test Suite
==========================================
Covers all spec requirements:
  - Callback URL mismatch detection
  - User denied / cancelled (error param from Meta)
  - Invalid / expired state (CSRF protection)
  - Token-exchange failure (bad code from Meta)
  - Missing Pages (no admin pages found)
  - Successful full connection + Instagram auto-link
  - Token encryption at rest
  - Test-connection health check
  - Meta data-deletion HMAC-SHA256 compliance
"""

import json
import base64
import hmac
import hashlib
import time
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.security import create_access_token, encrypt_token, decrypt_token
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.services.facebook_oauth_service import FacebookOAuthService
from app.models.platform_integration import PlatformIntegration


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def test_setup():
    settings.META_APP_ID = "123456789012345"
    settings.META_APP_SECRET = "test_meta_app_secret_994757e6abcdef"
    db = SessionLocal()

    plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == "facebook").first()
    if not plat:
        plat = PlatformIntegration(
            platform="facebook",
            display_name="Facebook Pages",
            client_id="1234567890",
            client_secret_encrypted=encrypt_token("test_meta_app_secret_123"),
            is_enabled=True,
            environment="development",
            scopes=["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
        )
        db.add(plat)
        db.commit()
        db.refresh(plat)

    org = db.query(Organization).filter(Organization.slug == "fb-test-org").first()
    if not org:
        org = Organization(name="Test Meta Org", slug="fb-test-org")
        db.add(org)
        db.commit()
        db.refresh(org)

    user = db.query(User).filter(User.email == "facebook_tester@example.com").first()
    if not user:
        user = User(
            organization_id=org.id,
            email="facebook_tester@example.com",
            hashed_password="hashed_pwd_test",
            full_name="Facebook Tester",
            role="creator",
            is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    brand = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
    if not brand:
        brand = BrandProfile(
            organization_id=org.id,
            name="Meta Brand Co",
            industry="Retail",
            tone_of_voice="Engaging"
        )
        db.add(brand)
        db.commit()
        db.refresh(brand)

    token = create_access_token(subject=user.id)
    headers = {"Authorization": f"Bearer {token}"}

    from app.db.dependencies import get_db
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {"db": db, "user": user, "brand": brand, "token": token, "headers": headers, "client": client}
    app.dependency_overrides.clear()
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_signed_request(payload_dict, app_secret):
    payload_json = json.dumps(payload_dict)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    sig = hmac.new(app_secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{sig_b64}.{payload_b64}"


class _R:
    def __init__(self, code, data):
        self.status_code = code
        self._json = data
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(data)
    def json(self):
        return self._json


# ===========================================================================
# 1. Authorization URL generation
# ===========================================================================

def test_facebook_connect_url_generation(test_setup):
    resp = test_setup["client"].get("/api/v1/social/facebook/connect", headers=test_setup["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"].startswith("fb_state_")
    assert "pages_manage_posts" in data["scopes"]
    assert "dialog/oauth" in data["authorization_url"]
    assert data["state"] in FacebookOAuthService._state_store


# ===========================================================================
# 2. User denied / cancelled
# ===========================================================================

def test_facebook_callback_user_denied(test_setup):
    res = test_setup["client"].get(
        "/api/v1/social/facebook/callback"
        "?error=access_denied&error_reason=user_denied"
        "&error_description=The+user+denied+your+request",
        follow_redirects=False
    )
    assert res.status_code in (302, 307)
    loc = res.headers["location"]
    assert "facebook_error" in loc
    assert "access_token" not in loc
    assert "EAAB" not in loc


# ===========================================================================
# 3. Missing code or state
# ===========================================================================

def test_facebook_callback_missing_code(test_setup):
    res = test_setup["client"].get("/api/v1/social/facebook/callback", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert "facebook_error" in res.headers["location"]


# ===========================================================================
# 4. Invalid state (CSRF spoofing)
# ===========================================================================

def test_facebook_callback_invalid_state(test_setup):
    res = test_setup["client"].get(
        "/api/v1/social/facebook/callback?code=real_code&state=spoofed_state_xyz",
        follow_redirects=False
    )
    assert res.status_code in (302, 307)
    assert "facebook_error" in res.headers["location"]


# ===========================================================================
# 5. Expired state
# ===========================================================================

def test_facebook_callback_expired_state(test_setup):
    user = test_setup["user"]
    expired = "fb_state_expired_test_abc123"
    FacebookOAuthService._state_store[expired] = {
        "user_id": user.id,
        "organization_id": user.organization_id,
        "brand_profile_id": None,
        "created_at": time.time() - 700,
        "expires_at": time.time() - 100,
        "redirect_uri": settings.META_REDIRECT_URI
    }
    res = test_setup["client"].get(
        f"/api/v1/social/facebook/callback?code=some_code&state={expired}",
        follow_redirects=False
    )
    assert res.status_code in (302, 307)
    assert "facebook_error" in res.headers["location"]


# ===========================================================================
# 6. Token-exchange failure
# ===========================================================================

@pytest.mark.asyncio
async def test_facebook_callback_token_exchange_failure(test_setup):
    db = test_setup["db"]
    user = test_setup["user"]
    brand = test_setup["brand"]
    conn = FacebookOAuthService.generate_authorization_url(
        user_id=user.id, organization_id=user.organization_id,
        brand_profile_id=brand.id, db=db
    )
    state = conn["state"]

    async def fail_get(url, params=None, **kw):
        if "oauth/access_token" in url:
            return _R(400, {"error": {"message": "Bad code.", "type": "OAuthException", "code": 100}})
        return _R(404, {})

    with patch("httpx.AsyncClient.get", side_effect=fail_get):
        res = test_setup["client"].get(
            f"/api/v1/social/facebook/callback?code=bad_code&state={state}",
            follow_redirects=False
        )
    assert res.status_code in (302, 307)
    loc = res.headers["location"]
    assert "facebook_error" in loc
    assert "access_token" not in loc


# ===========================================================================
# 7. No managed pages
# ===========================================================================

@pytest.mark.asyncio
async def test_facebook_callback_no_pages(test_setup):
    db = test_setup["db"]
    user = test_setup["user"]
    brand = test_setup["brand"]
    conn = FacebookOAuthService.generate_authorization_url(
        user_id=user.id, organization_id=user.organization_id,
        brand_profile_id=brand.id, db=db
    )
    state = conn["state"]

    async def no_pages_get(url, params=None, **kw):
        if "oauth/access_token" in url:
            return _R(200, {"access_token": "EAAB_long" if params and params.get("grant_type") == "fb_exchange_token" else "EAAB_short"})
        if "/me/accounts" in url:
            return _R(200, {"data": []})
        return _R(404, {})

    with patch("httpx.AsyncClient.get", side_effect=no_pages_get):
        res = test_setup["client"].get(
            f"/api/v1/social/facebook/callback?code=valid_code&state={state}",
            follow_redirects=False
        )
    assert res.status_code in (302, 307)
    assert "facebook_error" in res.headers["location"]


# ===========================================================================
# 8. Successful callback + page discovery (no raw tokens in responses)
# ===========================================================================

@pytest.mark.asyncio
async def test_facebook_callback_and_page_discovery(test_setup):
    client = test_setup["client"]
    headers = test_setup["headers"]
    db = test_setup["db"]
    user = test_setup["user"]
    brand = test_setup["brand"]

    conn = FacebookOAuthService.generate_authorization_url(
        user_id=user.id, organization_id=user.organization_id,
        brand_profile_id=brand.id, db=db
    )
    state = conn["state"]

    pages_payload = {"data": [{
        "id": "100200300400",
        "name": "Acme Fashion Page",
        "category": "Apparel & Clothing",
        "access_token": "EAAB_page_access_token_non_expiring_789",
        "picture": {"data": {"url": "https://facebook.com/acme_pic.jpg"}},
        "tasks": ["MANAGE", "CREATE_CONTENT"]
    }]}
    ig_payload = {"instagram_business_account": {
        "id": "17841400011223344",
        "username": "acmefashion_ig",
        "name": "Acme IG",
        "profile_picture_url": "https://instagram.com/pic.jpg"
    }}

    async def ok_get(url, params=None, **kw):
        if "oauth/access_token" in url:
            return _R(200, {"access_token": "EAAB_long" if params and params.get("grant_type") == "fb_exchange_token" else "EAAB_short"})
        if "/me/accounts" in url:
            return _R(200, pages_payload)
        if "/100200300400" in url:
            return _R(200, ig_payload)
        return _R(404, {})

    with patch("httpx.AsyncClient.get", side_effect=ok_get):
        cb = client.get(
            f"/api/v1/social/facebook/callback?code=mock_code&state={state}",
            follow_redirects=False
        )

    assert cb.status_code in (302, 307), cb.headers
    loc = cb.headers["location"]
    assert "facebook_auth=select_page" in loc
    assert "session_id=" in loc
    assert "access_token" not in loc
    assert "EAAB" not in loc

    session_id = loc.split("session_id=")[1].split("&")[0]

    pages_res = client.get(f"/api/v1/social/facebook/pages?session_id={session_id}", headers=headers)
    assert pages_res.status_code == 200
    pages = pages_res.json()
    assert len(pages) == 1
    assert pages[0]["id"] == "100200300400"
    assert pages[0]["name"] == "Acme Fashion Page"
    assert pages[0]["instagram_business_account"]["username"] == "acmefashion_ig"
    assert "raw_page_token" not in pages[0]
    assert "access_token" not in pages[0]


# ===========================================================================
# 9. Select page: token encryption + Instagram auto-link
# ===========================================================================

@pytest.mark.asyncio
async def test_select_facebook_page_and_token_encryption(test_setup):
    client = test_setup["client"]
    headers = test_setup["headers"]
    user = test_setup["user"]
    brand = test_setup["brand"]
    db = test_setup["db"]

    session_id = "fb_sess_test_persist_123"
    raw_page_token = "EAAB_super_secret_page_token_999"
    FacebookOAuthService._session_store[session_id] = {
        "user_id": user.id,
        "organization_id": user.organization_id,
        "brand_profile_id": brand.id,
        "created_at": time.time(),
        "expires_at": time.time() + 900,
        "pages": [{
            "id": "100200300400",
            "name": "Acme Fashion Page",
            "category": "Retail",
            "picture_url": "https://facebook.com/pic.jpg",
            "raw_page_token": raw_page_token,
            "instagram_business_account": {
                "id": "17841400011223344",
                "username": "acmefashion_ig",
                "name": "Acme IG",
                "profile_picture_url": "https://instagram.com/pic.jpg"
            }
        }]
    }

    async def debug_get(url, params=None, **kw):
        if "debug_token" in url:
            return _R(200, {"data": {
                "app_id": settings.META_APP_ID or "1234567890",
                "type": "PAGE",
                "is_valid": True,
                "scopes": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
            }})
        return _R(404, {})

    with patch("httpx.AsyncClient.get", side_effect=debug_get):
        res = client.post(
            "/api/v1/social/facebook/select-page",
            json={"session_id": session_id, "page_id": "100200300400", "brand_profile_id": brand.id},
            headers=headers
        )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert data["facebook_account"]["page_name"] == "Acme Fashion Page"
    assert data["instagram_account"] is not None
    assert data["instagram_account"]["account_name"] == "acmefashion_ig"

    fb_db = db.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand.id,
        SocialAccount.platform == "facebook"
    ).first()
    assert fb_db is not None
    assert fb_db.account_id == "100200300400"
    assert fb_db.access_token != raw_page_token
    assert decrypt_token(fb_db.access_token) == raw_page_token

    ig_db = db.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand.id,
        SocialAccount.platform == "instagram"
    ).first()
    assert ig_db is not None
    assert ig_db.account_id == "17841400011223344"
    assert decrypt_token(ig_db.access_token) == raw_page_token


# ===========================================================================
# 10. Live test-connection health check
# ===========================================================================

def test_facebook_test_connection_endpoint(test_setup):
    client = test_setup["client"]
    headers = test_setup["headers"]
    brand = test_setup["brand"]
    db = test_setup["db"]

    raw_token = "EAAB_test_connection_live_token"
    account = SocialAccount(
        brand_profile_id=brand.id, platform="facebook",
        account_name="Acme Live", account_id="100200300402",
        profile_url="https://facebook.com/100200300402",
        access_token=encrypt_token(raw_token), is_connected=True,
        meta_info={"auth_method": "OAUTH_2"}
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    async def live_get(url, params=None, **kw):
        if "100200300402" in url:
            return _R(200, {"id": "100200300402", "name": "Acme Live",
                            "link": "https://facebook.com/100200300402", "followers_count": 1420})
        return _R(404, {})

    with patch("httpx.AsyncClient.get", side_effect=live_get):
        res = client.post("/api/v1/social/facebook/test",
                          json={"account_id": account.id}, headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "healthy"
    assert data["followers"] == 1420


# ===========================================================================
# 11. Meta data-deletion HMAC compliance
# ===========================================================================

def test_meta_data_deletion_compliance(test_setup):
    client = test_setup["client"]
    db = test_setup["db"]
    brand = test_setup["brand"]

    user_id = "meta_user_99999"
    db.add(SocialAccount(
        brand_profile_id=brand.id, platform="facebook",
        account_name="User To Delete", account_id=user_id,
        access_token=encrypt_token("token"), is_connected=True, meta_info={}
    ))
    db.commit()

    _, app_secret = FacebookOAuthService.get_app_credentials(db)
    signed_request = _build_signed_request(
        {"user_id": user_id, "algorithm": "HMAC-SHA256", "issued_at": int(time.time())},
        app_secret
    )
    res = client.post("/api/v1/meta/data-deletion", data={"signed_request": signed_request})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["confirmation_code"].startswith("DEL_")
    assert "url" in data
    assert db.query(SocialAccount).filter(SocialAccount.account_id == user_id).first() is None


# ===========================================================================
# 12. Callback URL mismatch detection
# ===========================================================================

def test_callback_url_matches_meta_registration():
    uri = settings.META_REDIRECT_URI
    assert ":8000" not in uri, f"Still pointing to wrong port 8000: {uri}"
    assert "8001" in uri or "8002" in uri, f"Must use active port (8001/8002), got: {uri}"
    assert "/api/v1/social/facebook/callback" in uri, f"Wrong path: {uri}"
    assert "social-accounts" not in uri, f"Must not use /social-accounts/ path: {uri}"

