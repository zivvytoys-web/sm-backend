from app.integrations.social.youtube import YouTubeAPIError
from app.integrations.social.youtube import YouTubeAuthError
import os
import json
from app.core.logging import logger
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import encrypt_token, decrypt_token, sanitize_credential_token
from app.db.dependencies import get_db
from app.models.social_account import SocialAccount
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.schemas.social_account import SocialAccountCreate, SocialAccountUpdate, SocialAccountResponse
from app.integrations.social.instagram import InstagramClient
from app.integrations.social.facebook import FacebookClient
from app.integrations.social.youtube import YouTubeClient
from app.integrations.social.twitter import TwitterClient
from app.integrations.social.tiktok import TikTokClient
from app.integrations.social.linkedin import LinkedInClient
from app.integrations.social.pinterest import PinterestClient
from app.integrations.social.snapchat import SnapchatClient
from app.models.platform_integration import PlatformIntegration
from app.models.workspace_integration import WorkspaceIntegration
from app.core.permissions import get_current_active_user, require_creator
from app.utils.enums import PlatformType
# YouTubeAuthError/YouTubeAPIError needed for the credential connect handler
from app.services.youtube_token_manager import YouTubeAuthError, YouTubeAPIError

router = APIRouter(prefix="/social-accounts", tags=["Social Accounts"])

# Short-lived, single-use OAuth transactions.
# State tokens are cryptographically signed using HMAC-SHA256 and self-contained,
# ensuring zero failure across multiple server instances (e.g. Render / cloud horizontal scaling).
OAUTH_STATE_TTL = timedelta(hours=1)
_oauth_transactions: Dict[str, Dict[str, Any]] = {}
_OAUTH_CACHE_FILE = os.path.join(getattr(settings, "LOCAL_STORAGE_DIR", "./.media_tmp"), ".oauth_sessions.json")
_HMAC_SECRET = (getattr(settings, "SECRET_KEY", "social-ai-oauth-signing-secret") or "social-ai-oauth-signing-secret").encode("utf-8")


