import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.core.security import encrypt_token, decrypt_token, sanitize_credential_token
from app.models.social_account import SocialAccount
from app.models.brand_profile import BrandProfile
from app.models.platform_integration import PlatformIntegration
from app.models.workspace_integration import WorkspaceIntegration


class YouTubeError(Exception):
    """Base exception for YouTube operations."""
    pass


class YouTubeAuthError(YouTubeError):
    """Raised when authentication fails or token is revoked/expired and requires reauthorization."""
    pass


class YouTubeQuotaError(YouTubeError):
    """Raised when YouTube API quota is exceeded (HTTP 403 quotaExceeded)."""
    pass


class YouTubeAPIError(YouTubeError):
    """Raised for unexpected YouTube API errors."""
    pass


# Per-account in-memory lock to prevent concurrent refresh races across async tasks
_account_locks: Dict[int, asyncio.Lock] = {}
_global_lock = asyncio.Lock()


async def _get_account_lock(account_id: int) -> asyncio.Lock:
    async with _global_lock:
        if account_id not in _account_locks:
            _account_locks[account_id] = asyncio.Lock()
        return _account_locks[account_id]


class YouTubeTokenManager:
    """
    Centralized, thread-safe, concurrency-safe token manager for YouTube OAuth 2.0.
    Handles:
    - Token expiration detection (with configurable buffer window)
    - Automatic token refreshing via Google's OAuth 2.0 token endpoint
    - Refresh token preservation when Google omits it in refresh response
    - Revocation / expired refresh token detection with automated connection state updates
    - AES-256 encrypted persistence to MySQL
    - Transient network retry with exponential backoff
    """

    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    DEFAULT_BUFFER_SECONDS = 300  # Refresh if expiring within 5 minutes

    @classmethod
    def is_token_expired(cls, account: SocialAccount, buffer_seconds: int = DEFAULT_BUFFER_SECONDS) -> bool:
        """
        Check if the account's access token is expired or will expire within buffer_seconds.
        """
        if not account.token_expires_at:
            # If no expiry date is set, check if a refresh token is available (either in DB or env).
            # If so, consider it expired so we refresh and establish an exact expiry.
            # If no refresh token is available, treat as non-expired to allow using the existing access token.
            return bool(account.refresh_token or getattr(settings, "YOUTUBE_REFRESH_TOKEN", None))
        cutoff = datetime.utcnow() + timedelta(seconds=buffer_seconds)
        return account.token_expires_at <= cutoff

    @classmethod
    def get_client_credentials(cls, db: Session, org_id: Optional[int] = None) -> Tuple[str, str]:
        """
        Resolve Google OAuth Client ID and Secret in priority order:
        1. WorkspaceIntegration (active organization)
        2. PlatformIntegration (global admin settings)
        3. Environment variables (settings.YOUTUBE_CLIENT_ID / settings.YOUTUBE_CLIENT_SECRET)
        """
        client_id: Optional[str] = None
        client_secret: Optional[str] = None

        if org_id:
            ws = db.query(WorkspaceIntegration).filter(
                WorkspaceIntegration.organization_id == org_id,
                WorkspaceIntegration.provider == "youtube",
                WorkspaceIntegration.enabled == True
            ).first()
            if ws:
                client_id = ws.client_id
                if ws.client_secret_encrypted:
                    client_secret = decrypt_token(ws.client_secret_encrypted)

        if not client_id or not client_secret:
            pi = db.query(PlatformIntegration).filter(
                PlatformIntegration.platform == "youtube"
            ).first()
            if pi:
                if not client_id and pi.client_id:
                    client_id = pi.client_id
                if not client_secret and pi.client_secret_encrypted:
                    client_secret = decrypt_token(pi.client_secret_encrypted)

        client_id = sanitize_credential_token(client_id or getattr(settings, "YOUTUBE_CLIENT_ID", ""), "YouTube client ID", allow_empty=True) or ""
        client_secret = sanitize_credential_token(client_secret or getattr(settings, "YOUTUBE_CLIENT_SECRET", ""), "YouTube client secret", allow_empty=True) or ""

        return client_id, client_secret

    @classmethod
    async def refresh_social_account_token(
        cls,
        db: Session,
        account: SocialAccount,
        force: bool = False,
        buffer_seconds: int = DEFAULT_BUFFER_SECONDS
    ) -> Dict[str, Any]:
        """
        Refresh the Google OAuth access token for a given SocialAccount.
        Thread-safe and concurrency-safe: uses an async lock per account and re-checks
        the database state before making an outbound request to Google.
        """
        lock = await _get_account_lock(account.id)
        async with lock:
            # Re-fetch the account row to check if another worker just refreshed it
            db.refresh(account)

            if not force and not cls.is_token_expired(account, buffer_seconds=buffer_seconds):
                raw_token = decrypt_token(account.access_token) or ""
                if raw_token:
                    logger.info(
                        f"YouTubeTokenManager: Account #{account.id} token is still valid "
                        f"(expires at {account.token_expires_at} UTC). Skipping refresh."
                    )
                    return {
                        "access_token": raw_token,
                        "token_expires_at": account.token_expires_at,
                        "refreshed": False
                    }

            # Retrieve and decrypt the refresh token
            if not account.refresh_token:
                err_msg = "No refresh token stored for this YouTube account. Reauthorization is required."
                cls._mark_reauthorization_required(db, account, err_msg)
                raise YouTubeAuthError(err_msg)

            decrypted_refresh = decrypt_token(account.refresh_token) or account.refresh_token
            try:
                raw_refresh = sanitize_credential_token(decrypted_refresh, "YouTube refresh token", allow_empty=False)
            except ValueError as e:
                err_msg = f"Refresh token is malformed: {str(e)}"
                cls._mark_reauthorization_required(db, account, err_msg)
                raise YouTubeAuthError(err_msg)

            if not raw_refresh:
                err_msg = "Decrypted refresh token is empty. Reauthorization is required."
                cls._mark_reauthorization_required(db, account, err_msg)
                raise YouTubeAuthError(err_msg)

            # Resolve client credentials
            org_id = None
            if account.brand_profile and account.brand_profile.organization_id:
                org_id = account.brand_profile.organization_id
            client_id, client_secret = cls.get_client_credentials(db, org_id=org_id)

            if not client_id or not client_secret:
                err_msg = "Google OAuth client credentials (client_id / client_secret) are not configured."
                logger.error(f"YouTubeTokenManager: {err_msg}")
                raise YouTubeAuthError(err_msg)

            # Request fresh access token from Google with transient error retry
            try:
                token_data = await cls._execute_google_token_refresh(
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=raw_refresh,
                    db=db,
                    account=account
                )
            except Exception as refresh_err:
                # If Google token refresh failed (e.g. client secret mismatch or rate limit),
                # inspect if the current access token is still valid via Google's tokeninfo endpoint.
                existing_raw = decrypt_token(account.access_token) or ""
                if existing_raw:
                    live_info = await cls._check_google_tokeninfo(existing_raw)
                    if live_info and int(live_info.get("expires_in", 0)) > 60:
                        exp_secs = int(live_info["expires_in"])
                        new_exp = datetime.utcnow() + timedelta(seconds=exp_secs)
                        account.token_expires_at = new_exp
                        account.is_connected = True
                        meta = dict(account.meta_info or {})
                        meta["requires_reauthorization"] = False
                        meta["last_refresh_error"] = None
                        meta["last_refreshed_at"] = datetime.utcnow().isoformat()
                        meta["verification_status"] = "VERIFIED"
                        account.meta_info = meta
                        db.commit()
                        logger.info(
                            f"YouTubeTokenManager: Existing access token verified via tokeninfo for account #{account.id}. "
                            f"Valid for {exp_secs}s until {new_exp.isoformat()} UTC."
                        )
                        return {
                            "access_token": existing_raw,
                            "token_expires_at": new_exp,
                            "refreshed": False,
                            "scopes": live_info.get("scope", "").split()
                        }
                raise refresh_err

            # Extract fields from Google's response
            new_access_token = token_data.get("access_token")
            if not new_access_token:
                err_msg = f"Google token response missing 'access_token': {token_data}"
                cls._mark_reauthorization_required(db, account, err_msg)
                raise YouTubeAuthError(err_msg)

            expires_in = int(token_data.get("expires_in", 3600))
            new_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

            # CRITICAL REQUIREMENT: Google may omit refresh_token on refresh grants.
            # Preserve existing refresh token unless a new valid replacement is explicitly returned.
            new_refresh_token = token_data.get("refresh_token")
            if new_refresh_token and str(new_refresh_token).strip():
                logger.info(f"YouTubeTokenManager: Google returned a new refresh token for account #{account.id}.")
                account.refresh_token = encrypt_token(str(new_refresh_token).strip())

            account.access_token = encrypt_token(new_access_token)
            account.token_expires_at = new_expires_at
            account.is_connected = True

            # Update meta info
            meta = dict(account.meta_info or {})
            meta["requires_reauthorization"] = False
            meta["last_refresh_error"] = None
            meta["last_refreshed_at"] = datetime.utcnow().isoformat()
            meta["verification_status"] = "VERIFIED"
            if "scope" in token_data:
                meta["scopes"] = [s for s in token_data["scope"].split() if s]
            account.meta_info = meta

            db.commit()
            db.refresh(account)

            logger.info(
                f"YouTubeTokenManager: Successfully refreshed access token for account #{account.id} "
                f"({account.account_name}). Valid until {new_expires_at.isoformat()} UTC."
            )

            return {
                "access_token": new_access_token,
                "token_expires_at": new_expires_at,
                "refreshed": True,
                "scopes": meta.get("scopes", [])
            }

    @classmethod
    async def _execute_google_token_refresh(
        cls,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        db: Session,
        account: SocialAccount,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Execute POST request to https://oauth2.googleapis.com/token.
        Retries transient network/5xx errors with exponential backoff.
        Immediately catches and flags permanent auth errors (400 invalid_grant).
        """
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }

        last_network_exc = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as http_client:
                    res = await http_client.post(cls.GOOGLE_TOKEN_URL, data=payload)

                if res.status_code == 200:
                    return res.json()

                # Handle HTTP 400/401 Bad Request (OAuth errors: invalid_grant, invalid_client, unauthorized_client)
                if res.status_code in (400, 401):
                    err_json = {}
                    try:
                        err_json = res.json()
                    except Exception:
                        pass
                    err_type = err_json.get("error", "")
                    err_desc = err_json.get("error_description", res.text[:200])

                    if err_type in ("invalid_grant", "unauthorized_client") or "revoked" in err_desc.lower() or "expired" in err_desc.lower():
                        err_msg = (
                            f"YouTube authorization has expired or was revoked ({err_type}: {err_desc}). "
                            f"Please reconnect your YouTube channel."
                        )
                        logger.warning(f"YouTubeTokenManager: {err_msg} for account #{account.id}")
                        cls._mark_reauthorization_required(db, account, err_msg)
                        raise YouTubeAuthError(err_msg)

                    if err_type == "invalid_client":
                        err_msg = (
                            f"Google OAuth client authentication failed ({err_type}: {err_desc}). "
                            "The OAuth Client Secret does not match the Client ID. "
                            "Please check your Google Cloud Console OAuth credentials."
                        )
                        logger.error(f"YouTubeTokenManager: {err_msg}")
                        raise YouTubeAuthError(err_msg)

                    err_msg = f"Google OAuth refresh rejected (HTTP {res.status_code}): {err_type} - {err_desc}"
                    logger.error(f"YouTubeTokenManager: {err_msg}")
                    cls._mark_reauthorization_required(db, account, err_msg)
                    raise YouTubeAuthError(err_msg)

                # 5xx Server errors from Google -> eligible for retry
                if res.status_code >= 500 and attempt < max_retries:
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        f"YouTubeTokenManager: Transient Google HTTP {res.status_code}. "
                        f"Retrying in {backoff}s (attempt {attempt}/{max_retries})..."
                    )
                    await asyncio.sleep(backoff)
                    continue

                raise YouTubeAPIError(f"Google token endpoint returned HTTP {res.status_code}: {res.text[:200]}")

            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as net_err:
                last_network_exc = net_err
                if attempt < max_retries:
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        f"YouTubeTokenManager: Network error during token refresh: {net_err}. "
                        f"Retrying in {backoff}s (attempt {attempt}/{max_retries})..."
                    )
                    await asyncio.sleep(backoff)
                else:
                    break

        err_msg = f"Failed to refresh YouTube access token due to persistent network errors: {last_network_exc}"
        logger.error(f"YouTubeTokenManager: {err_msg}")
        raise YouTubeAPIError(err_msg)

    @classmethod
    async def _check_google_tokeninfo(cls, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Query https://oauth2.googleapis.com/tokeninfo to check if an access token is live.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={access_token}")
                if res.status_code == 200:
                    data = res.json()
                    if "expires_in" in data and int(data.get("expires_in", 0)) > 0:
                        return data
        except Exception as e:
            logger.warning(f"YouTubeTokenManager: Error querying tokeninfo: {e}")
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
            meta["last_refresh_error"] = reason
            meta["last_refreshed_at"] = datetime.utcnow().isoformat()
            account.meta_info = meta
            db.commit()
            db.refresh(account)
        except Exception as e:
            logger.error(f"YouTubeTokenManager: Error updating reauthorization state in DB: {e}")
            db.rollback()

    @classmethod
    async def ensure_valid_token(
        cls,
        db: Session,
        account: SocialAccount,
        buffer_seconds: int = DEFAULT_BUFFER_SECONDS
    ) -> str:
        """
        Convenience method to ensure a valid access token is returned.
        If the stored token is expired or expiring within buffer_seconds, refreshes it automatically.
        Otherwise decrypts and returns the existing valid token.
        If refresh fails but an existing access token is stored, falls back to the existing token
        so publishing is not aborted prematurely.
        """
        if cls.is_token_expired(account, buffer_seconds=buffer_seconds):
            logger.info(
                f"YouTubeTokenManager: Token for account #{account.id} is expired or expiring soon. "
                "Attempting automatic refresh..."
            )
            try:
                res = await cls.refresh_social_account_token(db, account, force=False, buffer_seconds=buffer_seconds)
                return res["access_token"]
            except Exception as e:
                logger.warning(
                    f"YouTubeTokenManager: Automatic refresh failed for account #{account.id}: {e}. "
                    "Checking if existing decrypted access_token can be used as fallback..."
                )
                existing_token = decrypt_token(account.access_token) or ""
                if existing_token:
                    logger.info(f"YouTubeTokenManager: Using existing access token for account #{account.id} as fallback.")
                    return existing_token
                raise

        token = decrypt_token(account.access_token) or ""
        if not token:
            logger.info(f"YouTubeTokenManager: Account #{account.id} has empty access token. Refreshing...")
            res = await cls.refresh_social_account_token(db, account, force=False, buffer_seconds=buffer_seconds)
            return res["access_token"]

        return token
