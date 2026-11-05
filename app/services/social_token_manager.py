import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.youtube_token_manager import YouTubeTokenManager, YouTubeAuthError, YouTubeAPIError
from app.services.meta_token_manager import MetaTokenManager, MetaAuthError, MetaAPIError


class SocialAuthError(Exception):
    """Raised when an account cannot be authenticated or auto-renewed, requiring user reconnection."""
    pass


class SocialAPIError(Exception):
    """Raised when a social platform API returns a transient or operational error."""
    pass


class SocialTokenManager:
    """
    Unified manager for social media token renewal, health diagnostics, and pre-publish token validation.
    Seamlessly dispatches to YouTubeTokenManager or MetaTokenManager based on platform type,
    ensuring one-time user authorization with automatic backend token renewal.
    """

    @classmethod
    async def ensure_valid_token(cls, db: Session, account: SocialAccount) -> str:
        """
        Validates the stored access token and automatically refreshes it on the backend
        if it is expired or expiring soon.
        Raises SocialAuthError if automatic renewal fails and user reconnection is required.
        """
        plat = str(account.platform.value if hasattr(account.platform, "value") else account.platform).lower()

        if plat == "youtube":
            try:
                return await YouTubeTokenManager.ensure_valid_token(db, account)
            except YouTubeAuthError as e:
                raise SocialAuthError(str(e))
            except Exception as e:
                logger.error(f"SocialTokenManager: YouTube token error for account #{account.id}: {e}")
                raise SocialAuthError(f"YouTube authentication failed: {str(e)}")

        elif plat in ("facebook", "instagram"):
            try:
                return await MetaTokenManager.ensure_valid_token(db, account)
            except MetaAuthError as e:
                raise SocialAuthError(str(e))
            except Exception as e:
                logger.error(f"SocialTokenManager: Meta token error for account #{account.id}: {e}")
                raise SocialAuthError(f"{plat.capitalize()} authentication failed: {str(e)}")

        else:
            token = decrypt_token(account.access_token) or account.access_token or ""
            if not token:
                raise SocialAuthError(f"No access token configured for {account.account_name} ({plat}).")
            return token

    @classmethod
    async def verify_and_refresh_account(
        cls,
        db: Session,
        account: SocialAccount,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Actively checks live token status against the provider API, renews expired tokens,
        and returns updated health status.
        """
        plat = str(account.platform.value if hasattr(account.platform, "value") else account.platform).lower()
        now_iso = datetime.utcnow().isoformat()

        if plat == "youtube":
            try:
                res = await YouTubeTokenManager.refresh_social_account_token(db, account, force=force)
                return {
                    "account_id": account.id,
                    "platform": "youtube",
                    "account_name": account.account_name,
                    "is_connected": True,
                    "status": "HEALTHY",
                    "refreshed": res.get("refreshed", False),
                    "token_expires_at": res.get("token_expires_at"),
                    "scopes": res.get("scopes", []),
                    "verified_at": now_iso,
                    "message": "YouTube channel token is valid and auto-renewing automatically."
                }
            except YouTubeAuthError as e:
                return {
                    "account_id": account.id,
                    "platform": "youtube",
                    "account_name": account.account_name,
                    "is_connected": False,
                    "status": "REQUIRES_REAUTHORIZATION",
                    "error_message": str(e),
                    "verified_at": now_iso
                }

        elif plat in ("facebook", "instagram"):
            try:
                res = await MetaTokenManager.renew_meta_tokens(db, account, force=force)
                return {
                    "account_id": account.id,
                    "platform": plat,
                    "account_name": account.account_name,
                    "is_connected": True,
                    "status": "HEALTHY",
                    "refreshed": res.get("refreshed", False),
                    "token_expires_at": res.get("token_expires_at"),
                    "scopes": res.get("scopes", []),
                    "verified_at": now_iso,
                    "message": f"{plat.capitalize()} account credentials verified and active."
                }
            except MetaAuthError as e:
                return {
                    "account_id": account.id,
                    "platform": plat,
                    "account_name": account.account_name,
                    "is_connected": False,
                    "status": "REQUIRES_REAUTHORIZATION",
                    "error_message": str(e),
                    "verified_at": now_iso
                }

        else:
            token = decrypt_token(account.access_token) or account.access_token or ""
            is_valid = bool(token)
            return {
                "account_id": account.id,
                "platform": plat,
                "account_name": account.account_name,
                "is_connected": is_valid,
                "status": "HEALTHY" if is_valid else "NO_CREDENTIALS",
                "verified_at": now_iso
            }

    @classmethod
    def get_health_status(cls, account: SocialAccount) -> Dict[str, Any]:
        """
        Determines current health and renewal eligibility for a given SocialAccount.
        """
        plat = str(account.platform.value if hasattr(account.platform, "value") else account.platform).lower()
        meta = account.meta_info or {}

        if meta.get("requires_reauthorization") or not account.is_connected:
            return {
                "status": "REQUIRES_REAUTHORIZATION",
                "label": "Needs Reconnection",
                "requires_reconnection": True,
                "can_auto_renew": False,
                "reason": meta.get("reauthorization_reason") or meta.get("last_refresh_error") or "Authentication expired or was revoked."
            }

        now = datetime.utcnow()
        if account.token_expires_at and account.token_expires_at < now:
            # Check if auto-renewal is possible
            has_renewal_mechanism = bool(account.refresh_token) or plat in ("facebook", "instagram")
            if has_renewal_mechanism:
                return {
                    "status": "RENEWAL_PENDING",
                    "label": "Auto-Renewing",
                    "requires_reconnection": False,
                    "can_auto_renew": True,
                    "reason": "Token has expired and will be automatically renewed before next publish."
                }
            return {
                "status": "EXPIRED",
                "label": "Token Expired",
                "requires_reconnection": True,
                "can_auto_renew": False,
                "reason": "Token has expired and no refresh token is stored. Reconnection required."
            }

        # Account is connected and valid
        has_auto_renew = bool(account.refresh_token) or plat in ("facebook", "instagram")
        return {
            "status": "HEALTHY",
            "label": "Auto-Renewing" if has_auto_renew else "Connected",
            "requires_reconnection": False,
            "can_auto_renew": has_auto_renew,
            "token_expires_at": account.token_expires_at.isoformat() if account.token_expires_at else None
        }