def _encode_signed_oauth_state(payload: Dict[str, Any]) -> str:
    """Creates a tamper-proof, URL-safe signed state token for OAuth flows."""
    import time
    token_dict = dict(payload)
    if "ts" not in token_dict:
        token_dict["ts"] = int(time.time())
    raw_json = json.dumps(token_dict, separators=(',', ':'))
    body = base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("utf-8").rstrip("=")
    sig = hmac.new(_HMAC_SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{body}.{sig}"


def _decode_signed_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """Validates and decodes signed state token or legacy base64 json."""
    import time
    if not state:
        return None
    # 1. New HMAC signed format: <base64_json>.<sig>
    if "." in state:
        parts = state.rsplit(".", 1)
        if len(parts) == 2:
            body, sig = parts
            expected_sig = hmac.new(_HMAC_SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
            if hmac.compare_digest(sig, expected_sig):
                try:
                    padded = body + "=" * ((4 - len(body) % 4) % 4)
                    data = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
                    # Allow 2-hour validity window
                    if time.time() - data.get("ts", 0) < 7200:
                        return data
                except Exception as err:
                    logger.debug(f"Failed decoding signed oauth payload: {err}")
    # 2. Legacy unsigned base64 json fallback
    try:
        clean_state = state.split(".")[0]
        padded = clean_state + "=" * ((4 - len(clean_state) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
        if isinstance(data, dict) and ("platform" in data or "uid" in data):
            return data
    except Exception:
        pass
    return None


def _store_oauth_transaction(state: str, transaction: Dict[str, Any]):
    _oauth_transactions[state] = transaction
    try:
        os.makedirs(os.path.dirname(_OAUTH_CACHE_FILE), exist_ok=True)
        data = {}
        if os.path.isfile(_OAUTH_CACHE_FILE):
            try:
                with open(_OAUTH_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        tx_copy = dict(transaction)
        if isinstance(tx_copy.get("expires_at"), datetime):
            tx_copy["expires_at"] = tx_copy["expires_at"].isoformat()
        data[state] = tx_copy
        now_iso = datetime.utcnow().isoformat()
        data = {k: v for k, v in data.items() if v.get("expires_at", "") > now_iso}
        with open(_OAUTH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Could not persist oauth transaction: {e}")


def _pop_oauth_transaction(state: str) -> Optional[Dict[str, Any]]:
    # 1. Check in-memory store
    tx = _oauth_transactions.pop(state or "", None)
    if tx:
        try:
            if os.path.isfile(_OAUTH_CACHE_FILE):
                with open(_OAUTH_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.pop(state, None)
                with open(_OAUTH_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f)
        except Exception:
            pass
        return tx

    # 2. Check persistent file store
    try:
        if os.path.isfile(_OAUTH_CACHE_FILE):
            with open(_OAUTH_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            tx_data = data.pop(state, None)
            with open(_OAUTH_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            if tx_data:
                if isinstance(tx_data.get("expires_at"), str):
                    tx_data["expires_at"] = datetime.fromisoformat(tx_data["expires_at"])
                return tx_data
    except Exception as e:
        logger.warning(f"Could not load oauth transaction from file: {e}")

    # 3. Stateless fallback: extract verified parameters directly from signed state token
    decoded = _decode_signed_oauth_state(state)
    if decoded:
        logger.info(f"[OAuth] Recovered stateless OAuth session for platform {decoded.get('platform')}")
        return {
            "uid": decoded.get("uid"),
            "org_id": decoded.get("org_id"),
            "brand_id": decoded.get("brand_id"),
            "platform": decoded.get("platform"),
            "redirect_uri": decoded.get("redirect_uri"),
            "code_verifier": decoded.get("cv"),
            "expires_at": datetime.utcnow() + timedelta(hours=1)
        }

    return None


class OAuthAuthorizeResponse(BaseModel):
    platform: str
    authorize_url: str
    client_id_configured: bool
    required_env_vars: List[str]
    scopes: List[str]


class OAuthCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    redirect_uri: Optional[str] = "http://localhost:3000/dashboard?tab=socials"
    brand_profile_id: Optional[int] = None


class ConnectCredentialsRequest(BaseModel):
    platform: PlatformType
    account_name: str
    account_id: Optional[str] = None
    profile_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    page_id: Optional[str] = None
    app_id: Optional[str] = None
    brand_profile_id: Optional[int] = None
    skip_live_verification: Optional[bool] = False
    meta_info: Optional[Dict[str, Any]] = None

    @field_validator('platform', mode='before')
    @classmethod
    def parse_platform(cls, v):
        if isinstance(v, str):
            return PlatformType(v.lower())
        return v

    @field_validator('access_token', 'refresh_token', 'api_key', 'api_secret', mode='before')
    @classmethod
    def sanitize_tokens(cls, v, info):
        if v is None:
            return None
        field_label = (info.field_name or "Token").replace("_", " ").title()
        return sanitize_credential_token(v, field_name=field_label, allow_empty=True)


class VerifyAccountResponse(BaseModel):
    account_id: int
    platform: str
    is_connected: bool
    status: str
    verified_at: str
    account_name: Optional[str] = None
    display_name: Optional[str] = None
    profile_url: Optional[str] = None
    error_message: Optional[str] = None
    scopes: List[str] = []


def normalize_platform_name(platform: Any) -> str:
    if hasattr(platform, "value"):
        platform = platform.value
    p = str(platform or "").strip().lower()
    if p in ("x", "twitter"):
        return "twitter"
    return p


def _get_platform_client(platform: Any, access_token: str, refresh_token: Optional[str] = None):
    token = decrypt_token(access_token) if access_token else ""
    ref_tok = decrypt_token(refresh_token) if refresh_token else None
    norm = normalize_platform_name(platform)
    try:
        p_enum = PlatformType(norm)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported platform: {platform}")

    if p_enum == PlatformType.INSTAGRAM:
        return InstagramClient(access_token=token)
    elif p_enum == PlatformType.FACEBOOK:
        return FacebookClient(access_token=token)
    elif p_enum == PlatformType.YOUTUBE:
        return YouTubeClient(access_token=token, refresh_token=ref_tok)
    elif p_enum == PlatformType.TWITTER:
        return TwitterClient(access_token=token, refresh_token=ref_tok)
    elif p_enum == PlatformType.SNAPCHAT:
        return SnapchatClient(access_token=token, refresh_token=ref_tok)
    elif p_enum == PlatformType.TIKTOK:
        return TikTokClient(access_token=token, refresh_token=ref_tok)
    elif p_enum == PlatformType.LINKEDIN:
        return LinkedInClient(access_token=token, refresh_token=ref_tok)
    elif p_enum == PlatformType.PINTEREST:
        return PinterestClient(access_token=token, refresh_token=ref_tok)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported platform: {platform}")


@router.get("/", response_model=List[SocialAccountResponse])
def list_social_accounts(
    brand_profile_id: Optional[int] = Query(None, description="Optional brand profile ID to scope accounts to specific enterprise"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all verified connected social media channels for current organization (optionally filtered by brand)."""
    user_brand_ids = [b.id for b in db.query(BrandProfile.id).filter(BrandProfile.organization_id == current_user.organization_id).all()]
    
    # Query accounts belonging to the organization or any brand of this organization
    from sqlalchemy import or_
    filters = [SocialAccount.organization_id == current_user.organization_id]
    if user_brand_ids:
        filters.append(SocialAccount.brand_profile_id.in_(user_brand_ids))

    accounts = db.query(SocialAccount).filter(or_(*filters)).all()

    # Deduplicate accounts by ID
    dedup = {a.id: a for a in accounts}
    all_accs = list(dedup.values())

    if brand_profile_id:
        brand_accs = [a for a in all_accs if a.brand_profile_id == brand_profile_id]
        if brand_accs:
            return brand_accs

    return all_accs


@router.get("/supported-platforms")
def get_supported_platforms():
    """Returns official configuration and publishing capabilities for supported social platforms."""
    return [
        {
            "id": "instagram",
            "name": "Instagram",
            "description": "Publish photos, carousels, and reels to Instagram Business accounts.",
            "auth_type": "OAUTH_2",
            "icon": "instagram",
            "supports_schedule": True,
            "supports_immediate": True,
            "supported_media": ["IMAGE", "VIDEO"]
        },
        {
            "id": "facebook",
            "name": "Facebook",
            "description": "Publish feed posts, photos, and video updates to Facebook Pages.",
            "auth_type": "OAUTH_2",
            "icon": "facebook",
            "supports_schedule": True,
            "supports_immediate": True,
            "supported_media": ["TEXT", "IMAGE", "VIDEO"]
        },
        {
            "id": "youtube",
            "name": "YouTube",
            "description": "Publish video content and shorts to YouTube channels.",
            "auth_type": "OAUTH_2",
            "icon": "youtube",
            "supports_schedule": True,
            "supports_immediate": True,
            "supported_media": ["VIDEO"]
        },
        {
            "id": "twitter",
            "name": "X (Twitter)",
            "description": "Publish tweets, images, and video updates to X.",
            "auth_type": "OAUTH_2",
            "icon": "twitter",
            "supports_schedule": True,
            "supports_immediate": True,
            "supported_media": ["TEXT", "IMAGE", "VIDEO"]
        },
        {
            "id": "snapchat",
            "name": "Snapchat",
            "description": "Publish creative content and campaigns to Snapchat Business accounts.",
            "auth_type": "OAUTH_2",
            "icon": "snapchat",
            "supports_schedule": True,
            "supports_immediate": True,
            "supported_media": ["IMAGE", "VIDEO"]
        }
    ]


@router.get("/brand/{brand_id}", response_model=List[SocialAccountResponse])
def get_brand_social_accounts(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List connected social media channels for a specific brand."""
    brand = db.query(BrandProfile).filter(
        BrandProfile.id == brand_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not brand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand profile not found or unauthorized.")

    return db.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand_id
    ).all()


@router.get("/oauth/{platform}/authorize", response_model=OAuthAuthorizeResponse)
def get_oauth_authorize_url(
    platform: PlatformType,
    redirect_uri: str = "http://localhost:3000/dashboard",
    brand_profile_id: Optional[int] = None,
    reauthenticate: bool = Query(True, description="Force re-authentication / account selection prompt"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate the official OAuth 2.0 authorization URL for a social platform with secure state."""
    plat = platform.value if hasattr(platform, "value") else str(platform).lower()
    plat = normalize_platform_name(plat)

    # Prefer credentials owned by the active workspace; global config remains
    # only as a compatibility fallback for legacy tenants.
    workspace_config = db.query(WorkspaceIntegration).filter(
        WorkspaceIntegration.organization_id == current_user.organization_id,
        WorkspaceIntegration.provider == plat,
        WorkspaceIntegration.enabled == True,
    ).first()
    db_config = db.query(PlatformIntegration).filter(PlatformIntegration.platform == plat).first()
    client_id = (workspace_config.client_id if workspace_config else None) or (db_config.client_id if db_config else None)
    scopes = (workspace_config.settings or {}).get("scopes", []) if workspace_config else []
    scopes = scopes or (db_config.scopes if (db_config and db_config.scopes) else [])
    auth_base = (workspace_config.settings or {}).get("auth_url") if workspace_config else None
    auth_base = auth_base or (db_config.auth_url if db_config else None)

    # Resolve effective redirect URI (strip query parameters for Google compliance)
    configured_redirect = (workspace_config.settings or {}).get("redirect_uri") if workspace_config else None
    configured_redirect = configured_redirect or (db_config.redirect_uri if db_config else None)
    effective_redirect = configured_redirect or redirect_uri
    if plat == "youtube":
        effective_redirect = effective_redirect.split("?")[0]

    state_data = {
        "platform": plat,
        "uid": current_user.id,
        "org_id": current_user.organization_id,
        "brand_id": brand_profile_id,
        "redirect_uri": effective_redirect,
        "nonce": secrets.token_urlsafe(16)
    }

    transaction = {
        "uid": current_user.id,
        "org_id": current_user.organization_id,
        "brand_id": brand_profile_id,
        "platform": plat,
        "redirect_uri": effective_redirect,
        "expires_at": datetime.utcnow() + OAUTH_STATE_TTL
    }
    if plat == "twitter":
        verifier = secrets.token_urlsafe(64)
        transaction["code_verifier"] = verifier
        state_data["cv"] = verifier
        transaction["code_challenge"] = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")

    state_payload = _encode_signed_oauth_state(state_data)
    _store_oauth_transaction(state_payload, transaction)

    if plat in ("instagram", "facebook"):
        from app.services.facebook_oauth_service import FacebookOAuthService
        try:
            fb_res = FacebookOAuthService.generate_authorization_url(
                user_id=current_user.id,
                organization_id=current_user.organization_id,
                brand_profile_id=brand_profile_id,
                db=db,
                reauthenticate=reauthenticate
            )
            return OAuthAuthorizeResponse(
                platform=plat,
                authorize_url=fb_res["authorization_url"],
                client_id_configured=True,
                required_env_vars=[],
                scopes=fb_res["scopes"]
            )
        except Exception as e:
            logger.warning(f"FacebookOAuthService url generation fallback: {e}")
            client_id = client_id or settings.META_APP_ID or settings.FACEBOOK_CLIENT_ID or settings.INSTAGRAM_CLIENT_ID
            scopes = scopes or ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "instagram_basic", "instagram_content_publish"]
            auth_type_val = "reauthenticate,rerequest" if reauthenticate else "rerequest"
            auth_url = (
                f"{auth_base or 'https://www.facebook.com/v19.0/dialog/oauth'}?"
                f"client_id={client_id or 'CONFIG_REQUIRED'}&"
                f"redirect_uri={effective_redirect}&"
                f"state={state_payload}&"
                f"scope={','.join(scopes)}&"
                f"auth_type={auth_type_val}&"
                f"response_type=code"
            )
            return OAuthAuthorizeResponse(
                platform=plat,
                authorize_url=auth_url,
                client_id_configured=bool(client_id),
                required_env_vars=["META_APP_ID", "META_APP_SECRET"] if not client_id else [],
                scopes=scopes
            )

    elif plat == "youtube":
        client_id = client_id or settings.YOUTUBE_CLIENT_ID
        scopes = scopes or [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/userinfo.profile"
        ]
        auth_url = (
            f"{auth_base or 'https://accounts.google.com/o/oauth2/v2/auth'}?"
            f"client_id={client_id or 'CONFIG_REQUIRED'}&"
            f"redirect_uri={effective_redirect}&"
            f"state={state_payload}&"
            f"response_type=code&"
            f"scope={'%20'.join(scopes)}&"
            # Force account chooser (select_account) and consent dialog to guarantee offline refresh token issuance
            f"access_type=offline&prompt=consent%20select_account&include_granted_scopes=true"
        )
        return OAuthAuthorizeResponse(
            platform="youtube",
            authorize_url=auth_url,
            client_id_configured=bool(client_id),
            required_env_vars=["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"] if not client_id else [],
            scopes=scopes
        )

    elif plat == "twitter":
        client_id = client_id or settings.TWITTER_CLIENT_ID
        scopes = scopes or ["tweet.read", "tweet.write", "users.read", "offline.access"]
        auth_url = (
            f"{auth_base or 'https://twitter.com/i/oauth2/authorize'}?"
            f"response_type=code&"
            f"client_id={client_id or 'CONFIG_REQUIRED'}&"
            f"redirect_uri={redirect_uri}&"
            f"state={state_payload}&"
            f"scope={'%20'.join(scopes)}&"
            f"code_challenge={transaction['code_challenge']}&code_challenge_method=S256"
        )
        return OAuthAuthorizeResponse(
            platform="twitter",
            authorize_url=auth_url,
            client_id_configured=bool(client_id),
            required_env_vars=["TWITTER_CLIENT_ID", "TWITTER_CLIENT_SECRET"] if not client_id else [],
            scopes=scopes
        )

    elif plat == "tiktok":
        client_id = client_id or getattr(settings, "TIKTOK_CLIENT_KEY", None)
        scopes = scopes or ["user.info.basic", "video.upload", "video.publish"]
        auth_url = (
            f"{auth_base or 'https://www.tiktok.com/v2/auth/authorize/'}?"
            f"client_key={client_id or 'CONFIG_REQUIRED'}&"
            f"scope={','.join(scopes)}&"
            f"response_type=code&"
            f"redirect_uri={redirect_uri}&"
            f"state={state_payload}"
        )
        return OAuthAuthorizeResponse(
            platform="tiktok",
            authorize_url=auth_url,
            client_id_configured=bool(client_id),
            required_env_vars=["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"] if not client_id else [],
            scopes=scopes
        )

    elif plat == "linkedin":
        client_id = client_id or getattr(settings, "LINKEDIN_CLIENT_ID", None)
        scopes = scopes or ["openid", "profile", "email", "w_member_social"]
        auth_url = (
            f"{auth_base or 'https://www.linkedin.com/oauth/v2/authorization'}?"
            f"response_type=code&"
            f"client_id={client_id or 'CONFIG_REQUIRED'}&"
            f"redirect_uri={redirect_uri}&"
            f"state={state_payload}&"
            f"scope={'%20'.join(scopes)}"
        )
        return OAuthAuthorizeResponse(
            platform="linkedin",
            authorize_url=auth_url,
            client_id_configured=bool(client_id),
            required_env_vars=["LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"] if not client_id else [],
            scopes=scopes
        )

    elif plat == "pinterest":
        client_id = client_id or getattr(settings, "PINTEREST_APP_ID", None)
        scopes = scopes or ["boards:read", "pins:read", "pins:write", "user_accounts:read"]
        auth_url = (
            f"{auth_base or 'https://www.pinterest.com/oauth/'}?"
            f"client_id={client_id or 'CONFIG_REQUIRED'}&"
            f"redirect_uri={redirect_uri}&"
            f"response_type=code&"
            f"scope={','.join(scopes)}&"
            f"state={state_payload}"
        )
        return OAuthAuthorizeResponse(
            platform="pinterest",
            authorize_url=auth_url,
            client_id_configured=bool(client_id),
            required_env_vars=["PINTEREST_APP_ID", "PINTEREST_APP_SECRET"] if not client_id else [],
            scopes=scopes
        )

    elif plat == "snapchat":
        client_id = client_id or getattr(settings, "SNAPCHAT_CLIENT_ID", None)
        scopes = scopes or ["snapchat-marketing-api", "snapchat-profile-api"]
        auth_url = (
            f"{auth_base or 'https://accounts.snapchat.com/login/oauth2/authorize'}?"
            f"client_id={client_id or 'CONFIG_REQUIRED'}&"
            f"redirect_uri={redirect_uri}&"
            f"response_type=code&"
            f"scope={'%20'.join(scopes)}&"
            f"state={state_payload}"
        )
        return OAuthAuthorizeResponse(
            platform="snapchat",
            authorize_url=auth_url,
            client_id_configured=bool(client_id),
            required_env_vars=["SNAPCHAT_CLIENT_ID", "SNAPCHAT_CLIENT_SECRET"] if not client_id else [],
            scopes=scopes
        )

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Platform '{platform}' is not supported.")


@router.post("/oauth/{platform}/callback", response_model=SocialAccountResponse)
async def handle_oauth_callback(
    platform: PlatformType,
    payload: OAuthCallbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Exchange authorization code for OAuth access token directly with platform provider,
    verify live channel details, encrypt token, and persist to MySQL.
    """
    plat = platform.value if hasattr(platform, "value") else str(platform).lower()
    transaction = _pop_oauth_transaction(payload.state or "")
    if not transaction:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth session expired or is invalid. Start the connection again.")

    # Graceful user / org matching with fallback for current authenticated session
    tx_uid = transaction.get("uid")
    tx_org = transaction.get("org_id")
    if tx_uid and tx_uid != current_user.id:
        logger.warning(f"[OAuth] User ID mismatch: transaction user {tx_uid} vs current user {current_user.id}")
    if tx_org and current_user.organization_id and tx_org != current_user.organization_id:
        logger.warning(f"[OAuth] Org ID mismatch: transaction org {tx_org} vs current org {current_user.organization_id}")

    # Auto-align platform if signed state belongs to another provider (e.g. YouTube vs Instagram)
    tx_plat = transaction.get("platform")
    if tx_plat and tx_plat != plat:
        logger.info(f"[OAuth] Adjusting callback platform from {plat} to state platform {tx_plat}")
        plat = tx_plat

    # Ensure transaction has required context and resolve effective redirect URI
    transaction["uid"] = current_user.id
    transaction["org_id"] = current_user.organization_id
    transaction["platform"] = plat
    effective_redirect_uri = transaction.get("redirect_uri") or payload.redirect_uri
    
    # Resolve brand profile
    brand_id = payload.brand_profile_id or transaction.get("brand_id")
    if not brand_id:
        default_brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()
        if not default_brand:
            default_brand = BrandProfile(
                organization_id=current_user.organization_id,
                name="Brand Workspace",
                industry="Fashion & Retail",
                tone_of_voice="Engaging",
                key_themes=[],
                hashtags=[],
                color_palette=[]
            )
            db.add(default_brand)
            db.flush()
        brand_id = default_brand.id

    access_token = ""
    refresh_token = ""
    token_expires_at = None
    account_info = {}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            # Retrieve credentials from PlatformIntegration in DB with settings fallback
            workspace_config = db.query(WorkspaceIntegration).filter(
                WorkspaceIntegration.organization_id == transaction["org_id"],
                WorkspaceIntegration.provider == plat,
                WorkspaceIntegration.enabled == True,
            ).first()
            db_plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == plat).first()
            db_client_id = (workspace_config.client_id if workspace_config else None) or (db_plat.client_id if db_plat else None)
            db_client_secret = decrypt_token(workspace_config.client_secret_encrypted) if workspace_config else None
            db_client_secret = db_client_secret or (decrypt_token(db_plat.client_secret_encrypted) if db_plat else None)

            if plat in ("instagram", "facebook"):
                client_id = db_client_id or settings.META_APP_ID or settings.INSTAGRAM_CLIENT_ID or settings.FACEBOOK_CLIENT_ID
                client_secret = db_client_secret or settings.META_APP_SECRET or settings.INSTAGRAM_CLIENT_SECRET or settings.FACEBOOK_CLIENT_SECRET
                if not client_id or not client_secret:
                    raise ValueError(f"Meta App ID / App Secret is not configured in admin integrations settings.")
                
                token_res = await client.get(
                    "https://graph.facebook.com/v19.0/oauth/access_token",
                    params={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": effective_redirect_uri,
                        "code": payload.code
                    }
                )
                if token_res.status_code != 200:
                    err_text = token_res.text
                    raise ValueError(f"Meta OAuth token exchange failed: {err_text}")
                
                token_data = token_res.json()
                access_token = token_data.get("access_token")
                if token_data.get("expires_in"):
                    token_expires_at = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))

                if plat == "instagram":
                    adapter = InstagramClient(access_token=access_token)
                    account_info = await adapter.verify_and_fetch_details()
                else:
                    adapter = FacebookClient(access_token=access_token)
                    account_info = await adapter.verify_and_fetch_details()

            elif plat == "youtube":
                client_id = db_client_id or settings.YOUTUBE_CLIENT_ID
                client_secret = db_client_secret or settings.YOUTUBE_CLIENT_SECRET
                if not client_id or not client_secret:
                    raise ValueError("Google / YouTube Client ID & Secret are not configured in admin integrations settings.")

                token_res = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": payload.code,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": effective_redirect_uri,
                        "grant_type": "authorization_code"
                    }
                )
                if token_res.status_code != 200:
                    raise ValueError(f"Google OAuth token exchange failed: {token_res.text}")

                token_data = token_res.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token", "")
                if token_data.get("expires_in"):
                    token_expires_at = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))

                # pyrefly: ignore[bad-instantiation]
                adapter = YouTubeClient(access_token=access_token, refresh_token=refresh_token)
                account_info = await adapter.verify_and_fetch_details()
                if token_data.get("scope"):
                    account_info["permissions"] = [s for s in token_data["scope"].split() if s]

            elif plat == "twitter":
                client_id = db_client_id or settings.TWITTER_CLIENT_ID
                client_secret = db_client_secret or settings.TWITTER_CLIENT_SECRET
                if not client_id:
                    raise ValueError("Twitter Client ID is not configured in admin integrations settings.")

                token_res = await client.post(
                    "https://api.twitter.com/2/oauth2/token",
                    data={
                        "code": payload.code,
                        "grant_type": "authorization_code",
                        "client_id": client_id,
                        "redirect_uri": effective_redirect_uri,
                        "code_verifier": transaction["code_verifier"]
                    }
                )
                if token_res.status_code != 200:
                    raise ValueError(f"Twitter OAuth token exchange failed: {token_res.text}")

                token_data = token_res.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token", "")

                adapter = TwitterClient(access_token=access_token)
                account_info = await adapter.verify_and_fetch_details()

            elif plat == "tiktok":
                client_id = db_client_id or getattr(settings, "TIKTOK_CLIENT_KEY", None)
                client_secret = db_client_secret or getattr(settings, "TIKTOK_CLIENT_SECRET", None)
                if not client_id or not client_secret:
                    raise ValueError("TikTok Client Key & Secret are not configured in admin settings.")

                token_res = await client.post(
                    "https://open.tiktokapis.com/v2/oauth/token/",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "client_key": client_id,
                        "client_secret": client_secret,
                        "code": payload.code,
                        "grant_type": "authorization_code",
                        "redirect_uri": effective_redirect_uri
                    }
                )
                if token_res.status_code != 200:
                    raise ValueError(f"TikTok OAuth token exchange failed: {token_res.text}")

                token_data = token_res.json().get("data", {})
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token", "")

                adapter = TikTokClient(access_token=access_token)
                account_info = await adapter.verify_and_fetch_details()

            elif plat == "linkedin":
                client_id = db_client_id or getattr(settings, "LINKEDIN_CLIENT_ID", None)
                client_secret = db_client_secret or getattr(settings, "LINKEDIN_CLIENT_SECRET", None)
                if not client_id or not client_secret:
                    raise ValueError("LinkedIn Client ID & Secret are not configured in admin settings.")

                token_res = await client.post(
                    "https://www.linkedin.com/oauth/v2/accessToken",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "authorization_code",
                        "code": payload.code,
                        "redirect_uri": payload.redirect_uri,
                        "client_id": client_id,
                        "client_secret": client_secret
                    }
                )
                if token_res.status_code != 200:
                    raise ValueError(f"LinkedIn OAuth token exchange failed: {token_res.text}")

                token_data = token_res.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token", "")

                adapter = LinkedInClient(access_token=access_token)
                account_info = await adapter.verify_and_fetch_details()

            elif plat == "pinterest":
                client_id = db_client_id or getattr(settings, "PINTEREST_APP_ID", None)
                client_secret = db_client_secret or getattr(settings, "PINTEREST_APP_SECRET", None)
                if not client_id or not client_secret:
                    raise ValueError("Pinterest App ID & Secret are not configured in admin settings.")

                token_res = await client.post(
                    "https://api.pinterest.com/v5/oauth/token",
                    auth=(client_id, client_secret),
                    data={
                        "grant_type": "authorization_code",
                        "code": payload.code,
                        "redirect_uri": effective_redirect_uri
                    }
                )
                if token_res.status_code != 200:
                    raise ValueError(f"Pinterest OAuth token exchange failed: {token_res.text}")

                token_data = token_res.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token", "")

                adapter = PinterestClient(access_token=access_token)
                account_info = await adapter.verify_and_fetch_details()

            elif plat == "snapchat":
                client_id = db_client_id or getattr(settings, "SNAPCHAT_CLIENT_ID", None)
                client_secret = db_client_secret or getattr(settings, "SNAPCHAT_CLIENT_SECRET", None)
                if not client_id or not client_secret:
                    raise ValueError("Snapchat Client ID & Secret are not configured in admin settings.")

                token_res = await client.post(
                    "https://accounts.snapchat.com/login/oauth2/access_token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "authorization_code",
                        "code": payload.code,
                        "redirect_uri": effective_redirect_uri
                    }
                )
                if token_res.status_code != 200:
                    raise ValueError(f"Snapchat OAuth token exchange failed: {token_res.text}")

                token_data = token_res.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token", "")

                adapter = SnapchatClient(access_token=access_token)
                account_info = await adapter.verify_and_fetch_details()

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Real-time OAuth connection failed: {str(e)}"
        )

    # Persist verified account with encrypted credentials
    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None

    meta = {
        "auth_method": "OAUTH_2",
        "last_verified_at": datetime.utcnow().isoformat(),
        "verification_status": "VERIFIED",
        "permissions": account_info.get("permissions", []),
        "scopes": account_info.get("permissions", []),
        "requires_reauthorization": False,
        "last_refresh_error": None,
        "connected_by": current_user.id
    }

    acc_id = account_info.get("account_id") or f"{plat}_{int(datetime.utcnow().timestamp())}"
    acc_name = account_info.get("account_name") or f"{plat}_account"

    existing = db.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand_id,
        SocialAccount.platform == plat,
        SocialAccount.account_id == acc_id
    ).first()

    if existing:
        existing.account_name = acc_name
        existing.profile_url = account_info.get("profile_url") or existing.profile_url
        existing.access_token = encrypted_access
        # Preserve existing valid refresh token if Google didn't return a new one
        if encrypted_refresh:
            existing.refresh_token = encrypted_refresh
        if token_expires_at:
            existing.token_expires_at = token_expires_at
        existing.is_connected = True
        existing.meta_info = meta
        db.commit()
        db.refresh(existing)
        try:
            from app.services.notification_service import NotificationService
            NotificationService.create_notification(
                db=db,
                user_id=current_user.id,
                organization_id=current_user.organization_id,
                title=f"{plat.title()} Connected",
                message=f"Successfully reconnected @{acc_name} to brand profile.",
                event_type="account_connected",
                severity="success",
                link="/dashboard/social-accounts",
                metadata={"platform": plat, "account_id": acc_id, "account_name": acc_name},
                dedup_key=f"account_connect_{plat}_{acc_id}_{int(datetime.utcnow().timestamp() // 300)}"
            )
        except Exception:
            pass
        return existing

    new_acc = SocialAccount(
        platform=plat,
        account_name=acc_name,
        account_id=acc_id,
        profile_url=account_info.get("profile_url"),
        access_token=encrypted_access,
        refresh_token=encrypted_refresh,
        token_expires_at=token_expires_at,
        is_connected=True,
        meta_info=meta,
        brand_profile_id=brand_id,
        organization_id=current_user.organization_id
    )
    db.add(new_acc)
    db.commit()
    db.refresh(new_acc)

    try:
        from app.services.notification_service import NotificationService
        NotificationService.create_notification(
            db=db,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            title=f"{plat.title()} Connected",
            message=f"Successfully connected @{acc_name} to brand profile.",
            event_type="account_connected",
            severity="success",
            link="/dashboard/social-accounts",
            metadata={"platform": plat, "account_id": acc_id, "account_name": acc_name},
            dedup_key=f"account_connect_{plat}_{acc_id}_{int(datetime.utcnow().timestamp() // 300)}"
        )
    except Exception:
        pass

    return new_acc


from fastapi.responses import RedirectResponse

@router.get("/oauth/{platform}/callback")
async def handle_oauth_browser_redirect(
    platform: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_reason: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Browser redirect endpoint for OAuth providers (Meta/Facebook/Instagram/Google).
    For Facebook/Meta, delegates to FacebookOAuthService callback for Page selection and long-lived token exchange.
    """
    if platform.lower() in ("facebook", "instagram") or (state and state.startswith("fb_state_")):
        from app.api.routes.facebook_oauth import facebook_oauth_callback
        return await facebook_oauth_callback(
            code=code,
            state=state,
            error=error,
            error_reason=error_reason,
            error_description=error_description,
            db=db
        )

    frontend_url = f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')}/dashboard"
    if error:
        return RedirectResponse(
            url=f"{frontend_url}?view=social-accounts&oauth_error={error}&error_description={error_description or ''}"
        )
    return RedirectResponse(
        url=f"{frontend_url}?view=social-accounts&oauth_success=true&platform={platform}&code={code}&state={state or ''}"
    )


@router.post("/connect-credentials", response_model=SocialAccountResponse, status_code=status.HTTP_201_CREATED)
async def connect_with_credentials(
    payload: ConnectCredentialsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Connect a social account using platform-specific API credentials.
    Performs live verification test against the provider API before saving as connected.
    Encrypts sensitive tokens at rest in MySQL.
    """
    brand_id = payload.brand_profile_id
    if not brand_id:
        default_brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()
        if not default_brand:
            default_brand = BrandProfile(
                organization_id=current_user.organization_id,
                name="Brand Workspace",
                industry="Retail & Apparel",
                tone_of_voice="Engaging",
                key_themes=[],
                hashtags=[],
                color_palette=[]
            )
            db.add(default_brand)
            db.flush()
        brand_id = default_brand.id
    else:
        brand = db.query(BrandProfile).filter(
            BrandProfile.id == brand_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
        if not brand:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brand profile does not belong to your organization.")

    try:
        raw_token = sanitize_credential_token(payload.access_token or payload.api_key or "", "Access token", allow_empty=True) or ""
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid credential format: {str(val_err)}")

    cleaned_refresh = None
    if payload.refresh_token:
        try:
            cleaned_refresh = sanitize_credential_token(payload.refresh_token, "Refresh token", allow_empty=True)
        except ValueError as val_err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid credential format: {str(val_err)}")

    plat_str = payload.platform.value if hasattr(payload.platform, "value") else str(payload.platform).lower()

    if not raw_token:
        if payload.platform == PlatformType.YOUTUBE and cleaned_refresh:
            db_p = db.query(PlatformIntegration).filter(PlatformIntegration.platform == "youtube").first()
            c_id = (db_p.client_id if db_p else None) or getattr(settings, "YOUTUBE_CLIENT_ID", None) or None
            c_sec = (decrypt_token(db_p.client_secret_encrypted) if (db_p and db_p.client_secret_encrypted) else None) or getattr(settings, "YOUTUBE_CLIENT_SECRET", None) or None
            if c_id and c_sec:
                try:
                    temp_yt = YouTubeClient(refresh_token=cleaned_refresh, client_id=c_id, client_secret=c_sec)
                    refreshed = await temp_yt.refresh_access_token()
                    raw_token = refreshed.get("access_token")
                except Exception as ref_err:
                    logger.warning(f"Failed to exchange YouTube refresh token: {ref_err}")
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid access token or refresh token is required to connect your social media channel."
            )

    # Execute Live API Verification
    verified_details = {}
    is_live_verified = False
    if not payload.skip_live_verification:
        try:
            if payload.platform == PlatformType.INSTAGRAM:
                client = InstagramClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details(account_id=payload.account_id)
                is_live_verified = True
            elif payload.platform == PlatformType.FACEBOOK:
                client = FacebookClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details(page_id=payload.page_id or payload.account_id)
                is_live_verified = True
            elif payload.platform == PlatformType.YOUTUBE:
                db_p = db.query(PlatformIntegration).filter(PlatformIntegration.platform == "youtube").first()
                c_id = (db_p.client_id if db_p else None) or getattr(settings, "YOUTUBE_CLIENT_ID", None) or None
                c_sec = (decrypt_token(db_p.client_secret_encrypted) if (db_p and db_p.client_secret_encrypted) else None) or getattr(settings, "YOUTUBE_CLIENT_SECRET", None) or None

                client = YouTubeClient(
                    access_token=raw_token,
                    refresh_token=cleaned_refresh,
                    client_id=c_id,
                    client_secret=c_sec
                )
                try:
                    verified_details = await client.verify_and_fetch_details()
                    is_live_verified = True
                except Exception as verify_err:
                    if cleaned_refresh and c_id and c_sec:
                        try:
                            refreshed = await client.refresh_access_token()
                            raw_token = refreshed.get("access_token")
                            client.access_token = raw_token
                            verified_details = await client.verify_and_fetch_details()
                            is_live_verified = True
                        except Exception:
                            raise verify_err
                    else:
                        raise verify_err
            elif payload.platform == PlatformType.TWITTER:
                client = TwitterClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details()
                is_live_verified = True
            elif payload.platform == PlatformType.TIKTOK:
                client = TikTokClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details()
                is_live_verified = True
            elif payload.platform == PlatformType.LINKEDIN:
                client = LinkedInClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details()
                is_live_verified = True
            elif payload.platform == PlatformType.PINTEREST:
                client = PinterestClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details()
                is_live_verified = True
            elif payload.platform == PlatformType.SNAPCHAT:
                client = SnapchatClient(access_token=raw_token)
                verified_details = await client.verify_and_fetch_details()
                is_live_verified = True
        except (YouTubeAuthError, YouTubeAPIError) as yt_err:
            if payload.platform == PlatformType.YOUTUBE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Verification failed with official YouTube API: {str(yt_err)}"
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Live provider verification failed for {plat_str}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Verification failed with official {payload.platform.value.capitalize()} API: {str(e)[:150]}"
            )

    account_id = verified_details.get("account_id") or payload.account_id
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not retrieve a valid account/channel ID from {payload.platform.value.capitalize()} API. Please verify your token permissions."
        )
    account_name = verified_details.get("account_name") or payload.account_name
    if not account_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Please provide an account name or handle for your {payload.platform.value.capitalize()} channel."
        )
    profile_url = verified_details.get("profile_url") or payload.profile_url
    
    meta_dict = payload.meta_info or {}
    meta_dict.update({
        "connected_by": current_user.id,
        "page_id": payload.page_id,
        "app_id": payload.app_id,
        "auth_method": "API_CREDENTIALS",
        "last_verified_at": datetime.utcnow().isoformat(),
        "verification_status": "VERIFIED" if is_live_verified else "CONNECTED",
        "permissions": verified_details.get("permissions", ["publish_posts", "read_insights"])
    })

    encrypted_token = encrypt_token(raw_token) if raw_token else ""
    encrypted_refresh = encrypt_token(cleaned_refresh) if cleaned_refresh else None

    # Check existing account
    existing = db.query(SocialAccount).filter(
        SocialAccount.brand_profile_id == brand_id,
        SocialAccount.platform == plat_str,
        (SocialAccount.account_name == account_name) | (SocialAccount.account_id == account_id)
    ).first()

    if existing:
        existing.account_name = account_name
        existing.account_id = account_id
        existing.profile_url = profile_url
        if encrypted_token:
            existing.access_token = encrypted_token
        if encrypted_refresh:
            existing.refresh_token = encrypted_refresh
        existing.is_connected = True
        existing.meta_info = meta_dict
        db.commit()
        db.refresh(existing)
        return existing

    account = SocialAccount(
        platform=plat_str,
        account_name=account_name,
        account_id=account_id,
        profile_url=profile_url,
        access_token=encrypted_token,
        refresh_token=encrypted_refresh,
        is_connected=True,
        meta_info=meta_dict,
        brand_profile_id=brand_id,
        organization_id=current_user.organization_id
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.post("/", response_model=SocialAccountResponse, status_code=status.HTTP_201_CREATED)
def connect_social_account(
    payload: SocialAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Legacy endpoint retained for compatibility; verified connection endpoints must be used."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Direct account creation is disabled. Connect with a verified access token or official OAuth."
    )
    brand_id = payload.brand_profile_id
    if not brand_id:
        default_brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()
        if not default_brand:
            default_brand = BrandProfile(
                organization_id=current_user.organization_id,
                name="Brand Workspace",
                industry="Retail & Apparel",
                tone_of_voice="Engaging",
                key_themes=[],
                hashtags=[],
                color_palette=[]
            )
            db.add(default_brand)
            db.flush()
        brand_id = default_brand.id
    else:
        brand = db.query(BrandProfile).filter(
            BrandProfile.id == brand_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
        if not brand:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brand profile does not belong to your organization.")

    plat_str = normalize_platform_name(payload.platform)
    meta = payload.meta_info or {}
    meta["last_verified_at"] = datetime.utcnow().isoformat()
    meta["verification_status"] = "VERIFIED"

    encrypted_token = encrypt_token(payload.access_token) if payload.access_token else ""
    encrypted_refresh = encrypt_token(payload.refresh_token) if payload.refresh_token else None

    account = SocialAccount(
        platform=plat_str,
        account_name=payload.account_name,
        account_id=payload.account_id or f"{plat_str}_{payload.account_name.replace('@', '').lower()}",
        profile_url=payload.profile_url or f"https://{plat_str}.com/{payload.account_name.replace('@', '')}",
        access_token=encrypted_token,
        refresh_token=encrypted_refresh,
        token_expires_at=payload.token_expires_at,
        is_connected=True,
        meta_info=meta,
        brand_profile_id=brand_id
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=SocialAccountResponse)
async def update_social_account(
    account_id: int,
    payload: SocialAccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Edit account handle, connection status, or API metadata with re-verification and encryption."""
    account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        SocialAccount.id == account_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    # Normalize stored platform to ensure clean matching
    account.platform = normalize_platform_name(account.platform)

    if payload.account_name is not None:
        account.account_name = payload.account_name
    if payload.profile_url is not None:
        account.profile_url = payload.profile_url
    if payload.is_connected is not None:
        account.is_connected = payload.is_connected
    if payload.access_token is not None and payload.access_token != "":
        # Re-verify and encrypt
        try:
            client = _get_platform_client(account.platform, payload.access_token)
            verified = await client.verify_and_fetch_details()
            account.access_token = encrypt_token(payload.access_token)
            account.is_connected = True
            current_meta = dict(account.meta_info or {})
            current_meta["last_verified_at"] = datetime.utcnow().isoformat()
            current_meta["verification_status"] = "VERIFIED"
            current_meta["permissions"] = verified.get("permissions", [])
            account.meta_info = current_meta
        except HTTPException as he:
            account.access_token = encrypt_token(payload.access_token)
            account.is_connected = False
            current_meta = dict(account.meta_info or {})
            current_meta["last_verified_at"] = datetime.utcnow().isoformat()
            current_meta["verification_status"] = "FAILED"
            current_meta["last_error"] = str(he.detail)
            account.meta_info = current_meta
            db.commit()
            raise HTTPException(status_code=he.status_code, detail=f"Token verification failed: {he.detail}")
        except Exception as e:
            account.access_token = encrypt_token(payload.access_token)
            account.is_connected = False
            current_meta = dict(account.meta_info or {})
            current_meta["last_verified_at"] = datetime.utcnow().isoformat()
            current_meta["verification_status"] = "FAILED"
            current_meta["last_error"] = str(e)
            account.meta_info = current_meta
            db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Token verification failed: {str(e)}")

    if payload.refresh_token is not None and payload.refresh_token.strip() != "":
        account.refresh_token = encrypt_token(payload.refresh_token.strip())
    if payload.meta_info is not None:
        current_meta = dict(account.meta_info or {})
        current_meta.update(payload.meta_info)
        account.meta_info = current_meta

    db.commit()
    db.refresh(account)
    return account



@router.post("/{account_id}/verify", response_model=VerifyAccountResponse)
async def verify_social_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Explicitly verify connection status and API permissions against live provider API.
    Updates last_verified_at and connection state in MySQL.
    """
    account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        SocialAccount.id == account_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    plat_str = account.platform.value if hasattr(account.platform, "value") else str(account.platform).lower()
    now_iso = datetime.utcnow().isoformat()

    from app.services.social_token_manager import SocialTokenManager, SocialAuthError
    try:
        raw_token = await SocialTokenManager.ensure_valid_token(db, account)
    except SocialAuthError as auth_err:
        account.is_connected = False
        meta = dict(account.meta_info or {})
        meta["last_verified_at"] = now_iso
        meta["verification_status"] = "REQUIRES_REAUTHORIZATION"
        meta["requires_reauthorization"] = True
        meta["last_error"] = str(auth_err)
        meta["reauthorization_reason"] = str(auth_err)
        account.meta_info = meta
        db.commit()
        return VerifyAccountResponse(
            account_id=account.id,
            platform=plat_str,
            is_connected=False,
            status="REQUIRES_REAUTHORIZATION",
            verified_at=now_iso,
            error_message=str(auth_err)
        )

    if not raw_token:
        account.is_connected = False
        meta = dict(account.meta_info or {})
        meta["last_verified_at"] = now_iso
        meta["verification_status"] = "NO_CREDENTIALS"
        account.meta_info = meta
        db.commit()
        return VerifyAccountResponse(
            account_id=account.id,
            platform=plat_str,
            is_connected=False,
            status="CONFIGURATION_REQUIRED",
            verified_at=now_iso,
            error_message="No API credentials configured for this account."
        )

    try:
        client = _get_platform_client(account.platform, raw_token, account.refresh_token)
        verified = await client.verify_and_fetch_details()
        
        # Persist updated access or refresh token if automatically refreshed
        if getattr(client, "access_token", None) and client.access_token != raw_token:
            account.access_token = encrypt_token(client.access_token)
        if getattr(client, "refresh_token", None):
            account.refresh_token = encrypt_token(client.refresh_token)

        meta = dict(account.meta_info or {})
        meta["last_verified_at"] = now_iso
        meta["verification_status"] = "VERIFIED"
        meta["permissions"] = verified.get("permissions", ["publish_posts", "read_insights"])
        account.meta_info = meta
        account.is_connected = True
        if verified.get("account_name"):
            account.account_name = verified["account_name"]
        if verified.get("profile_url"):
            account.profile_url = verified["profile_url"]
        
        db.commit()
        return VerifyAccountResponse(
            account_id=account.id,
            platform=plat_str,
            is_connected=True,
            status="ACTIVE_AND_VERIFIED",
            verified_at=now_iso,
            account_name=account.account_name,
            display_name=verified.get("display_name", account.account_name),
            profile_url=account.profile_url,
            scopes=verified.get("permissions", ["publish_posts", "read_insights"])
        )
    except Exception as e:
        err_str = str(e)
        is_quota = "quotaexceeded" in err_str.lower() or "exceeded your quota" in err_str.lower() or ("quota" in err_str.lower() and "403" in err_str)
        meta = dict(account.meta_info or {})
        meta["last_verified_at"] = now_iso
        meta["last_error"] = err_str
        if is_quota:
            meta["verification_status"] = "QUOTA_EXCEEDED"
            account.meta_info = meta
            account.is_connected = True
            db.commit()
            return VerifyAccountResponse(
                account_id=account.id,
                platform=plat_str,
                is_connected=True,
                status="QUOTA_EXCEEDED",
                verified_at=now_iso,
                account_name=account.account_name,
                display_name=account.account_name,
                profile_url=account.profile_url,
                error_message="Daily YouTube API quota limit reached (10,000 units/day). Token is valid and channel is connected, but YouTube requests are paused until Google resets quota at midnight Pacific Time."
            )
        else:
            account.is_connected = False
            meta["verification_status"] = "FAILED"
            account.meta_info = meta
            db.commit()
            return VerifyAccountResponse(
                account_id=account.id,
                platform=plat_str,
                is_connected=False,
                status="VERIFICATION_FAILED",
                verified_at=now_iso,
                error_message=err_str
            )


@router.post("/{account_id}/refresh", response_model=VerifyAccountResponse)
async def refresh_social_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Refresh OAuth access token using platform refresh token or exchange API.
    Updates encrypted token, expiration, and status.
    """
    account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        SocialAccount.id == account_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    plat_norm = normalize_platform_name(account.platform)
    account.platform = plat_norm
    now_iso = datetime.utcnow().isoformat()

    if plat_norm == "youtube":
        from app.services.youtube_token_manager import YouTubeTokenManager, YouTubeAuthError
        try:
            await YouTubeTokenManager.refresh_social_account_token(db=db, account=account, force=True)
            return await verify_social_account(account_id=account.id, db=db, current_user=current_user)
        except Exception as auth_err:
            raw_token = decrypt_token(account.access_token) or ""
            err_str = str(auth_err)

            if "invalid_client" in err_str:
                err_msg = (
                    "YouTube refresh token is saved. However, Google rejected the refresh request because the OAuth Client Secret "
                    "in system settings does not match this OAuth Client ID. "
                    "Please check your Google Cloud Console OAuth credentials."
                )
            else:
                err_msg = err_str

            meta = dict(account.meta_info or {})
            meta["last_error"] = err_msg
            meta["last_refreshed_at"] = now_iso
            account.meta_info = meta
            db.commit()

            if raw_token:
                try:
                    verify_res = await verify_social_account(account_id=account.id, db=db, current_user=current_user)
                    if verify_res.is_connected:
                        return verify_res
                except Exception:
                    pass

            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    raw_token = decrypt_token(account.access_token) or ""
    raw_refresh = decrypt_token(account.refresh_token) or ""

    try:
        client = _get_platform_client(plat_norm, raw_token)
        new_token_data = None
        if hasattr(client, "refresh_access_token"):
            if plat_norm == "twitter":
                db_plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == "twitter").first()
                c_id = (db_plat.client_id if db_plat else None) or getattr(settings, "TWITTER_CLIENT_ID", None)
                new_token_data = await client.refresh_access_token(refresh_token=raw_refresh, client_id=c_id)
            elif plat_norm == "snapchat":
                db_plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == "snapchat").first()
                c_id = (db_plat.client_id if db_plat else None) or getattr(settings, "SNAPCHAT_CLIENT_ID", None)
                c_sec = (decrypt_token(db_plat.client_secret_encrypted) if db_plat else None) or getattr(settings, "SNAPCHAT_CLIENT_SECRET", None)
                new_token_data = await client.refresh_access_token(refresh_token=raw_refresh, client_id=c_id, client_secret=c_sec)
            elif plat_norm in ("instagram", "facebook"):
                db_plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == plat_norm).first()
                if not db_plat:
                    alt_plat = "facebook" if plat_norm == "instagram" else "instagram"
                    db_plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == alt_plat).first()
                c_id = (db_plat.client_id if db_plat else None) or getattr(settings, f"{plat_norm.upper()}_CLIENT_ID", None) or getattr(settings, "META_APP_ID", None)
                c_sec = (decrypt_token(db_plat.client_secret_encrypted) if db_plat else None) or getattr(settings, f"{plat_norm.upper()}_CLIENT_SECRET", None) or getattr(settings, "META_APP_SECRET", None)
                new_token_data = await client.refresh_access_token(client_id=c_id, client_secret=c_sec)

        if new_token_data and new_token_data.get("access_token"):
            account.access_token = encrypt_token(new_token_data["access_token"])
            if new_token_data.get("refresh_token"):
                account.refresh_token = encrypt_token(new_token_data["refresh_token"])
            if new_token_data.get("expires_in"):
                account.token_expires_at = datetime.utcnow() + timedelta(seconds=int(new_token_data["expires_in"]))
            account.is_connected = True
            meta = dict(account.meta_info or {})
            meta["last_refreshed_at"] = now_iso
            meta["verification_status"] = "VERIFIED"
            account.meta_info = meta
            db.commit()

        return await verify_social_account(account_id=account.id, db=db, current_user=current_user)
    except Exception as e:
        meta = dict(account.meta_info or {})
        meta["last_error"] = str(e)
        meta["verification_status"] = "FAILED"
        account.meta_info = meta
        account.is_connected = False
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Token refresh failed: {str(e)}")


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_social_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Disconnect and revoke a social media channel."""
    account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        SocialAccount.id == account_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found or unauthorized.")
    
    plat_str = str(account.platform.value if hasattr(account.platform, 'value') else account.platform).title()
    acc_name = account.account_name
    db.delete(account)
    db.commit()

    try:
        from app.services.notification_service import NotificationService
        NotificationService.create_notification(
            db=db,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            title=f"{plat_str} Disconnected",
            message=f"Disconnected social channel @{acc_name}.",
            event_type="account_disconnected",
            severity="info",
            link="/dashboard/social-accounts",
            metadata={"account_id": account_id, "platform": plat_str},
            dedup_key=f"account_del_{account_id}"
        )
    except Exception:
        pass

    return None


# =========================================================================
# META (FACEBOOK / INSTAGRAM) WEBHOOK VERIFICATION & INGESTION
# =========================================================================

from fastapi.responses import PlainTextResponse, Response
from fastapi import Request


@router.get("/webhooks/meta", response_class=PlainTextResponse)
@router.get("/webhooks/facebook", response_class=PlainTextResponse)
@router.get("/webhooks/instagram", response_class=PlainTextResponse)
def meta_webhook_verification(
    request: Request,
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """
    Meta (Facebook & Instagram) Webhook Handshake Verification.
    Meta sends GET request with hub.mode=subscribe, hub.verify_token, and hub.challenge.
    """
    expected_token = getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "social_ai_meta_verify_token_2026_994757e6")
    
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        return PlainTextResponse(content=str(hub_challenge), status_code=200)
    
    return Response(content="Forbidden: Invalid verify token", status_code=403)


@router.post("/webhooks/meta")
@router.post("/webhooks/facebook")
@router.post("/webhooks/instagram")
async def meta_webhook_events(request: Request):
    """
    Meta Event Ingestion (Instagram & Facebook feed, mentions, and publishing status events).
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return {"status": "EVENT_RECEIVED", "data": payload}

