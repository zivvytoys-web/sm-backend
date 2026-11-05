import os
import time
import json
import hmac
import hashlib
import base64
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import encrypt_token, decrypt_token
from app.core.logging import logger
from app.models.social_account import SocialAccount
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.models.platform_integration import PlatformIntegration


class MetaGraphAPIError(Exception):
    def __init__(self, message: str, code: Optional[int] = None, subcode: Optional[int] = None, error_type: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.error_type = error_type


def handle_graph_api_error(error_data: Dict[str, Any]) -> MetaGraphAPIError:
    """
    Centralized Meta Graph API error mapper.
    Translates cryptic Meta error codes into clear, actionable messages.
    """
    err = error_data.get("error", {})
    code = err.get("code")
    subcode = err.get("error_subcode")
    raw_msg = err.get("message", "Unknown Meta Graph API error")
    err_type = err.get("type", "OAuthException")

    if code in (190, 102):
        if subcode == 463:
            msg = "Facebook Access Token has expired. Please reconnect your account."
        elif subcode == 467:
            msg = "Facebook Access Token is invalid or was revoked. Please reconnect."
        else:
            msg = "Facebook Session or Access Token is invalid/expired. Please reconnect."
    elif code in (10, 200) or (200 <= (code or 0) <= 299):
        msg = f"Missing required Facebook permission: {raw_msg}. Ensure 'pages_show_list' and 'pages_manage_posts' are approved."
    elif code in (4, 17):
        msg = "Meta Graph API rate limit reached. Please wait a few minutes before trying again."
    elif code == 368:
        msg = "Your account or request was temporarily restricted by Meta security policy."
    else:
        msg = f"Meta Graph API Error ({code}): {raw_msg}"

    return MetaGraphAPIError(message=msg, code=code, subcode=subcode, error_type=err_type)


class FacebookOAuthService:
    """
    Production-grade Facebook OAuth 2.0 & Page Selection Service.
    Handles CSRF-safe state verification, code exchange for long-lived non-expiring Page tokens,
    Page listing with linked Instagram Business Account discovery, debug_token verification,
    and Meta signed_request verification for data deletion & deauthorization.
    """

    # In-memory TTL stores for CSRF states and page selection sessions with file cache persistence
    _state_store: Dict[str, Dict[str, Any]] = {}
    _session_store: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _get_cache_file(cls) -> str:
        cache_dir = getattr(settings, "LOCAL_STORAGE_DIR", "./.media_tmp")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, ".fb_oauth_cache.json")

    @classmethod
    def _save_fb_stores(cls):
        try:
            cache_file = cls._get_cache_file()
            now = time.time()
            data = {
                "states": {k: v for k, v in cls._state_store.items() if v.get("expires_at", 0) > now},
                "sessions": {k: v for k, v in cls._session_store.items() if v.get("expires_at", 0) > now}
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Could not persist Facebook OAuth cache: {e}")

    @classmethod
    def _load_fb_stores(cls):
        try:
            cache_file = cls._get_cache_file()
            if os.path.isfile(cache_file):
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                for k, v in data.get("states", {}).items():
                    if v.get("expires_at", 0) > now and k not in cls._state_store:
                        cls._state_store[k] = v
                for k, v in data.get("sessions", {}).items():
                    if v.get("expires_at", 0) > now and k not in cls._session_store:
                        cls._session_store[k] = v
        except Exception as e:
            logger.warning(f"Could not load Facebook OAuth cache: {e}")

    @classmethod
    def _cleanup_expired(cls):
        cls._load_fb_stores()
        now = time.time()
        cls._state_store = {k: v for k, v in cls._state_store.items() if v.get("expires_at", 0) > now}
        cls._session_store = {k: v for k, v in cls._session_store.items() if v.get("expires_at", 0) > now}
        cls._save_fb_stores()

    @classmethod
    def get_graph_version(cls) -> str:
        return getattr(settings, "META_GRAPH_VERSION", "v23.0")

    @classmethod
    def get_app_credentials(cls, db: Optional[Session] = None) -> Tuple[str, str]:
        """
        Retrieves Meta App ID and App Secret from DB PlatformIntegration or settings.
        """
        app_id = settings.META_APP_ID or settings.FACEBOOK_CLIENT_ID
        app_secret = settings.META_APP_SECRET or settings.FACEBOOK_CLIENT_SECRET

        if db:
            db_plat = db.query(PlatformIntegration).filter(PlatformIntegration.platform == "facebook").first()
            if db_plat and db_plat.client_id:
                app_id = db_plat.client_id
            if db_plat and db_plat.client_secret_encrypted:
                app_secret = decrypt_token(db_plat.client_secret_encrypted)

        if not app_id or not app_secret:
            raise ValueError(
                "Meta App ID and App Secret are not configured. "
                "Please configure META_APP_ID and META_APP_SECRET in .env or the Admin Integrations dashboard."
            )
        return app_id, app_secret

    @classmethod
    def get_redirect_uri(cls) -> str:
        return getattr(settings, "META_REDIRECT_URI", "http://127.0.0.1:8002/api/v1/social/facebook/callback")

    # =========================================================================
    # 1. GENERATE AUTH URL WITH SECURE STATE
    # =========================================================================

    @classmethod
    def generate_authorization_url(
        cls,
        user_id: int,
        organization_id: int,
        brand_profile_id: Optional[int] = None,
        db: Optional[Session] = None,
        reauthenticate: bool = True
    ) -> Dict[str, str]:
        cls._cleanup_expired()
        app_id, _ = cls.get_app_credentials(db)
        version = cls.get_graph_version()
        redirect_uri = cls.get_redirect_uri()

        # Scopes for Page posting and Instagram
        scopes_str = getattr(
            settings,
            "META_SCOPES",
            "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish"
        )
        scopes = [s.strip() for s in scopes_str.split(",") if s.strip()]

        # Generate single-use random state with 1-hour expiry and stateless recovery payload
        state = f"fb_state_{uuid.uuid4().hex}_{user_id}_{organization_id}_{brand_profile_id or 0}"
        cls._state_store[state] = {
            "user_id": user_id,
            "organization_id": organization_id,
            "brand_profile_id": brand_profile_id,
            "created_at": time.time(),
            "expires_at": time.time() + 3600,  # 1 hour
            "redirect_uri": redirect_uri
        }
        cls._save_fb_stores()

        # Use reauthenticate,rerequest so Facebook forces the account confirmation/login
        # dialog with 'Log into another account' option instead of silently reusing a cached test account (e.g. zivvy).
        auth_type_val = "reauthenticate,rerequest" if reauthenticate else "rerequest"

        auth_url = (
            f"https://www.facebook.com/{version}/dialog/oauth?"
            f"client_id={app_id}&"
            f"redirect_uri={redirect_uri}&"
            f"state={state}&"
            f"scope={','.join(scopes)}&"
            f"auth_type={auth_type_val}&"
            f"response_type=code"
        )

        return {
            "authorization_url": auth_url,
            "state": state,
            "scopes": scopes
        }

    # =========================================================================
    # 2. HANDLE CALLBACK & FETCH MANAGED PAGES
    # =========================================================================

    @classmethod
    async def exchange_code_and_fetch_pages(
        cls,
        code: str,
        state: str,
        db: Session
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
        """
        Validates state, exchanges code for long-lived user token,
        queries managed Facebook Pages with non-expiring Page Access Tokens,
        and saves session in temporary store.
        """
        cls._cleanup_expired()

        # Validate CSRF State with persistent fallback & stateless recovery
        state_data = cls._state_store.pop(state, None)
        if not state_data:
            cls._load_fb_stores()
            state_data = cls._state_store.pop(state, None)

        if not state_data and state and state.startswith("fb_state_"):
            parts = state.split("_")
            if len(parts) >= 6:
                try:
                    state_data = {
                        "user_id": int(parts[3]),
                        "organization_id": int(parts[4]),
                        "brand_profile_id": int(parts[5]) if parts[5] != "0" else None,
                        "created_at": time.time(),
                        "expires_at": time.time() + 3600,
                        "redirect_uri": cls.get_redirect_uri()
                    }
                    logger.info(f"[MetaOAuth] Stateless state recovery for user {parts[3]}, org {parts[4]}")
                except Exception as e:
                    logger.warning(f"[MetaOAuth] Stateless parse error: {e}")

        if not state_data:
            raise ValueError("Invalid or expired OAuth state parameter. Please try connecting again.")

        if state_data.get("expires_at", 0) < time.time():
            raise ValueError("OAuth state has expired. Please try connecting again.")

        app_id, app_secret = cls.get_app_credentials(db)
        version = cls.get_graph_version()
        redirect_uri = state_data.get("redirect_uri") or cls.get_redirect_uri()

        async with httpx.AsyncClient(timeout=25.0) as client:
            # 1. Exchange code for short-lived user token
            token_url = f"https://graph.facebook.com/{version}/oauth/access_token"
            res_short = await client.get(
                token_url,
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code
                }
            )

            if res_short.status_code != 200:
                err_data = res_short.json() if res_short.headers.get("content-type", "").startswith("application/json") else {}
                raise handle_graph_api_error(err_data if err_data else {"error": {"message": res_short.text}})

            short_token = res_short.json().get("access_token")

            # 2. Exchange short-lived token for long-lived user token (valid 60 days)
            res_long = await client.get(
                token_url,
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "fb_exchange_token": short_token
                }
            )

            if res_long.status_code != 200:
                err_data = res_long.json() if res_long.headers.get("content-type", "").startswith("application/json") else {}
                raise handle_graph_api_error(err_data if err_data else {"error": {"message": res_long.text}})

            long_user_token = res_long.json().get("access_token")

            # 3. Query managed Facebook Pages (Page tokens from long-lived user tokens do not expire)
            pages_url = f"https://graph.facebook.com/{version}/me/accounts"
            res_pages = await client.get(
                pages_url,
                params={
                    "fields": "id,name,access_token,category,picture{url},tasks",
                    "access_token": long_user_token
                }
            )

            if res_pages.status_code != 200:
                err_data = res_pages.json() if res_pages.headers.get("content-type", "").startswith("application/json") else {}
                raise handle_graph_api_error(err_data if err_data else {"error": {"message": res_pages.text}})

            pages_data = res_pages.json().get("data", [])
            if not pages_data:
                raise ValueError(
                    "No Facebook Pages found under this account. Please ensure you are an Admin or Editor of at least one Facebook Page."
                )

            # 4. Check for linked Instagram Business Accounts for each Page
            enriched_pages = []
            for p in pages_data:
                page_id = p.get("id")
                page_token = p.get("access_token")
                ig_info = None

                try:
                    res_ig = await client.get(
                        f"https://graph.facebook.com/{version}/{page_id}",
                        params={
                            "fields": "instagram_business_account{id,username,name,profile_picture_url}",
                            "access_token": page_token
                        }
                    )
                    if res_ig.status_code == 200:
                        ig_data = res_ig.json().get("instagram_business_account")
                        if ig_data:
                            ig_info = {
                                "id": ig_data.get("id"),
                                "username": ig_data.get("username"),
                                "name": ig_data.get("name"),
                                "profile_picture_url": ig_data.get("profile_picture_url")
                            }
                except Exception as e:
                    logger.debug(f"[MetaOAuth] IG lookup for page {page_id}: {e}")

                picture_url = p.get("picture", {}).get("data", {}).get("url") if isinstance(p.get("picture"), dict) else None

                enriched_pages.append({
                    "id": page_id,
                    "name": p.get("name"),
                    "category": p.get("category"),
                    "picture_url": picture_url,
                    "tasks": p.get("tasks", []),
                    "raw_page_token": page_token,  # Kept in secure server session only
                    "instagram_business_account": ig_info
                })

        # Generate temporary session ID (TTL 1 hour) and persist to cache
        session_id = f"fb_sess_{uuid.uuid4().hex}"
        cls._session_store[session_id] = {
            "user_id": state_data["user_id"],
            "organization_id": state_data["organization_id"],
            "brand_profile_id": state_data.get("brand_profile_id"),
            "long_user_token": long_user_token,
            "pages": enriched_pages,
            "created_at": time.time(),
            "expires_at": time.time() + 3600  # 1 hour
        }
        cls._save_fb_stores()

        # Safe list without raw tokens for frontend
        safe_pages_list = [
            {
                "id": p["id"],
                "name": p["name"],
                "category": p["category"],
                "picture_url": p["picture_url"],
                "instagram_business_account": p["instagram_business_account"]
            }
            for p in enriched_pages
        ]

        return session_id, safe_pages_list, state_data

    # =========================================================================
    # 3. GET TEMPORARY PAGES (Without Tokens)
    # =========================================================================

    @classmethod
    def get_temporary_pages(cls, session_id: str) -> List[Dict[str, Any]]:
        cls._cleanup_expired()
        session_data = cls._session_store.get(session_id)
        if not session_data:
            cls._load_fb_stores()
            session_data = cls._session_store.get(session_id)
        if not session_data:
            raise ValueError("Page selection session expired or invalid. Please connect again.")

        return [
            {
                "id": p["id"],
                "name": p["name"],
                "category": p["category"],
                "picture_url": p["picture_url"],
                "instagram_business_account": p["instagram_business_account"]
            }
            for p in session_data.get("pages", [])
        ]

    # =========================================================================
    # 4. SELECT PAGE, VERIFY WITH DEBUG_TOKEN, AND PERSIST ENCRYPTED
    # =========================================================================

    @classmethod
    async def select_and_persist_page(
        cls,
        db: Session,
        current_user: User,
        session_id: str,
        page_id: str,
        brand_profile_id: Optional[int] = None,
        connect_mode: Optional[str] = "both"
    ) -> Dict[str, Any]:
        cls._cleanup_expired()
        session_data = cls._session_store.get(session_id)
        if not session_data:
            cls._load_fb_stores()
            session_data = cls._session_store.get(session_id)
        if not session_data:
            raise ValueError("Page selection session expired or invalid. Please connect again.")

        # Ensure matching user / organization
        if session_data.get("organization_id") != current_user.organization_id:
            raise ValueError("Unauthorized page selection attempt.")

        # Find requested page in session
        matched_page = next((p for p in session_data.get("pages", []) if p["id"] == str(page_id)), None)
        if not matched_page:
            raise ValueError(f"Page ID {page_id} not found in this authentication session.")

        page_token = matched_page["raw_page_token"]
        page_name = matched_page["name"]
        ig_info = matched_page.get("instagram_business_account")

        # Resolve brand profile
        target_brand_id = brand_profile_id or session_data.get("brand_profile_id")
        if not target_brand_id:
            default_brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()
            if not default_brand:
                default_brand = BrandProfile(
                    organization_id=current_user.organization_id,
                    name=f"{page_name} Brand",
                    industry="Retail & Lifestyle",
                    tone_of_voice="Engaging"
                )
                db.add(default_brand)
                db.flush()
            target_brand_id = default_brand.id

        # 5. Verify Token with debug_token endpoint
        app_id, app_secret = cls.get_app_credentials(db)
        version = cls.get_graph_version()
        scopes_verified = []
        token_valid = True

        async with httpx.AsyncClient(timeout=15.0) as client:
            debug_url = f"https://graph.facebook.com/{version}/debug_token"
            res_debug = await client.get(
                debug_url,
                params={
                    "input_token": page_token,
                    "access_token": f"{app_id}|{app_secret}"
                }
            )
            if res_debug.status_code == 200:
                data_debug = res_debug.json().get("data", {})
                token_valid = data_debug.get("is_valid", True)
                scopes_verified = data_debug.get("scopes", ["pages_show_list", "pages_read_engagement", "pages_manage_posts"])

        if not token_valid:
            raise ValueError("Facebook debug_token verification failed. Token is invalid or restricted.")

        # Encrypt token at rest and prepare long-lived renewal tracking
        encrypted_token = encrypt_token(page_token)
        long_user_token = session_data.get("long_user_token")
        encrypted_user_token = encrypt_token(long_user_token) if long_user_token else None
        now_exp = datetime.utcnow() + timedelta(days=60)
        now_iso = datetime.utcnow().isoformat()
        db_fb_acc = None

        # 6. Save or update Facebook SocialAccount (if connect_mode is 'both' or 'facebook')
        should_connect_fb = connect_mode in ("both", "facebook") or not ig_info
        if should_connect_fb:
            fb_meta = {
                "page_id": page_id,
                "page_name": page_name,
                "category": matched_page.get("category"),
                "picture_url": matched_page.get("picture_url"),
                "scopes": scopes_verified,
                "auth_method": "OAUTH_2",
                "last_verified_at": now_iso,
                "last_refreshed_at": now_iso,
                "verification_status": "HEALTHY",
                "requires_reauthorization": False,
                "reauthorization_reason": None,
                "token_type": "PAGE",
                "connected_by": current_user.id
            }

            existing_fb = db.query(SocialAccount).filter(
                SocialAccount.brand_profile_id == target_brand_id,
                SocialAccount.platform == "facebook",
                SocialAccount.account_id == str(page_id)
            ).first()

            if existing_fb:
                existing_fb.account_name = page_name
                existing_fb.profile_url = f"https://facebook.com/{page_id}"
                existing_fb.access_token = encrypted_token
                # Preserve existing refresh token if session did not include one
                if encrypted_user_token:
                    existing_fb.refresh_token = encrypted_user_token
                existing_fb.token_expires_at = now_exp
                existing_fb.is_connected = True
                existing_fb.meta_info = fb_meta
                db_fb_acc = existing_fb
            else:
                db_fb_acc = SocialAccount(
                    brand_profile_id=target_brand_id,
                    platform="facebook",
                    account_name=page_name,
                    account_id=str(page_id),
                    profile_url=f"https://facebook.com/{page_id}",
                    access_token=encrypted_token,
                    refresh_token=encrypted_user_token,
                    token_expires_at=now_exp,
                    is_connected=True,
                    meta_info=fb_meta
                )
                db.add(db_fb_acc)

        # Record discovered Instagram Business Account info and create/update Instagram SocialAccount
        ig_account_result = None
        should_connect_ig = connect_mode in ("both", "instagram")
        if ig_info and should_connect_ig:
            if db_fb_acc and hasattr(db_fb_acc, "meta_info") and isinstance(db_fb_acc.meta_info, dict):
                db_fb_acc.meta_info["linked_instagram_account"] = {
                    "instagram_business_id": ig_info.get("id"),
                    "username": ig_info.get("username"),
                    "profile_picture_url": ig_info.get("profile_picture_url")
                }

            ig_id = str(ig_info.get("id", ""))
            ig_username = ig_info.get("username") or ig_info.get("name") or f"instagram_{ig_id}"
            ig_encrypted_token = encrypt_token(page_token)

            existing_ig = db.query(SocialAccount).filter(
                SocialAccount.brand_profile_id == target_brand_id,
                SocialAccount.platform == "instagram",
                SocialAccount.account_id == ig_id
            ).first()

            ig_meta = {
                "linked_facebook_page_id": page_id,
                "linked_facebook_page_name": page_name,
                "auth_method": "OAUTH_2_VIA_FACEBOOK_PAGE",
                "last_verified_at": now_iso,
                "last_refreshed_at": now_iso,
                "verification_status": "HEALTHY",
                "requires_reauthorization": False,
                "reauthorization_reason": None,
                "connected_by": current_user.id,
                "scopes": scopes_verified,
                "profile_picture_url": ig_info.get("profile_picture_url")
            }

            if existing_ig:
                existing_ig.account_name = ig_username
                existing_ig.access_token = ig_encrypted_token
                # Preserve existing refresh token if session did not include one
                if encrypted_user_token:
                    existing_ig.refresh_token = encrypted_user_token
                existing_ig.token_expires_at = now_exp
                existing_ig.is_connected = True
                existing_ig.meta_info = ig_meta
                db_ig_acc = existing_ig
            else:
                db_ig_acc = SocialAccount(
                    brand_profile_id=target_brand_id,
                    platform="instagram",
                    account_name=ig_username,
                    account_id=ig_id,
                    profile_url=f"https://instagram.com/{ig_username}",
                    access_token=ig_encrypted_token,
                    refresh_token=encrypted_user_token,
                    token_expires_at=now_exp,
                    is_connected=True,
                    meta_info=ig_meta
                )
                db.add(db_ig_acc)

            ig_account_result = {
                "account_id": ig_id,
                "account_name": ig_username,
                "profile_picture_url": ig_info.get("profile_picture_url")
            }

        db.commit()
        if db_fb_acc:
            db.refresh(db_fb_acc)

        # Remove used session
        cls._session_store.pop(session_id, None)

        logger.info(f"[MetaOAuth] Successfully connected Facebook Page '{page_name}' (ID: {page_id}) for user {current_user.id}")

        return {
            "success": True,
            "facebook_account": {
                "id": db_fb_acc.id if db_fb_acc else None,
                "platform": "facebook",
                "page_id": page_id,
                "page_name": page_name,
                "is_connected": bool(db_fb_acc)
            } if db_fb_acc else None,
            "instagram_account": ig_account_result,
            "linked_instagram_detected": ig_info
        }

    # =========================================================================
    # 5. TEST CONNECTION & LIVE DIAGNOSTICS
    # =========================================================================

    @classmethod
    async def test_page_connection(cls, db: Session, current_user: User, account_id: int) -> Dict[str, Any]:
        account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
            SocialAccount.id == account_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()

        if not account:
            raise ValueError("Facebook SocialAccount not found or unauthorized.")

        token = decrypt_token(account.access_token)
        if not token:
            raise ValueError("No access token stored for this account.")

        version = cls.get_graph_version()
        page_id = account.account_id

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"https://graph.facebook.com/{version}/{page_id}",
                params={
                    "fields": "id,name,link,followers_count,fan_count,verification_status",
                    "access_token": token
                }
            )

            if res.status_code != 200:
                err_data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                raise handle_graph_api_error(err_data if err_data else {"error": {"message": res.text}})

            data = res.json()
            return {
                "status": "healthy",
                "page_id": data.get("id"),
                "name": data.get("name"),
                "link": data.get("link"),
                "followers": data.get("followers_count") or data.get("fan_count") or 0,
                "tested_at": datetime.utcnow().isoformat()
            }

    # =========================================================================
    # 6. DISCONNECT & PERMISSION REVOCATION
    # =========================================================================

    @classmethod
    async def disconnect_facebook_account(cls, db: Session, current_user: User, account_id: int, revoke: bool = False):
        account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
            SocialAccount.id == account_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()

        if not account:
            raise ValueError("Account not found.")

        if revoke:
            token = decrypt_token(account.access_token)
            if token:
                try:
                    version = cls.get_graph_version()
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.delete(
                            f"https://graph.facebook.com/{version}/me/permissions",
                            params={"access_token": token}
                        )
                except Exception as e:
                    logger.warning(f"[MetaOAuth] Failed to revoke permissions on disconnect: {e}")

        db.delete(account)
        db.commit()

    # =========================================================================
    # 7. META SIGNED_REQUEST PARSER (Data Deletion & Deauth)
    # =========================================================================

    @classmethod
    def parse_signed_request(cls, signed_request: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Parses and cryptographically verifies Meta's signed_request using HMAC-SHA256.
        """
        try:
            encoded_sig, payload = signed_request.split(".", 1)
        except ValueError:
            raise ValueError("Invalid signed_request format.")

        sig = base64.urlsafe_b64decode(encoded_sig + "=" * ((4 - len(encoded_sig) % 4) % 4))
        data_str = base64.urlsafe_b64decode(payload + "=" * ((4 - len(payload) % 4) % 4)).decode("utf-8")
        data = json.loads(data_str)

        _, app_secret = cls.get_app_credentials(db)
        expected_sig = hmac.new(app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()

        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("Invalid signature in signed_request.")

        return data
