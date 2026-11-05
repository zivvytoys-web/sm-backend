import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.core.security import encrypt_token, decrypt_token, sanitize_credential_token
from app.models.social_account import SocialAccount
from app.models.platform_integration import PlatformIntegration
from app.models.workspace_integration import WorkspaceIntegration


class MetaError(Exception):
    """Base exception for Meta operations."""
    pass


class MetaAuthError(MetaError):
    """Raised when authentication fails, or token is revoked/expired and requires reauthorization."""
    pass


class MetaAPIError(MetaError):
    """Raised for unexpected Meta Graph API errors."""
    pass


# Per-account in-memory lock to prevent concurrent refresh races across async tasks
_account_locks: Dict[int, asyncio.Lock] = {}
_global_lock = asyncio.Lock()


async def _get_account_lock(account_id: int) -> asyncio.Lock:
    async with _global_lock:
        if account_id not in _account_locks:
            _account_locks[account_id] = asyncio.Lock()
        return _account_locks[account_id]


class MetaTokenManager:
    """
    Centralized, thread-safe, concurrency-safe token manager for Meta (Facebook & Instagram) OAuth.
    Handles:
    - Official token inspection via Meta's /debug_token endpoint.
    - Long-lived user token renewal via grant_type=fb_exchange_token (extends validity for another 60 days).
    - Page Access Token re-stamping from long-lived user tokens.
    - Instagram Business Account token management via linked Facebook Page or ig_refresh_token.
    - Error code mapping (e.g. Code 190, Subcode 463/467) with automatic requires_reauthorization flagging.
    - AES-256 encrypted persistence to MySQL.
    - Refresh token preservation (never overwrites valid stored user token with empty values).
    """

    DEFAULT_GRAPH_VERSION = "v21.0"
    DEFAULT_BUFFER_DAYS = 7  # Renew if expiring within 7 days

    @classmethod
    def get_graph_version(cls) -> str:
        return getattr(settings, "META_GRAPH_VERSION", cls.DEFAULT_GRAPH_VERSION)

    @classmethod
    def get_app_credentials(cls, db: Session, org_id: Optional[int] = None) -> Tuple[str, str]:
        """
        Resolves Meta App ID and App Secret in priority order:
        1. WorkspaceIntegration (active organization)
        2. PlatformIntegration (global admin settings)
        3. Environment variables (settings.META_APP_ID / settings.META_APP_SECRET)
        """
        app_id: Optional[str] = None
        app_secret: Optional[str] = None

        if org_id:
            ws = db.query(WorkspaceIntegration).filter(
                WorkspaceIntegration.organization_id == org_id,
                WorkspaceIntegration.provider.in_(["facebook", "instagram", "meta"]),
                WorkspaceIntegration.enabled == True
            ).first()
            if ws:
                app_id = ws.client_id
                if ws.client_secret_encrypted:
                    app_secret = decrypt_token(ws.client_secret_encrypted)

        if not app_id or not app_secret:
            pi = db.query(PlatformIntegration).filter(
                PlatformIntegration.platform.in_(["facebook", "instagram", "meta"])
            ).first()
            if pi:
                if not app_id and pi.client_id:
                    app_id = pi.client_id
                if not app_secret and pi.client_secret_encrypted:
                    app_secret = decrypt_token(pi.client_secret_encrypted)

        app_id = sanitize_credential_token(app_id or getattr(settings, "META_APP_ID", "") or getattr(settings, "FACEBOOK_CLIENT_ID", ""), "Meta App ID", allow_empty=True) or ""
        app_secret = sanitize_credential_token(app_secret or getattr(settings, "META_APP_SECRET", "") or getattr(settings, "FACEBOOK_CLIENT_SECRET", ""), "Meta App Secret", allow_empty=True) or ""

        return app_id, app_secret

    @classmethod
    def is_token_expired(cls, account: SocialAccount, buffer_days: int = DEFAULT_BUFFER_DAYS) -> bool:
        """
        Check if the token is expired or will expire within buffer_days.
        If requires_reauthorization flag is True, considered expired.
        If token_expires_at is None, we check if renewal is possible.
        """
        meta = account.meta_info or {}
        if meta.get("requires_reauthorization"):
            return True

        if not account.token_expires_at:
            # If no expiry date is set, check if we have a refresh_token (stored long-lived user token).
            # If so, consider it due for inspection/renewal to establish exact validity.
            return bool(account.refresh_token)

        cutoff = datetime.utcnow() + timedelta(days=buffer_days)
        return account.token_expires_at <= cutoff

    @classmethod
    async def debug_token(cls, db: Session, token: str, org_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Queries Meta's official /debug_token endpoint using App Access Token.
        Returns detailed token health including is_valid, expires_at, scopes, and error details.
        """
        app_id, app_secret = cls.get_app_credentials(db, org_id=org_id)
        if not app_id or not app_secret:
            logger.warning("MetaTokenManager: App ID or App Secret not configured; skipping debug_token.")
            return {"is_valid": True, "scopes": [], "error": None}

        version = cls.get_graph_version()
        debug_url = f"https://graph.facebook.com/{version}/debug_token"
        app_access_token = f"{app_id}|{app_secret}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(
                    debug_url,
                    params={
                        "input_token": token,
                        "access_token": app_access_token
                    }
                )

                if res.status_code == 200:
                    data = res.json().get("data", {})
                    return {
                        "is_valid": data.get("is_valid", False),
                        "expires_at": data.get("expires_at", 0),
                        "data_access_expires_at": data.get("data_access_expires_at", 0),
                        "scopes": data.get("scopes", []),
                        "type": data.get("type", "UNKNOWN"),
                        "error": data.get("error")
                    }
                else:
                    err_json = {}
                    try:
                        err_json = res.json()
                    except Exception:
                        pass
                    return {
                        "is_valid": False,
                        "scopes": [],
                        "error": err_json.get("error", {"message": res.text})
                    }
        except Exception as e:
            logger.warning(f"MetaTokenManager: Network error during debug_token: {e}")
            return {"is_valid": True, "scopes": [], "error": None, "network_error": str(e)}

    @classmethod
    async def renew_meta_tokens(
        cls,
        db: Session,
        account: SocialAccount,
        force: bool = False,
        buffer_days: int = DEFAULT_BUFFER_DAYS
    ) -> Dict[str, Any]:
        """
        Inspect and renew Meta credentials for Facebook Page or Instagram accounts.
        Thread-safe and concurrency-safe: uses an async lock per account.
        """
        lock = await _get_account_lock(account.id)
        async with lock:
            db.refresh(account)

            raw_access = decrypt_token(account.access_token) or ""
            raw_user_token = decrypt_token(account.refresh_token) or ""

            if not force and not cls.is_token_expired(account, buffer_days=buffer_days):
                if raw_access:
                    logger.info(
                        f"MetaTokenManager: Account #{account.id} ({account.account_name}) token is valid "
                        f"(expires at {account.token_expires_at} UTC). Skipping renewal."
                    )
                    return {
                        "access_token": raw_access,
                        "token_expires_at": account.token_expires_at,
                        "refreshed": False,
                        "status": "HEALTHY"
                    }

            org_id = account.brand_profile.organization_id if account.brand_profile else account.organization_id
            app_id, app_secret = cls.get_app_credentials(db, org_id=org_id)
            version = cls.get_graph_version()

            if not app_id or not app_secret:
                err_msg = "Meta App ID and App Secret are not configured in admin integrations or settings."
                logger.error(f"MetaTokenManager: {err_msg}")
                raise MetaAuthError(err_msg)

            # Step 1: Inspect current access token health
            debug_info = await cls.debug_token(db, raw_access, org_id=org_id)
            is_valid = debug_info.get("is_valid", False)
            debug_err = debug_info.get("error")

            # Check if standalone Instagram token refresh is supported (ig_refresh_token)
            plat_str = str(account.platform.value if hasattr(account.platform, "value") else account.platform).lower()
            auth_method = (account.meta_info or {}).get("auth_method", "")

            if plat_str == "instagram" and auth_method != "OAUTH_2_VIA_FACEBOOK_PAGE":
                refreshed_ig = await cls._refresh_standalone_instagram(db, account, raw_access)
                if refreshed_ig:
                    return refreshed_ig

            # Step 2: If the current page/access token is valid, verify renewal of the long-lived user token
            if is_valid:
                # If we have stored the long-lived user token in refresh_token, renew it via fb_exchange_token
                new_user_token = None
                new_page_token = None
                if raw_user_token:
                    try:
                        renew_res = await cls._exchange_fb_token(app_id, app_secret, raw_user_token, version)
                        if renew_res and "access_token" in renew_res:
                            new_user_token = renew_res["access_token"]
                            logger.info(f"MetaTokenManager: Successfully extended long-lived user token for account #{account.id}.")

                            # Query fresh Page token from renewed user token
                            target_page_id = account.account_id
                            if plat_str == "instagram":
                                target_page_id = (account.meta_info or {}).get("linked_facebook_page_id") or account.account_id

                            fresh_pt = await cls._fetch_page_access_token(new_user_token, str(target_page_id), version)
                            if fresh_pt:
                                new_page_token = fresh_pt
                                logger.info(f"MetaTokenManager: Re-stamped fresh Page token for account #{account.id}.")
                    except Exception as renew_err:
                        logger.warning(f"MetaTokenManager: Could not auto-extend user token for account #{account.id}: {renew_err}")

                # Update database record
                if new_page_token:
                    account.access_token = encrypt_token(new_page_token)
                if new_user_token:
                    account.refresh_token = encrypt_token(new_user_token)

                now_exp = datetime.utcnow() + timedelta(days=60)
                account.token_expires_at = now_exp
                account.is_connected = True

                meta = dict(account.meta_info or {})
                meta["requires_reauthorization"] = False
                meta["last_refresh_error"] = None
                meta["reauthorization_reason"] = None
                meta["last_refreshed_at"] = datetime.utcnow().isoformat()
                meta["verification_status"] = "HEALTHY"
                if debug_info.get("scopes"):
                    meta["scopes"] = debug_info["scopes"]
                account.meta_info = meta

                db.commit()
                db.refresh(account)

                return {
                    "access_token": new_page_token or raw_access,
                    "token_expires_at": now_exp,
                    "refreshed": bool(new_page_token or new_user_token),
                    "status": "HEALTHY",
                    "scopes": meta.get("scopes", [])
                }

            # Step 3: Current token is INVALID. Attempt automated recovery using stored user token.
            if raw_user_token:
                logger.info(f"MetaTokenManager: Account #{account.id} token invalid/expired. Attempting recovery via user token...")
                try:
                    renew_res = await cls._exchange_fb_token(app_id, app_secret, raw_user_token, version)
                    if renew_res and "access_token" in renew_res:
                        new_user_token = renew_res["access_token"]
                        target_page_id = account.account_id
                        if plat_str == "instagram":
                            target_page_id = (account.meta_info or {}).get("linked_facebook_page_id") or account.account_id

                        fresh_pt = await cls._fetch_page_access_token(new_user_token, str(target_page_id), version)
                        if fresh_pt:
                            account.access_token = encrypt_token(fresh_pt)
                            account.refresh_token = encrypt_token(new_user_token)
                            now_exp = datetime.utcnow() + timedelta(days=60)
                            account.token_expires_at = now_exp
                            account.is_connected = True

                            meta = dict(account.meta_info or {})
                            meta["requires_reauthorization"] = False
                            meta["last_refresh_error"] = None
                            meta["reauthorization_reason"] = None
                            meta["last_refreshed_at"] = datetime.utcnow().isoformat()
                            meta["verification_status"] = "HEALTHY"
                            account.meta_info = meta

                            db.commit()
                            db.refresh(account)

                            logger.info(f"MetaTokenManager: Recovery successful for account #{account.id}.")
                            return {
                                "access_token": fresh_pt,
                                "token_expires_at": now_exp,
                                "refreshed": True,
                                "status": "HEALTHY"
                            }
                except Exception as recovery_err:
                    logger.warning(f"MetaTokenManager: Automated recovery via user token failed for account #{account.id}: {recovery_err}")

            # Step 4: Token is permanently invalid or revoked. Mark requires_reauthorization.
            code = (debug_err or {}).get("code")
            subcode = (debug_err or {}).get("error_subcode")
            raw_msg = (debug_err or {}).get("message", "Token expired or revoked by user or security policy.")

            if code in (190, 102):
                if subcode == 463:
                    reason = "Your Facebook Page access token has expired. Please reconnect your account to resume automated publishing."
                elif subcode == 467:
                    reason = "Your Facebook access token was revoked or password was changed. Please reconnect your account."
                else:
                    reason = f"Your Facebook authorization has expired ({raw_msg}). Please reconnect your Facebook Page."
            else:
                reason = f"Meta authorization error ({code}): {raw_msg}. Reconnection required."

            cls._mark_reauthorization_required(db, account, reason)
            raise MetaAuthError(reason)

    @classmethod
    async def _exchange_fb_token(cls, app_id: str, app_secret: str, token: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Executes grant_type=fb_exchange_token against Meta Graph API.
        """
        url = f"https://graph.facebook.com/{version}/oauth/access_token"
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                url,
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "fb_exchange_token": token
                }
            )
            if res.status_code == 200:
                return res.json()
            return None

    @classmethod
    async def _fetch_page_access_token(cls, user_token: str, page_id: str, version: str) -> Optional[str]:
        """
        Fetches fresh Page Access Token from /{page_id}?fields=access_token using a valid user token.
        """
        url = f"https://graph.facebook.com/{version}/{page_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                url,
                params={
                    "fields": "access_token,name",
                    "access_token": user_token
                }
            )
            if res.status_code == 200:
                return res.json().get("access_token")
            return None

    @classmethod
    async def _refresh_standalone_instagram(cls, db: Session, account: SocialAccount, current_token: str) -> Optional[Dict[str, Any]]:
        """
        Refreshes long-lived Instagram Basic Display / User Token via ig_refresh_token.
        """
        if not current_token:
            return None

        url = "https://graph.instagram.com/refresh_access_token"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(
                    url,
                    params={
                        "grant_type": "ig_refresh_token",
                        "access_token": current_token
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    new_token = data.get("access_token")
                    expires_in = int(data.get("expires_in", 5184000))  # Default 60 days
                    new_exp = datetime.utcnow() + timedelta(seconds=expires_in)

                    account.access_token = encrypt_token(new_token)
                    account.token_expires_at = new_exp
                    account.is_connected = True

                    meta = dict(account.meta_info or {})
                    meta["requires_reauthorization"] = False
                    meta["last_refreshed_at"] = datetime.utcnow().isoformat()
                    meta["last_refresh_error"] = None
                    meta["verification_status"] = "HEALTHY"
                    account.meta_info = meta

                    db.commit()
                    db.refresh(account)

                    logger.info(f"MetaTokenManager: Successfully refreshed Instagram standalone token for account #{account.id}.")
                    return {
                        "access_token": new_token,
                        "token_expires_at": new_exp,
                        "refreshed": True,
                        "status": "HEALTHY"
                    }
        except Exception as e:
            logger.warning(f"MetaTokenManager: Standalone Instagram refresh failed: {e}")
        return None

    @classmethod
    def _mark_reauthorization_required(cls, db: Session, account: SocialAccount, reason: str):
        """
        Atomically mark account as disconnected and requiring reauthorization in MySQL.
        """
        try:
            account.is_connected = False
            meta = dict(account.meta_info or {})
            meta["requires_reauthorization"] = True
            meta["verification_status"] = "REQUIRES_REAUTHORIZATION"
            meta["reauthorization_reason"] = reason
            meta["last_refresh_error"] = reason
            meta["last_refreshed_at"] = datetime.utcnow().isoformat()
            account.meta_info = meta
            db.commit()
            db.refresh(account)
            logger.warning(f"MetaTokenManager: Account #{account.id} flagged REQUIRES_REAUTHORIZATION: {reason}")
        except Exception as e:
            logger.error(f"MetaTokenManager: Error updating reauthorization state in DB: {e}")
            db.rollback()

    @classmethod
    async def ensure_valid_token(
        cls,
        db: Session,
        account: SocialAccount,
        buffer_days: int = DEFAULT_BUFFER_DAYS
    ) -> str:
        """
        Returns a valid, decrypted access token.
        Auto-renews expired tokens or tokens within buffer window.
        Falls back to existing token if auto-renewal is transiently blocked,
        or raises MetaAuthError if reauthorization is required.
        """
        if cls.is_token_expired(account, buffer_days=buffer_days):
            logger.info(f"MetaTokenManager: Token for account #{account.id} is expired or expiring soon. Attempting renewal...")
            try:
                res = await cls.renew_meta_tokens(db, account, force=False, buffer_days=buffer_days)
                return res["access_token"]
            except Exception as e:
                logger.warning(
                    f"MetaTokenManager: Automatic renewal failed for account #{account.id}: {e}. "
                    "Checking if existing decrypted token can be used as fallback..."
                )
                existing = decrypt_token(account.access_token) or ""
                meta = account.meta_info or {}
                if existing and not meta.get("requires_reauthorization"):
                    return existing
                raise

        token = decrypt_token(account.access_token) or ""
        if not token:
            res = await cls.renew_meta_tokens(db, account, force=True)
            return res["access_token"]

        return token
